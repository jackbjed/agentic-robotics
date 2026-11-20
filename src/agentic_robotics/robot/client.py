"""The robot as a skill program reaches it: the world server's Unix socket, behind the two interfaces the vendored
RealSim (panda_twin/code/real/real_sim.py) already uses for the real robot (RemoteArm: hello / state / execute / idle /
wait_gripper / now; cameras: get(cam, after, timeout)). So the program-side robot is the ORIGINAL RealSim, unchanged:
the same motion planning, limit checks, timing and settle rules as on the robot path, here over the twin.

make_robot(sock) -> (robot, link): robot = RealSim (see robot/api.py for the documented API)."""
from __future__ import annotations

import time

import numpy as np

from ..execution import protocol


class Link:
    """One REQ socket to the world server; counts the wall time spent waiting for it, per request kind."""

    def __init__(self, sock_path, timeout_s=900):
        import zmq
        self.zmq = zmq
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.REQ)
        self.sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000)); self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(f"ipc://{sock_path}")
        self.wait = {}
        self.n = {}

    def call(self, cmd, arrays=None, **kw):
        t0 = time.time()
        protocol.send(self.sock, dict(kw, cmd=cmd), arrays)
        try:
            h, arr = protocol.recv(self.sock)
        except self.zmq.Again:
            from ..sim.twin import RobotStop
            raise RobotStop(f"no answer from the world server ({cmd})")
        self.wait[cmd] = self.wait.get(cmd, 0.0) + time.time() - t0
        self.n[cmd] = self.n.get(cmd, 0) + 1
        return h, arr


class SocketArm:
    """real_sim.RemoteArm's interface over the world socket (the twin: sim time; refusals -> RobotStop)."""

    def __init__(self, link):
        self.link = link

    def hello(self):
        return self.link.call("hello")[0]

    def state(self):
        r = self.link.call("state")[0]
        if not r.get("ok"):
            from ..sim.twin import RobotStop
            raise RobotStop("arm state: " + str(r.get("why")))
        return r

    def execute(self, t, Q, grip=None, settle=False, phase=None, hold_after=0.1, guard=None):
        return self.link.call("execute", t=np.asarray(t, float).tolist(), q=np.asarray(Q, float).tolist(), grip=grip,
                              settle=bool(settle), phase=phase, hold_after=hold_after, guard=guard)[0]

    def idle(self, T_):
        r = self.link.call("idle", T=float(T_))[0]
        if not r.get("ok"):
            from ..sim.twin import RobotStop
            raise RobotStop(str(r.get("why")))

    def wait_gripper(self, timeout=4.0):
        return self.link.call("wait_gripper", timeout=timeout)[0]

    def now(self):
        return float(self.link.call("now")[0]["t"])


class SocketCams:
    """The two D415s as the world server delivers them (colour, depth in m with inf = none, intrinsics)."""

    def __init__(self, link):
        self.link = link

    def get(self, cam, after=0.0, timeout=2.0):
        h, arr = self.link.call("camera", cam=cam)
        if not h.get("ok"):
            from ..sim.twin import RobotStop
            raise RobotStop(f"camera {cam}: {h.get('why')}")
        return dict(rgb=arr[0], depth=arr[1], K=tuple(h["K"]), t=h["t"])

    def info(self):
        return {}

    def close(self):
        pass


def make_robot(sock_path):
    from ..sim import twin
    link = Link(sock_path)
    h = link.call("hello")[0]
    mounts = h.get("camera_mounts", "twin")                # (the poses the program believes: as the world's)
    rig = twin.load_rig()
    pl = h.get("planner")                                  # (simple_sim: a floor under its lower table, not its height;
    if pl:                                                 # the world's arm still refuses fingertips under the table)
        rig["safety"]["table_z"] = float(pl["table_z"])
        rig["safety"]["tcp_lo"] = [*rig["safety"]["tcp_lo"][:2], float(pl["tcp_lo_z"])]
    S = twin.RealSim(SocketArm(link), SocketCams(link), rig, twin.camera_calib(mounts), speed=1.0, v_max=None,
                     log_dir=None)
    S.task_timing = True                                   # (the robot path's timing: real_box run, PickTwin)
    return S, link
