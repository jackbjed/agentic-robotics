"""The clutter-pick scene (PRIVILEGED: lives only in the world server). One target tape roll (red or yellow) among 2-4
touching or nearby objects on the table; success = the target lifted >= 5 cm, held by both fingers, nothing else
lifted, for 1 s. Reset, judge and sensor state are ported from panda_pick_rgbd's PickEnv (pickrgbd/env.py, commit
75c201f: reset() without the RL observation/reward parts, _judge(), episode_summary()), so that an episode here is the
same episode as there: the same layout bank entry, start pose, settle, per-episode camera calibration error, depth
noise stream and judge.

Levels (layout banks, panda_pick_rgbd/pickrgbd/layouts.py): the gap between the target's outline and each other
object's: L0 6-10 cm, L1 3.5-6 cm, L2 1.5-3.5 cm, L3 boxed in (2-12 mm all round, no free straight-down grasp).
Splits: train (seed 101, 2000 / level), val (202, 100), test (303, 200)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .. import paths
from .picktwin import PickTwin
from .sensors import Corruptor
from .twin import B, MIN_Z

TASK = dict(lift_ok=0.05, hold_s=1.0, wrong_lift=0.03, disturb_mm=30.0)     # pickrgbd/config.py TaskCfg (judge)
PHRASES = {"tape_red": "red tape roll", "tape_yellow": "yellow tape roll", "tape_white": "white tape roll",
           "hole_block": "black block with holes", "cable": "coiled black cable", "screwdriver": "screwdriver",
           "glue": "glue bottle", "sticky_pad": "sticky note pad"}
# condition C (docs/fidelity.md, scripts/build_condition_c.py): other shapes, the same body names, masses, frictions
PHRASES_SUBSTITUTE = {"tape_red": "red can", "tape_yellow": "yellow can", "tape_white": "white can",
                      "hole_block": "black box", "cable": "black hoop", "screwdriver": "red and grey bar",
                      "glue": "red and white bottle", "sticky_pad": "purple disc"}
PHRASES_TASKS = dict(PHRASES, box_white="white box", kettle="kettle", peg_stand="wooden stand with three vertical sticks",
                     wood_cyl="short wooden cylinder")   # (SAM3: "wooden cylinder" -> the stand's sticks in 9/10 layouts; this 10/10)
# the objects the multi-task model adds (scripts/build_tasks_model.py), as task_bench / precision_bench describe them
OBJ_TASKS = {"kettle": dict(kind="kettle", r=0.070, h=0.177, z0=0.0, desc="the steel kettle"),
             "peg_stand": dict(kind="peg_stand", h=0.173, z0=0.0, desc="the wooden stand with three vertical wooden sticks"),
             "wood_cyl": dict(kind="kcup", r=0.020, h=0.040, z0=0.0, hole=0.022,
                              desc="the wooden cylinder (a hole goes along its axis)")}
# simple_sim (docs/simple_sim.md, scripts/build_simple_sim.py): a simple, INACCURATE world: the table 5 cm lower,
# brown; MuJoCo primitives for objects; a plain grey box. The same body names (the vendored world needs them):
PHRASES_SIMPLE = {"tape_red": "red cylinder", "tape_yellow": "yellow cylinder", "tape_white": "red cube",
                  "hole_block": "blue cube", "sticky_pad": "green cube", "box_white": "grey box"}
SIMPLE_POOL = ("tape_red", "tape_yellow", "tape_white", "hole_block", "sticky_pad")
OBJECT_SETS = {
    "real": dict(model=None, phrases=dict(PHRASES, box_white="white box"), banks="layouts"),   # the twin's objects
    "tasks": dict(model="sim/models/twin_6522_wedge_tasks.xml", phrases=PHRASES_TASKS, banks="layouts_tasks",
                  world="tasks"),                                        # + kettle, stand with sticks, wooden cylinder
    "substitute": dict(model="sim/models/twin_6522_wedge_substitute.xml", phrases=PHRASES_SUBSTITUTE,
                       banks="layouts_substitute"),
    # the families it has (their banks: <the family's banks>_simple), its own objects and box; table_z: its table top;
    # the robot's limits there (sim/picktwin.py): the world's arm refuses fingertips under ITS table, while the
    # program's planner gets a looser floor (planner_floor_z) that does not give the table's height away
    "simple": dict(model="sim/models/simple_sim.xml", phrases=PHRASES_SIMPLE, banks_suffix="_simple", world="simple",
                   pool=SIMPLE_POOL, targets=SIMPLE_POOL, destination="grey box", table_z=-0.05,
                   tasks=("place-box", "close-box", "clutter-pick-any"), planner_floor_z=-0.10, tcp_lo_z=-0.10),
}
ACTIVE = {"name": "real"}                                      # (the object set this process uses: use_object_set)


def use_object_set(name):
    """Point the vendored code's model at the object set's (before any PickTwin / BoxSim is built). -> the set."""
    from .twin import B, TB
    S = OBJECT_SETS[name]
    ACTIVE.setdefault("model0", B.MODEL)
    if S["model"] is not None:
        B.MODEL = paths.SRC / "agentic_robotics" / S["model"]
    elif str(B.MODEL).endswith(OBJECT_SETS["simple"]["model"].split("/")[-1]):
        B.MODEL = ACTIVE["model0"]                             # (after simple_sim: the twin again)
    if S.get("world") == "tasks":
        for k, v in OBJ_TASKS.items():
            TB.OBJ.setdefault(k, dict(v))
    ACTIVE["name"] = name
    return S


def active_set():
    """The object set this process uses (use_object_set; default real)."""
    return OBJECT_SETS[ACTIVE["name"]]


def bank_name(task_def, object_set):
    """The layout banks' folder of a task family in an object set."""
    S = OBJECT_SETS[object_set]
    if object_set == "substitute":
        return S["banks"]
    return task_def["banks"] + S.get("banks_suffix", "")


