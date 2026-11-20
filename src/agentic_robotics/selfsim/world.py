"""The agent's own simulator as a world (real2sim, docs/real2sim.md): the composed model (selfsim/model.py) behind the
same world server as the twin (sim/server.py: the same robot path, limits, recording), with

  - the agent's layouts (its sim/layouts.py, sampled in the program sandbox: selfsim/sample.py): its bodies placed,
    resting on its table, its lid opened, the arm at home, 1 s of settling;
  - the success checks of the real world's tasks (sim/scene.py, tasks.py), on the agent's declared names: lift
    (clutter-pick-any), inside the box (place-box: its declared interior), lid closed (close-box);
  - the agent's sensor model (sim.yaml sensors: depth noise, dropout, edge and dark holes, depth scale; per-episode
    camera pose errors; colour gain and noise); plain MuJoCo colour of its own materials and textures.

PRIVILEGED like the twin's world (it holds the true state of the agent's simulator), but it runs no agent code: the
scene is data, the layouts arrive as checked JSON."""
from __future__ import annotations

import math

import numpy as np

from ..sim.scene import frame_key
from ..sim.twin import B, TB, MIN_Z
from .model import SimError

TASK = dict(lift_ok=0.05, hold_s=1.0, wrong_lift=0.03)                       # (sim/scene.py TASK: the same checks)


class SelfBoxSim(TB.BoxSim):
    """task_box.BoxSim on the composed model (its placeholders satisfy BoxSim's names), with the agent's free bodies
    added to the objects the world knows (pose, subtree, parking)."""
    DECL = None

    def __init__(self):
        super().__init__()
        m, D = self.m, SelfBoxSim.DECL
        free = list(D["objects"].values()) + ([D["box"]["body"]] if D.get("box") else [])
        for b in free:
            bid = m.body(b).id
            self.ob[b] = bid
            self.oq[b] = m.jnt_qposadr[m.body_jntadr[bid]]
            self.subtree[b] = {c for c in range(m.nbody) if self.root_of(c) == bid}


