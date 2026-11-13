"""Private physics and sensors. No Cartesian adapter or task controller."""
from pathlib import Path
import threading
import uuid
import json
import math
import time
import numpy as np
import mujoco
from PIL import Image

MODEL = Path(__file__).resolve().parent / "tabletop.xml"


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
        result = {"snapshot_id": sid, "session_id": self.session,
                  "captured_at_unix": time.time(), "snapshot_json_path": str(folder / "snapshot.json"),
                  "rgb_path": str(folder / "rgb.png"),
                  "depth_path": str(folder / "depth.npy"),
                  "calibration": calibration, "state": state}
        (folder / "snapshot.json").write_text(json.dumps(result, indent=2))
        return result
