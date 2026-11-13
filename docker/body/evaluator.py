"""Maintainer-only truth evaluation over a body-local Unix socket, never ROS/HTTP."""
import itertools
import json
import os
import socket
import socketserver
import threading

import numpy as np

SOCKET = '/tmp/rosbody-evaluator.sock'


def evaluate(sim):
    with sim.lock:
        model, data = sim.model, sim.data
        block = model.body('red_block')
        geom_id = int(block.geomadr[0])
        plate = model.geom('red_plate')
        center = data.xpos[block.id].copy()
        rotation = data.xmat[block.id].reshape(3, 3)
        corners = np.array(list(itertools.product([-1, 1], repeat=3))) * model.geom_size[geom_id]
        corners = corners @ rotation.T + center
        inside = bool(np.all(np.linalg.norm(corners[:, :2] - plate.pos[:2], axis=1) < plate.size[0]))
        on_surface = abs(float(corners[:, 2].min() - (plate.pos[2] + plate.size[1]))) < .003
        dof = int(model.jnt_dofadr[block.jntadr[0]])
        stationary = bool(np.max(np.abs(data.qvel[dof:dof+6])) < .02)
        finger_ids = {int(model.body(n).geomadr[0]) for n in ('left_finger', 'right_finger')}
        held = any((c.geom1 == geom_id and c.geom2 in finger_ids) or
                   (c.geom2 == geom_id and c.geom1 in finger_ids) for c in data.contact)
        return dict(session_id=sim.session, sim_time=float(data.time),
                    success=inside and on_surface and stationary and not held,
                    footprint_inside=inside, on_plate_surface=bool(on_surface),
                    stationary=stationary, finger_contact=held)


def start_evaluator(driver):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            with driver.gate:
                result = evaluate(driver.sim)
                result['busy'] = driver.busy
                result['success'] = result['success'] and not driver.busy
            self.wfile.write((json.dumps(result) + '\n').encode())
    # /tmp is private to this container; no network listener or shared-volume truth.
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    server = socketserver.ThreadingUnixStreamServer(SOCKET, Handler)
    server.daemon_threads = True
    os.chmod(SOCKET, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == '__main__':
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(SOCKET)
        with client.makefile() as stream:
            print(stream.readline().strip())