def _rotz(yaw):
    return [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


def small_rot(v):
    th = float(np.linalg.norm(v))
    if th < 1e-12:
        return np.eye(3)
    k = v / th; K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


class SelfSimScene:
    """The world server's scene interface (as sim/scene.py ClutterPickScene) over the agent's simulator."""

    def __init__(self, sim, task):
        from ..sim.picktwin import PickTwin
        from ..sim.scene import use_object_set
        from .. import tasks as TK
        use_object_set("real")                                   # (the robot's own limits: the real world's rig)
        self.D = D = sim["decl"]
        self.task_name, self.tdef = task, TK.get(task)
        if task not in ("place-box", "close-box", "clutter-pick-any"):
            raise SimError(f"no task {task!r} in real2sim")
        if task in ("place-box", "close-box") and not D.get("box"):
            raise SimError(f"{task} needs the box (sim.yaml box)")
        model0 = B.MODEL
        B.MODEL = sim["model"]; SelfBoxSim.DECL = D
        try:
            self.P = P = PickTwin(render=True, world_cls=SelfBoxSim)
        finally:
            B.MODEL = model0
        P.use_cameras("calibrated")
        self.mounts, self.planner, self.object_set = "calibrated", None, "selfsim"
        m = P.m
        self.names = {b: n for n, b in D["objects"].items()}         # body -> the name the tasks use
        self.box = D["box"]["body"] if D.get("box") else None
        if self.box:
            self.names[self.box] = D["box"]["name"]
            self.lid_q = m.joint(D["box"]["lid_joint"]).qposadr[0]
        self.phrases = dict(self.names)
        self.table_g = [m.geom(g).id for g in D["table"]]
        arm, w = P.arm, P.world                                   # (the arm's force estimate: things resting on the
        box_bodies = w.subtree[self.box] if self.box else set()   # agent's table or in its box are not held)
        arm.box_b = set(box_bodies) or arm.box_b
        if self.box:
            arm.box_dof = m.jnt_dofadr[m.body_jntadr[m.body(self.box).id]]
        arm._tables()
        arm._tab = np.zeros(m.ngeom, bool); arm._tab[self.table_g] = True
        self.geom_obj = np.array([-1] * m.ngeom, np.int16)
        keys = list(P.obj_keys)
        for g in range(m.ngeom):
            r = P.root[g]
            for i, k in enumerate(keys):
                if w.ob[k] == r:
                    self.geom_obj[g] = i
        self.seg_cache, self.seg_order = {}, []
        self.sens = D["sensors"]

    # ------------------------------------------------------------------ geometry
    def table_top(self):
        P, d, m = self.P, self.P.d, self.P.m
        top = -1e9
        for g in self.table_g:
            R = d.geom_xmat[g].reshape(3, 3); c, h = m.geom_aabb[g].reshape(2, 3)
            for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3):
                top = max(top, float((d.geom_xpos[g] + R @ (c + h * s))[2]))
        return top

    def bottom(self, b):
        """The lowest point of body b's own collision geoms, in its frame (z)."""
        P, m = self.P, self.P.m
        lo = 1e9
        for g in np.nonzero(m.geom_bodyid == m.body(b).id)[0]:
            if not (m.geom_contype[g] or m.geom_conaffinity[g]):
                continue
            Rg = np.zeros(9); P.mj.mju_quat2Mat(Rg, m.geom_quat[g]); Rg = Rg.reshape(3, 3)
            c, h = m.geom_aabb[g].reshape(2, 3)
            for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3):
                lo = min(lo, float((m.geom_pos[g] + Rg @ (c + h * s))[2]))
        return 0.0 if lo > 1e8 else lo

    # ------------------------------------------------------------------ reset
    def reset_layout(self, L, seed, split="selfsim", level=0, idx=-1):
        """L: a checked layout (selfsim/sample.py check_layout): target (a body), place {body: [x, y, yaw] |
        [x, y, z, qw, qx, qy, qz]}, joints {name: value}."""
        P, m, d = self.P, self.P.m, self.P.d
        self.L = L; self.split, self.level, self.layout_idx = split, int(level), int(idx)
        rng = np.random.default_rng(int(seed))
        P.reset_world(active=list(L["place"]))
        P.mj.mj_forward(m, d)
        top = self.table_top()
        for b, v in L["place"].items():
            if len(v) == 3:
                q7 = [v[0], v[1], top - self.bottom(b) + 0.001] + _rotz(v[2])
            else:
                q7 = list(v)
            P.put(b, 0, 0, 0, qpos7=np.asarray(q7, float))
        for jn, v in (L.get("joints") or {}).items():
            d.qpos[m.joint(jn).qposadr[0]] = float(v)
        P.teleport_arm(None, 0.0, q=P.S.q_home)
        self.ep = self._draw_sensors(rng)
        P.settle(1.0)
        P.sync_controller()
        self.target = L["target"]; P.target = self.target; P.L = {"objs": list(L["place"])}
        self.objs = list(L["place"])
        self.distractors = [k for k in self.objs if k != self.target]
        self.rest = {k: P.com_z(k) for k in self.objs}
        self.xy0 = {k: P.world.pose(k)[0][:2].copy() for k in self.objs}
        self.table_z = top
        self.hold_t0 = None; self.max_lift = 0.0; self.ever_touched = False
        self.t0 = float(d.time)
        self.jd = self.judge()
        self.t_success = None
        return self.task()

    def _draw_sensors(self, rng):
        w = self.P.world
        ep = dict(calib={}, depth_scale={}, gain={})
        for cam in ("d415", "L_wrist"):
            dp, dr = self.sens["calibration_error"].get(cam, (0.0, 0.0))
            u = rng.normal(size=3); u /= np.linalg.norm(u); v = rng.normal(size=3); v /= np.linalg.norm(v)
            e_p, e_r = u * rng.uniform(0, dp), v * rng.uniform(0, dr)
            w.set_camera_error(cam, e_p, e_r)
            ep["calib"][cam] = [round(float(np.linalg.norm(e_p)) * 1000, 2), round(float(np.linalg.norm(e_r)), 3)]
            s = self.sens["depth"].get("scale", 0.0)
            ep["depth_scale"][cam] = float(1 + rng.uniform(-s, s))
            g = self.sens["colour"].get("gain", (1.0, 1.0))
            ep["gain"][cam] = float(rng.uniform(*g))
        self.rng = rng
        return ep

    def task(self):
        name = self.names[self.target]
        t = self.tdef
        if self.task_name == "close-box":
            return dict(kind="close_box", object=name, instruction=t["instruction"].format(object=name))
        if self.task_name == "place-box":
            dest = self.names[self.box]
            return dict(kind="place", object=name, destination=dest, instruction=t["instruction"].format(object=name, destination=dest))
        return dict(kind="clutter_pick", object=name, instruction=t["instruction"].format(object=name))

    # ------------------------------------------------------------------ the checks (sim/scene.py's, on its names)
    def judge(self):
        P = self.P; w = P.world
        fc = P.finger_contacts()
        lift = P.com_z(self.target) - self.rest[self.target]
        held = P.held_by_both(self.target, fc)
        touch_t = P.touching_fingers(self.target, fc)
        self.ever_touched |= touch_t
        self.max_lift = max(self.max_lift, lift)
        wrong = None
        for k in self.distractors:
            if k == self.box:
                continue
            if P.com_z(k) - self.rest[k] >= TASK["wrong_lift"] and P.touching_fingers(k, fc):
                wrong = k
        off = [k for k in self.objs if P.com_z(k) < self.table_z - 0.05]
        extra = {}
        if self.task_name == "clutter-pick-any":
            cond = lift >= TASK["lift_ok"] and held and wrong is None
        else:
            bp, bR = w.pose(self.box)
            bt = math.degrees(math.acos(min(1.0, bR[2, 2])))
            if self.task_name == "place-box":
                ins = self.inside_box(self.target)
                others = [k for k in self.distractors if k != self.box and self.inside_box(k)]
                cond = ins and not touch_t and bt < 10 and not others and not off and wrong is None
                extra = dict(inside_box=bool(ins), others_in_box=[self.names.get(k, k) for k in others], box_tilt_deg=round(bt, 1))
            else:
                lid = math.degrees(P.d.qpos[self.lid_q])
                moved = float(np.linalg.norm(bp[:2] - self.xy0[self.box]))
                cond = lid < 8 and bt < 10 and moved < 0.10 and not off
                extra = dict(lid_deg=round(lid, 1), box_tilt_deg=round(bt, 1), box_moved_mm=round(moved * 1000, 1))
        now = float(P.d.time)
        if cond:
            self.hold_t0 = now if self.hold_t0 is None else self.hold_t0
        else:
            self.hold_t0 = None
        success = cond and (now - self.hold_t0) >= TASK["hold_s"]
        if success and self.t_success is None and hasattr(self, "t0"):
            self.t_success = now - self.t0
        return dict(lift=lift, held=held, touch=touch_t, wrong=wrong, off_table=off, disturb={}, success=bool(success), **extra)

    def inside_box(self, k):
        bp, bR = self.P.world.pose(self.box)
        rel = bR.T @ (self.P.d.xipos[self.P.world.ob[k]] - bp)
        I = self.D["box"]["inside"]
        return bool(all(I[i][0] < rel[i] < I[i][1] for i in range(3)))

    def summary(self):
        jd = self.jd
        nm = lambda k: self.names.get(k, k) if k else k
        if self.t_success is not None:
            cat = "success"
        elif jd["wrong"]:
            cat = "wrong_object"
        elif jd["off_table"]:
            cat = "off_table"
        elif self.task_name != "clutter-pick-any":
            cat = "not_done"
        elif self.max_lift >= 0.02:
            cat = "dropped"
        elif self.ever_touched:
            cat = "touched_not_lifted"
        else:
            cat = "not_touched"
        ts = {k: jd[k] for k in ("inside_box", "others_in_box", "box_tilt_deg", "lid_deg", "box_moved_mm") if k in jd}
        return dict(task=self.task_name, task_state=ts, category=cat, success=self.t_success is not None,
                    t_success=None if self.t_success is None else round(self.t_success, 3),
                    max_lift_mm=round(self.max_lift * 1000, 1), max_disturb_mm=0.0, disturbed=False,
                    touched_target=bool(self.ever_touched), wrong_object=nm(jd["wrong"]),
                    off_table=[nm(k) for k in jd["off_table"]], target=nm(self.target),
                    n_distractors=len(self.distractors), corrupt=dict(preset="selfsim", **{k: v for k, v in self.ep.items()}))

    # ------------------------------------------------------------------ cameras: its rendering + its sensor model
    def camera(self, cam):
        import cv2
        P = self.P
        rgb, z, gid = P.render(cam, depth=True, seg=True)
        S = self.sens["depth"]; rng = self.rng
        z = z * self.ep["depth_scale"][cam]
        ok = np.isfinite(z) & (z > MIN_Z)
        if S.get("noise"):
            a, b = S["noise"]
            z = z + rng.normal(size=z.shape) * (a + b * np.where(ok, z, 0) ** 2)
        if S.get("edge_holes_px"):
            zz = np.where(ok, z, 0).astype(np.float32)
            gx = np.abs(cv2.Sobel(zz, cv2.CV_32F, 1, 0, ksize=3)); gy = np.abs(cv2.Sobel(zz, cv2.CV_32F, 0, 1, ksize=3))
            edge = ((gx + gy) > 0.08).astype(np.uint8)
            k = 2 * S["edge_holes_px"] + 1
            ok &= cv2.dilate(edge, np.ones((k, k), np.uint8)) == 0
        if S.get("dark_holes") and S["dark_holes"]["p"] > 0:
            lum = rgb.astype(np.float32).mean(2) / 255.0
            ok &= ~((lum < S["dark_holes"]["below"]) & (rng.random(z.shape) < S["dark_holes"]["p"]))
        if S.get("dropout"):
            ok &= rng.random(z.shape) >= S["dropout"]
        depth = np.where(ok, z, np.inf).astype(np.float32)
        g = self.ep["gain"][cam]; nse = self.sens["colour"].get("noise", 0.0)
        if g != 1.0 or nse:
            f = rgb.astype(np.float32) * g
            if nse:
                f += rng.normal(size=f.shape) * nse
            rgb = np.clip(f + 0.5, 0, 255).astype(np.uint8)
        m = P.m; cid = m.camera(cam).id
        Wc, Hc = B.SIZE[cam]
        fpx = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        K = (Wc, Hc, fpx, fpx, Wc / 2, Hc / 2)
        lab = np.where(gid >= 0, self.geom_obj[np.clip(gid, 0, None)], -1).astype(np.int16)
        k = frame_key(rgb)
        self.seg_cache[k] = lab; self.seg_order.append(k)
        if len(self.seg_order) > 96:
            self.seg_cache.pop(self.seg_order.pop(0), None)
        return rgb, depth, K

    def colour_frame(self, cam):
        return self.P.render(cam, depth=False, seg=False)[0]

    def labels_for(self, rgb):
        return self.seg_cache.get(frame_key(rgb))