def world_class(name):
    if OBJECT_SETS[name].get("world") == "tasks":
        from .worlds import TasksBoxSim
        return TasksBoxSim
    if OBJECT_SETS[name].get("world") == "simple":
        from .worlds import SimpleBoxSim
        return SimpleBoxSim
    return None


def load_bank(split, level, object_set="real", banks=None):
    f = paths.DATA / (banks or OBJECT_SETS[object_set]["banks"]) / f"{split}_L{level}.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"{f}: real: scripts/link_assets.py (manifest clutter_layouts); "
                                f"substitute: bin/ar layouts --object-set substitute ...")
    return [json.loads(s) for s in open(f) if s.strip()]


class ClutterPickScene:
    def __init__(self, preset="d415", colour_noise=False, latency_s=(0.0, 0.0), colour="mujoco", object_set=None,
                 task="clutter-pick", mounts="twin", arm_start=None):
        """arm_start: None = the layout's own start pose; "home" = the arm at its home joints whatever the layout says
        (the task config's arm_start: as the real robot starts)."""
        from .. import tasks as TK
        self.task_name, self.tdef = task, TK.get(task)
        self.arm_start = arm_start
        if self.tdef["object_set"] != "real":                    # (press, insert: the multi-task model, always)
            if object_set not in (None, "real", self.tdef["object_set"]):
                raise ValueError(f"task {task}: no {object_set!r} version (its objects: {self.tdef['object_set']!r} only)")
            object_set = self.tdef["object_set"]
        object_set = object_set or "real"
        if object_set == "substitute" and task != "clutter-pick":
            raise ValueError("condition C's substitute objects have clutter-pick layouts only")
        if object_set == "simple" and task not in OBJECT_SETS["simple"]["tasks"]:
            raise ValueError(f"simple_sim has no {task} (its families: {', '.join(OBJECT_SETS['simple']['tasks'])})")
        if self.tdef.get("colour_only") and colour != self.tdef["colour_only"]:
            raise ValueError(f"task {task}: colour {self.tdef['colour_only']} only (no scans of its objects)")
        self.object_set = object_set
        S_ = use_object_set(object_set)
        self.phrases = S_["phrases"]
        self.table_z = float(S_.get("table_z", 0.0))
        self.destination = S_.get("destination")                # (simple_sim: its own box)
        self.planner = (dict(table_z=S_["planner_floor_z"], tcp_lo_z=S_["tcp_lo_z"]) if "planner_floor_z" in S_ else None)
        if colour == "splat" and object_set != "real":
            raise ValueError("the splat composite has the 6522 scan's objects only (none for the substitutes, the kettle, "
                             "the stand or the cylinder)")
        self.P = P = PickTwin(render=True, world_cls=world_class(object_set))
        if mounts not in ("twin", "calibrated"):
            raise ValueError(f"camera mounts {mounts!r}")
        P.use_cameras(mounts)                                  # (twin: the model's; calibrated: the real rig's calib.json)
        self.mounts = mounts
        self.preset, self.colour_noise, self.latency_s = preset, colour_noise, tuple(latency_s)
        self.colour = colour                                   # mujoco (condition B) | splat (condition A)
        self.compositor = None
        if colour == "splat":
            from .composite import Compositor
            self.compositor = Compositor(P)
        elif colour != "mujoco":
            raise ValueError(f"colour mode {colour!r}")
        self.corr = Corruptor(preset, P.world, MIN_Z)
        self.banks = {}
        self.seg_cache, self.seg_order = {}, []
        m = P.m
        names = [m.body(b).name for b in range(m.nbody)]
        kid = {f"obj_{k}": i for i, k in enumerate(P.obj_keys)}
        self.geom_obj = np.array([kid.get(names[r], -1) for r in P.root], np.int16)

    # ------------------------------------------------------------------ reset (PickEnv.reset, options=layout)
    def reset(self, split, level, idx, seed):
        key = (split, int(level))
        if key not in self.banks:
            self.banks[key] = load_bank(split, level, self.object_set, bank_name(self.tdef, self.object_set))
        return self.reset_layout(self.banks[key][int(idx)], seed, split, level, idx)

    def reset_layout(self, L, seed, split="custom", level=-1, idx=-1):
        """The episode on layout L (a bank entry, or a scene the agent wrote: sim/custom.py), reset seed `seed`."""
        from gymnasium.utils import seeding
        np_random, _ = seeding.np_random(int(seed))
        self.L = L
        self.split, self.level, self.layout_idx = split, int(level), int(idx)
        P = self.P
        P.reset_world(active=L["objs"])
        for k, q7 in L["objs"].items():
            P.put(k, 0, 0, 0, qpos7=np.asarray(q7))
        if self.arm_start == "home":
            P.teleport_arm(None, 0.0, q=P.S.q_home)
        else:
            P.teleport_arm(L["start"]["tip"], L["start"]["yaw"], q=L["arm_q"])
        w = P.world
        if hasattr(w, "latch"):                                      # (the kettle: latched, lid and button home)
            w.set_latch(True)
        for jn, v in (L.get("joints") or {}).items():                # (the box's lid opening, ...)
            P.d.qpos[P.m.joint(jn).qposadr[0]] = float(v)
        if L.get("joints"):
            P.mj.mj_forward(P.m, P.d)
        self.target = L["target"]; P.target = self.target; P.L = {"objs": list(L["objs"])}
        self.targets = list(L.get("targets") or [self.target])        # (pack: every object to put in the box)
        self.distractors = [k for k in L["distractors"] if k not in self.targets]
        rng = np.random.default_rng(int(np_random.integers(1 << 62)))
        self.ep_corrupt = self.corr.reset(rng, self.latency_s, light_hook=self._scale_lights)
        P.settle(0.2)
        P.sync_controller()
        self.rest = {k: float(v) for k, v in L["rest_com_z"].items()}
        self.xy0 = {k: P.world.pose(k)[0][:2].copy() for k in L["objs"]}
        self.top_t = P.footprint(self.target)[1]
        self.hold_t0 = None; self.max_lift = 0.0; self.ever_touched = False
        self.t0 = float(P.d.time)
        self.jd = self.judge()
        self.t_success = None
        # PickEnv.reset rendered the policy's first observation here (both cameras through the sensor model): the
        # same draws from the depth noise stream keep every later frame identical to the original episodes
        for cam in ("d415", "L_wrist"):
            rgb, z, gid = P.render(cam, depth=True, seg=self.corr.d415 is not None)
            self.corr.rgb(rgb, cam)
            self.corr.depth(z, gid if gid is not None else np.full(z.shape, -1, np.int32), cam)
        return self.task()

    def _scale_lights(self, s):
        m = self.P.m
        d0, a0, s0, hd, ha = self.P.light0
        m.light_diffuse[:] = d0 * s; m.light_specular[:] = s0 * s; m.light_ambient[:] = a0 * min(s, 1.3)
        m.vis.headlight.diffuse[:] = hd * s; m.vis.headlight.ambient[:] = ha * min(s, 1.3)

    def task(self):
        """What the skill is told: the instruction and the object's name. Nothing else about the scene."""
        ph = self.phrases[self.target]
        t = self.tdef
        if self.task_name == "clutter-pick":                         # (milestone 1's exact task dict: parity)
            return dict(kind="clutter_pick", instruction=f"Pick up the {ph} and lift it.", object=ph)
        if self.task_name == "pack":                                 # (several objects, named in the instruction)
            names = [self.phrases[k] for k in self.targets]
            the = [f"the {n}" for n in names]
            lst = the[0] if len(the) == 1 else ", ".join(the[:-1]) + " and " + the[-1]
            return dict(kind="pack", objects=names, object=names[0], destination=t["destination"],
                        instruction=t["instruction"].format(objects=lst, destination=t["destination"]))
        dest = self.destination or t.get("destination", "")
        out = dict(kind=t.get("kind", self.task_name.replace("-", "_")), object=ph,
                   instruction=t["instruction"].format(object=ph, destination=dest))
        for k in ("destination", "part"):
            if t.get(k):
                out[k] = dest if k == "destination" else t[k]
        return out

    def truth_hint(self):
        """PARITY ONLY (never in learning or evaluation): the seed point the 11-18 skill baseline gave the skill, the
        target's true top centre (Astra's point stand-in)."""
        P, k = self.P, self.target
        if self.task_name == "press":                                # (diagnostics: the button cap's top)
            g = P.m.geom("obj_kettle_btn").id
            return dict(p=[*map(float, P.d.geom_xpos[g][:2]), float(P.d.geom_xpos[g][2] + P.m.geom_size[g][1])])
        if self.task_name == "insert":                               # (diagnostics: the middle stick's tip)
            g = P.m.geom("obj_peg_stand_peg1").id
            return dict(p=[*map(float, P.d.geom_xpos[g][:2]), float(P.d.geom_xpos[g][2] + P.m.geom_size[g][1])])
        top = float(P.world.pose(k)[0][2] + self.top_t)
        return dict(p=[*map(float, P.d.xipos[P.world.ob[k]][:2]), min(top, 0.06)])

    # ------------------------------------------------------------------ judge (PickEnv._judge)
    def judge(self):
        P = self.P
        fc = P.finger_contacts()
        lift = P.com_z(self.target) - self.rest[self.target]
        held = P.held_by_both(self.target, fc)
        touch_t = P.touching_fingers(self.target, fc)
        self.ever_touched |= touch_t
        self.max_lift = max(self.max_lift, lift)
        wrong = None; disturb = {}
        for k in self.distractors:
            lk = P.com_z(k) - self.rest[k]
            if lk >= TASK["wrong_lift"] and P.touching_fingers(k, fc):
                wrong = k
            disturb[k] = float(np.linalg.norm(P.world.pose(k)[0][:2] - self.xy0[k]))
        off = [k for k in self.L["objs"] if P.com_z(k) < self.table_z - 0.05]
        extra = {}
        kind = self.tdef["judge"]
        if kind == "lift":
            cond = lift >= TASK["lift_ok"] and held and wrong is None
        else:
            cond, extra = self._judge_task(kind, touch_t, wrong, off)
        now = float(P.d.time)
        if cond:
            self.hold_t0 = now if self.hold_t0 is None else self.hold_t0
        else:
            self.hold_t0 = None
        success = cond and (now - self.hold_t0) >= TASK["hold_s"]
        if success and self.t_success is None and hasattr(self, "t0"):
            self.t_success = now - self.t0
        return dict(lift=lift, held=held, touch=touch_t, wrong=wrong, off_table=off, disturb=disturb, success=bool(success), **extra)

    def _judge_task(self, kind, touch_t, wrong, off):
        """The non-lift tasks' conditions (tasks.py), on the true state."""
        import math
        from .twin import TB
        P = self.P; w = P.world
        tilt = lambda k: math.degrees(math.acos(min(1.0, w.pose(k)[1][2, 2])))
        inside_box = w.inside_box if hasattr(w, "inside_box") else (lambda k: TB.inside_box(w, k))   # (simple_sim: its box)
        if kind == "in_box":
            inside = inside_box(self.target)
            others = [k for k in self.distractors if k != "box_white" and inside_box(k)]
            bt = tilt("box_white")
            cond = inside and not touch_t and bt < 10 and not others and not off and wrong is None
            return cond, dict(inside_box=bool(inside), others_in_box=others, box_tilt_deg=round(bt, 1))
        if kind == "packed":
            ins = [k for k in self.targets if inside_box(k)]
            held = any(P.touching_fingers(k) for k in self.targets)
            others = [k for k in self.distractors if k != "box_white" and inside_box(k)]
            lid = math.degrees(P.d.qpos[w.lid_q]); bt = tilt("box_white")
            cond = len(ins) == len(self.targets) and not held and lid < 8 and bt < 10 and not others and not off and wrong is None
            return cond, dict(in_box=ins, n_in_box=len(ins), n_targets=len(self.targets), lid_deg=round(lid, 1),
                              box_tilt_deg=round(bt, 1), others_in_box=others)
        if kind == "lid_closed":
            lid = math.degrees(P.d.qpos[w.lid_q]); bt = tilt("box_white")
            moved = float(np.linalg.norm(w.pose("box_white")[0][:2] - self.xy0["box_white"]))
            cond = lid < 8 and bt < 10 and moved < 0.10 and not off
            return cond, dict(lid_deg=round(lid, 1), box_tilt_deg=round(bt, 1), box_moved_mm=round(moved * 1000, 1))
        if kind == "kettle_open":
            lid = math.degrees(P.d.qpos[w.kettle_lid_q]); kt = tilt("kettle")
            btn = float(-P.d.qpos[w.btn_q])
            cond = lid > 30 and kt < 15 and not off
            return cond, dict(kettle_lid_deg=round(lid, 1), button_mm=round(btn * 1000, 2), latched=bool(w.latched),
                              kettle_tilt_deg=round(kt, 1))
        if kind == "on_stick":
            p, R = w.pose("wood_cyl"); best, stick = 1e9, None
            for i in range(3):
                g = P.m.geom(f"obj_peg_stand_peg{i}").id
                gp = P.d.geom_xpos[g]; tip = gp[2] + P.m.geom_size[g][1]
                dxy = float(np.linalg.norm(p[:2] - gp[:2]))
                if p[2] < tip - 0.01 and dxy < best:
                    best, stick = dxy, i
            hole = 0.022; stick_r = 0.009
            on = best < hole / 2 - stick_r + 0.003
            cond = on and not touch_t and not off
            return cond, dict(on_stick=bool(on), cyl_to_stick_mm=round(best * 1000, 1) if best < 1e8 else None, stick=stick)
        raise ValueError(kind)

    def summary(self):
        jd = self.jd
        if self.t_success is None and self.tdef["judge"] != "lift":   # (the other tasks: what was missing)
            cat = ("wrong_object" if jd["wrong"] else "off_table" if jd["off_table"] else "not_done")
        elif self.t_success is not None:
            cat = "success"
        elif jd["wrong"]:
            cat = "wrong_object"
        elif jd["off_table"]:
            cat = "off_table"
        elif self.max_lift >= 0.02:
            cat = "dropped"
        elif self.ever_touched:
            cat = "touched_not_lifted"
        else:
            cat = "not_touched"
        dist = max(jd["disturb"].values()) if jd["disturb"] else 0.0
        task_state = {k: jd[k] for k in ("inside_box", "in_box", "n_in_box", "n_targets", "others_in_box", "box_tilt_deg",
                                          "lid_deg", "box_moved_mm",
                                          "kettle_lid_deg", "button_mm", "latched", "kettle_tilt_deg", "on_stick",
                                          "cyl_to_stick_mm", "stick") if k in jd}
        nm = (lambda k: k) if self.object_set != "simple" else (lambda k: self.phrases.get(k, k) if k else k)
        if self.object_set == "simple":                              # (simple_sim's practice records: the names it was
            for k in ("in_box", "others_in_box"):                    # told, not the twin's body names)
                if k in task_state:
                    task_state[k] = [nm(x) for x in task_state[k]]
        return dict(task=self.task_name, task_state=task_state, category=cat, success=self.t_success is not None,
                    t_success=None if self.t_success is None else round(self.t_success, 3),
                    max_lift_mm=round(self.max_lift * 1000, 1), max_disturb_mm=round(dist * 1000, 1),
                    disturbed=bool(dist * 1000 > TASK["disturb_mm"]), touched_target=bool(self.ever_touched),
                    wrong_object=nm(jd["wrong"]), off_table=[nm(k) for k in jd["off_table"]], target=nm(self.target),
                    n_distractors=len(self.distractors), corrupt={k: v for k, v in self.ep_corrupt.items() if not k.startswith("_")
                                                                  and k in ("preset", "calib", "depth_scale", "latency_s", "light")})

    # ------------------------------------------------------------------ cameras (what the robot's cameras deliver)
    def camera(self, cam):
        """A frame of camera cam now: colour (plain MuJoCo render from the TRUE camera pose; the sensor's colour
        variation only with colour_noise), depth through the D415 model (the episode's calibration error included),
        and the intrinsics. The true segmentation is kept here (server side) for the oracle segmentation service."""
        P = self.P
        rgb, z, gid = P.render(cam, depth=True, seg=True)
        depth = self.corr.depth(z, gid, cam)
        m = P.m; cid = m.camera(cam).id
        Wc, Hc = B.SIZE[cam]
        f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        K = (Wc, Hc, f, f, Wc / 2, Hc / 2)
        if self.compositor is not None:
            rgb = self.compositor.photo(cam, rgb, z, gid, K)
        if self.colour_noise:
            rgb = self.corr.rgb(rgb, cam)
        lab = np.where(gid >= 0, self.geom_obj[np.clip(gid, 0, None)], -1).astype(np.int16)
        k = frame_key(rgb)
        self.seg_cache[k] = lab; self.seg_order.append(k)
        if len(self.seg_order) > 96:
            self.seg_cache.pop(self.seg_order.pop(0), None)
        return rgb, depth.astype(np.float32), K

    def colour_frame(self, cam):
        """Camera cam's colour now, as the condition renders it (for videos): no depth model, no noise draws."""
        P = self.P
        if self.compositor is None:
            return P.render(cam, depth=False, seg=False)[0]
        rgb, z, gid = P.render(cam, depth=True, seg=True)
        m = P.m; cid = m.camera(cam).id; Wc, Hc = B.SIZE[cam]
        f = Hc / 2 / math.tan(math.radians(m.cam_fovy[cid]) / 2)
        return self.compositor.photo(cam, rgb, z, gid, (Wc, Hc, f, f, Wc / 2, Hc / 2))

    def labels_for(self, rgb):
        return self.seg_cache.get(frame_key(rgb))

    def overview(self, W=480, H=360):
        """A third-person view for videos (a free camera looking at the target's area)."""
        P = self.P
        tp = self.xy0.get(self.target, np.zeros(2))
        return P.free_view(np.r_[tp, 0.10], distance=0.95, azimuth=205.0, elevation=-32.0)


def frame_key(rgb):
    import hashlib
    return hashlib.md5(np.ascontiguousarray(rgb[::4, ::4]).tobytes()).hexdigest()
