"""Stepped MuJoCo world. No object teleporting or attachment constraints."""
from pathlib import Path
import threading
import uuid
import json
import math
import numpy as np
import mujoco
from PIL import Image

MODEL = Path(__file__).resolve().parent / "assets" / "tabletop.xml"


class Simulation:
    def __init__(self, seed=0):
        self.lock = threading.RLock()
        self.model = mujoco.MjModel.from_xml_path(str(MODEL))
        self.data = mujoco.MjData(self.model)
        self.seed = seed
        self.session = uuid.uuid4().hex[:12]
        self.reset(seed)

    def reset(self, seed=0):
        with self.lock:
            mujoco.mj_resetData(self.model, self.data)
            self.data.qpos[:5] = [0, -0.25, 0.08, 0.04, 0.04]
            self.data.ctrl[:] = self.data.qpos[:5]
            rng = np.random.default_rng(seed)
            if seed:
                # Only reset changes scene state; object truth never enters client observations.
                for color in ("red", "blue", "green"):
                    body = self.model.body(f"{color}_block")
                    adr = self.model.jnt_qposadr[body.jntadr[0]]
                    self.data.qpos[adr:adr+2] += rng.uniform(-0.035, 0.035, 2)
            self.step(250)

    def step(self, count):
        for _ in range(count):
            mujoco.mj_step(self.model, self.data)

    def state(self):
        with self.lock:
            return {"sim_time": float(self.data.time), "frame": "base",
                    "tcp_xyz": self.data.site("tcp").xpos.tolist(),
                    "gripper_width": float(self.data.qpos[3] + self.data.qpos[4]),
                    "joint_names": ["x", "y", "z", "left", "right"],
                    "joint_positions": self.data.qpos[:5].tolist(),
                    "joint_velocities": self.data.qvel[:5].tolist(),
                    "orientation": "fixed downward; no rotational DOF"}

    @staticmethod
    def validate_move(xyz, speed):
        a = np.asarray(xyz, dtype=float)
        if a.shape != (3,) or not np.isfinite(a).all() or not math.isfinite(speed):
            raise ValueError("finite xyz[3] and speed required")
        if not 0.01 <= speed <= 0.25:
            raise ValueError("speed must be 0.01..0.25 m/s")
        if np.any(a < [-0.38, -0.26, 0.025]) or np.any(a > [0.38, 0.26, 0.33]):
            raise ValueError("target outside workspace: x ±0.38, y ±0.26, z 0.025..0.33 m")
        return a

    def receipt(self, complete, executed, reason, stopped=True):
        return {"complete": bool(complete), "executed": executed,
                "stop_confirmed": bool(stopped), "reason": reason,
                "state": self.state(), "task_success": None}

    def hold(self):
        with self.lock:
            self.data.ctrl[:3] = self.data.qpos[:3]
            self.step(150)
            return bool(np.max(np.abs(self.data.qvel[:5])) < 0.02)

    def move(self, xyz, speed=0.12, cancel=lambda: False, feedback=lambda _: None):
        target = self.validate_move(xyz, speed)
        with self.lock:
            start = self.data.site("tcp").xpos.copy()
        count = max(1, int(np.ceil(np.linalg.norm(target-start) / speed / 0.02)))
        for i in range(1, count + 26):
            if cancel():
                stopped = self.hold()
                return self.receipt(False, "some" if i > 1 else "none", "cancelled", stopped)
            alpha = min(i / count, 1.)
            with self.lock:
                desired = start + alpha * (target-start)
                self.data.ctrl[:3] = desired - [0, 0, 0.2]
                # Gravity compensation for the z carriage servo, not for objects.
                self.data.qfrc_applied[:3] = self.data.qfrc_bias[:3]
                self.step(10)
                state = self.state()
            feedback(state)
        error = float(np.linalg.norm(np.array(state["tcp_xyz"])-target))
        stopped = self.hold() if error > 0.004 else bool(np.max(np.abs(self.data.qvel[:3])) < 0.02)
        result = self.receipt(error <= 0.004, "some", "reached" if error <= 0.004 else "tracking_error", stopped)
        result.update(target_xyz=target.tolist(), position_error_m=error)
        return result

    def grip(self, width, cancel=lambda: False, feedback=lambda _: None):
        if not math.isfinite(width) or not 0 <= width <= 0.08:
            raise ValueError("width must be finite and in 0..0.08 metres")
        for i in range(60):
            if cancel():
                with self.lock:
                    self.data.ctrl[3:] = self.data.qpos[3:5]
                return self.receipt(False, "some" if i else "none", "cancelled", self.hold())
            with self.lock:
                self.data.ctrl[3:] = width / 2
                self.step(10)
                state = self.state()
            feedback(state)
        settled = bool(np.max(np.abs(self.data.qvel[3:5])) < 0.02)
        # Contact may prevent requested closure. This reports settling, NOT a successful grasp.
        result = self.receipt(settled, "some", "settled" if settled else "not_settled", settled)
        result.update(requested_width=width, grasp_confirmed=None)
        return result

    def observe(self):
        with self.lock:
            with mujoco.Renderer(self.model, height=480, width=640) as renderer:
                renderer.update_scene(self.data, camera="overhead")
                rgb = renderer.render().copy()
                renderer.enable_depth_rendering()
                depth = renderer.render().copy()
            cam = self.model.camera("overhead")
            focal = 240 / math.tan(float(cam.fovy[0]) * math.pi / 360)
            # MuJoCo camera: +x right, +y up, -z forward. ROS optical: +y down, +z forward.
            rotation = self.data.cam_xmat[cam.id].reshape(3, 3) @ np.diag([1., -1., -1.])
            calibration = {"width": 640, "height": 480,
                           "K": [[focal, 0, 320], [0, focal, 240], [0, 0, 1]],
                           "depth_units": "metres along optical z",
                           "optical_to_base_rotation": rotation.tolist(),
                           "optical_to_base_translation": self.data.cam_xpos[cam.id].tolist()}
            return rgb, depth, calibration, self.state()

    def snapshot(self, root):
        rgb, depth, calibration, state = self.observe()
        sid = f"{self.session}-{uuid.uuid4().hex[:12]}"
        folder = Path(root).resolve() / "snapshots" / sid
        folder.mkdir(parents=True, exist_ok=False)
        Image.fromarray(rgb).save(folder / "rgb.png")
        np.save(folder / "depth.npy", depth)
        result = {"snapshot_id": sid, "snapshot_json_path": str(folder / "snapshot.json"),
                  "rgb_path": str(folder / "rgb.png"),
                  "depth_path": str(folder / "depth.npy"),
                  "calibration": calibration, "state": state}
        (folder / "snapshot.json").write_text(json.dumps(result, indent=2))
        return result

    def evaluate(self):
        """Evaluator-only ground truth. Never included in ROS observations or action receipts."""
        with self.lock:
            block = self.data.body("red_block").xpos
            plate = self.model.geom("red_plate").pos
            xy = float(np.linalg.norm(block[:2] - plate[:2]))
            return {"success": bool(xy < 0.035 and abs(block[2]-0.032) < 0.008
                                    and self.state()["gripper_width"] > 0.06),
                    "red_block_xyz": block.tolist(), "plate_distance_xy": xy}
