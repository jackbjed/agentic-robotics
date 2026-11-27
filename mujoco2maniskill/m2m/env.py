"""The ManiSkill side: the rig's left Panda with the project's gripper, the table, the two cameras, and the objects of
one exported scene (tools/export_scene.py), simulated by SAPIEN/PhysX and rendered by SAPIEN's renderer.

Everything the robot is TOLD about itself is the project's (the same arm, gripper shapes, camera mounts, joint and
gripper gains, speeds, limits). What differs is what does the simulating: the contact solver and its friction rule,
the joint drives, the renderer and its depth.

    env = gym.make("M2M-Scene-v1", scene_spec=<scene dict>, scene_dir=<its folder>)
    env.reset(seed=...)
Frame: the rig's (z up, the table top at z = spec["table_z"], the robot at -x facing +x); metres."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import sapien
import torch
from mani_skill.agents.base_agent import BaseAgent, Keyframe
from mani_skill.agents.controllers import PDJointPosControllerConfig, PDJointPosMimicControllerConfig, deepcopy_dict
from mani_skill.agents.registration import register_agent
from mani_skill.envs.sapien_env import BaseEnv
from mani_skill.sensors.camera import CameraConfig
from mani_skill.utils import sapien_utils
from mani_skill.utils.registration import register_env
from mani_skill.utils.structs.types import SceneConfig, SimConfig

from .build_urdf import build as build_urdf

W, H = 640, 480
# the project's arm servos (models/sim_bimanual_grippers.xml: position actuators kp, kv) and gripper (kp 2000 N/m on
# one finger with the two coupled, 70 N: per finger that is half of each)
ARM_KP = [4500.0, 4500.0, 3500.0, 3500.0, 2000.0, 2000.0, 2000.0]
ARM_KD = [450.0, 450.0, 350.0, 350.0, 200.0, 200.0, 200.0]
ARM_FORCE = [87.0, 87.0, 87.0, 87.0, 12.0, 12.0, 12.0]          # (the Panda's joint torque limits)
GRIP_KP, GRIP_KD, GRIP_FORCE = 1000.0, 20.0, 35.0
FINGER_FRICTION = 1.5
# The fingers' contact gives a little, as every contact does in MuJoCo (its default: a critically damped spring of
# 20 ms; a hand arriving at 40 mm/s is slowed with about 20 N over some 15 ms, and the 4 N contact test fires).
# PhysX's rigid contact stops it within one 2 ms step: 70 to 180 N, a safety stop where MuJoCo and the real arm report
# a contact. So the fingers get PhysX's spring contact, of the stiffness that gives that 20 N (v * sqrt(k * m), with
# about 5 kg of arm behind the hand).
FINGER_STIFFNESS = 5.0e4
SIM_HZ, CTRL_HZ = 500, 100
CV2SAPIEN = np.array([[0.0, -1.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]])   # (camera axes: OpenCV -> SAPIEN's x forward, y left, z up)
ROBOT_LINKS_FELT = ("panda_hand", "panda_leftfinger", "panda_rightfinger", "panda_link7", "panda_link6", "panda_link5",
                    "panda_link4", "panda_link3", "panda_link2", "panda_link1")


def quat_R(q):
    w, x, y, z = [float(v) for v in q]
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def R_quat(R):
    R = np.asarray(R, float); t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2; q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(R)))
        if i == 0:
            s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
            q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
        elif i == 1:
            s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
            q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
        else:
            s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
            q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    q = np.array(q); return q / np.linalg.norm(q)


def pose_of(p, R):
    return sapien.Pose(p=np.asarray(p, float), q=R_quat(R))


def quat_mul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return np.array([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw])


X2Z = np.array([math.sqrt(0.5), 0.0, -math.sqrt(0.5), 0.0])       # (SAPIEN's cylinders and capsules lie along x; MuJoCo's along z)


@register_agent()
class PandaUMI(BaseAgent):
    uid = "panda_umi"
    urdf_path = str(build_urdf())
    urdf_config = dict(
        _materials=dict(gripper=dict(static_friction=FINGER_FRICTION, dynamic_friction=FINGER_FRICTION, restitution=-FINGER_STIFFNESS)),
        link=dict(panda_leftfinger=dict(material="gripper", patch_radius=0.0, min_patch_radius=0.0),
                  panda_rightfinger=dict(material="gripper", patch_radius=0.0, min_patch_radius=0.0)))
    keyframes = dict(rest=Keyframe(qpos=np.array([0.0, np.pi / 8, 0, -np.pi * 5 / 8, 0, np.pi * 3 / 4, np.pi / 4, 0.04, 0.04]),
                                   pose=sapien.Pose()))
    arm_joint_names = [f"panda_joint{i}" for i in range(1, 8)]
    gripper_joint_names = ["panda_finger_joint1", "panda_finger_joint2"]
    ee_link_name = "panda_hand_tcp"

    @property
    def _controller_configs(self):
        arm = PDJointPosControllerConfig(self.arm_joint_names, lower=None, upper=None, stiffness=ARM_KP, damping=ARM_KD,
                                         force_limit=ARM_FORCE, normalize_action=False)
        grip = PDJointPosMimicControllerConfig(self.gripper_joint_names, lower=0.0, upper=0.04, stiffness=GRIP_KP,
                                               damping=GRIP_KD, force_limit=GRIP_FORCE, normalize_action=False,
                                               mimic={"panda_finger_joint2": {"joint": "panda_finger_joint1"}})
        return deepcopy_dict(dict(pd_joint_pos=dict(arm=arm, gripper=grip)))

    def _after_init(self):
        L = self.robot.get_links()
        self.hand = sapien_utils.get_obj_by_name(L, "panda_hand")
        self.tcp = sapien_utils.get_obj_by_name(L, self.ee_link_name)
        self.fingers = [sapien_utils.get_obj_by_name(L, "panda_leftfinger"), sapien_utils.get_obj_by_name(L, "panda_rightfinger")]


@register_env("M2M-Scene-v1", max_episode_steps=10 ** 9)
class SceneEnv(BaseEnv):
    SUPPORTED_ROBOTS = ["panda_umi"]
    agent: PandaUMI

    def __init__(self, *args, scene_spec=None, scene_dir=None, shader="default", record_camera=True, **kw):
        if scene_spec is None:
            raise ValueError("scene_spec: a scene exported by tools/export_scene.py")
        self.sc, self.sc_dir, self.shader, self.record_camera = scene_spec, (Path(scene_dir) if scene_dir else None), shader, record_camera
        self.bodies = {}
        kw.pop("robot_uids", None)
        super().__init__(*args, robot_uids="panda_umi", **kw)

    # ------------------------------------------------------------------ configuration
    @property
    def _default_sim_config(self):
        return SimConfig(sim_freq=SIM_HZ, control_freq=CTRL_HZ,
                         scene_config=SceneConfig(solver_position_iterations=25, solver_velocity_iterations=2,
                                                  contact_offset=0.002, rest_offset=0.0, enable_ccd=True))

    def _cam_poses(self, which):
        """(the camera's TRUE pose in its body's frame, as SAPIEN wants it; the body's name)"""
        c = self.sc["cameras"][which]
        t = c["true_in_body"]
        return pose_of(t["eye"], np.asarray(t["R"]) @ CV2SAPIEN), c["body"]

    @property
    def _default_sensor_configs(self):
        f = H / 2 / math.tan(math.radians(self.sc["cameras"]["scene"]["fovy"]) / 2)
        K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])
        out = []
        for which in ("scene", "left_wrist"):
            local, body = self._cam_poses(which)
            if which == "left_wrist":
                out.append(CameraConfig(which, pose=local, width=W, height=H, intrinsic=K, near=0.01, far=20.0,
                                        mount=self.agent.hand, shader_pack=self.shader))
            else:
                b = next(x for x in self.sc["bodies"] if x["name"] == body)
                world = sapien.Pose(p=b["pos"], q=b["quat"]) * local
                out.append(CameraConfig(which, pose=world, width=W, height=H, intrinsic=K, near=0.01, far=20.0,
                                        shader_pack=self.shader))
        return out

    @property
    def _default_human_render_camera_configs(self):
        if not self.record_camera:
            return []
        # (the project's recording view: looking at the table from the robot's right front)
        pose = sapien_utils.look_at(eye=[0.789, -0.571, 0.639], target=[-0.30, -0.175, 0.17])
        return CameraConfig("render_camera", pose=pose, width=640, height=480, fov=0.785, near=0.01, far=20.0, shader_pack=self.shader)

    # ------------------------------------------------------------------ loading
    def _load_agent(self, options):
        a = self.sc["arm"]
        super()._load_agent(options, sapien.Pose(p=a["base_pos"], q=a["base_quat"]))

    def _load_lighting(self, options):
        # (MuJoCo's picture of this scene shows a lit, level surface at about 1.7 times its colour value: its
        #  headlight and two lights add up; in linear light that is about 3.2 times)
        self.scene.set_ambient_light([0.55, 0.55, 0.55])
        self.scene.add_directional_light([-0.1, 0.1, -1.0], [2.3, 2.3, 2.3], shadow=True, shadow_scale=3, shadow_map_size=2048)
        self.scene.add_directional_light([0.6, -0.5, -0.6], [0.5, 0.5, 0.5])

    def _material(self, rgba, texture=None):
        # (a scene's colours are display values, as MuJoCo draws them; SAPIEN's renderer wants linear ones and
        #  encodes its picture for display: without this a black mat comes out dark grey)
        c = [float(v) for v in rgba]
        lin = [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in c[:3]] + [c[3]]
        m = sapien.render.RenderMaterial(base_color=lin, roughness=0.8, specular=0.1, metallic=0.0)
        if texture is not None:
            m.base_color_texture = sapien.render.RenderTexture2D(filename=str(texture))
        return m

    def _phys_material(self, g, mass=None):
        """The contact material of a shape. A shape the world gave its own contact softness (MuJoCo solref / solimp
        other than the defaults: a sponge) gets PhysX's spring contact (a negative "restitution" is its stiffness,
        N/m): MuJoCo's contact spring scales with the body's mass, k = m / timeconst^2 x d / (1 - d) at its full
        impedance d, so a light sponge pressed on the table gives by millimetres under a few newtons. Every other
        contact is PhysX's rigid one."""
        mu, rest = float(g["friction"][0]), 0.0
        sr, si = g.get("solref"), g.get("solimp")
        if mass and sr and si and sr[0] > 0 and (abs(sr[0] - 0.02) > 1e-6 or abs(si[0] - 0.9) > 1e-6 or abs(si[2] - 0.001) > 1e-6):
            rest = -float(np.clip(mass / sr[0] ** 2 * si[1] / max(1.0 - si[1], 1e-3), 200.0, 2.0e5))
            self.soft[g.get("name") or g.get("body") or "?"] = round(-rest)
        return sapien.physx.PhysxMaterial(mu, mu, rest)

    def _add_geom(self, b, g, phys=True, vis=True, mass=None):
        pose = sapien.Pose(p=g["pos"], q=g["quat"])
        s, kind = g["size"], g["type"]
        mat = self._phys_material(g, mass)
        rm = self._material(g["rgba"])
        seen = vis and g["rgba"][3] > 0.02 and g.get("group", 0) < 3
        felt = phys and g["col"]
        if kind == "box":
            if felt:
                b.add_box_collision(pose=pose, half_size=s, material=mat)
            if seen:
                b.add_box_visual(pose=pose, half_size=s, material=rm)
        elif kind == "sphere":
            if felt:
                b.add_sphere_collision(pose=pose, radius=s[0], material=mat)
            if seen:
                b.add_sphere_visual(pose=pose, radius=s[0], material=rm)
        elif kind in ("cylinder", "capsule"):
            px = sapien.Pose(p=g["pos"], q=quat_mul(g["quat"], X2Z))
            if kind == "cylinder":
                if felt:
                    b.add_cylinder_collision(pose=px, radius=s[0], half_length=s[1], material=mat)
                if seen:
                    b.add_cylinder_visual(pose=px, radius=s[0], half_length=s[1], material=rm)
            else:
                if felt:
                    b.add_capsule_collision(pose=px, radius=s[0], half_length=s[1], material=mat)
                if seen:
                    b.add_capsule_visual(pose=px, radius=s[0], half_length=s[1], material=rm)
        elif kind == "plane":
            if felt:
                b.add_box_collision(pose=sapien.Pose(p=np.asarray(g["pos"]) - [0, 0, 0.05], q=g["quat"]), half_size=[6, 6, 0.05], material=mat)
            if seen:
                b.add_box_visual(pose=sapien.Pose(p=np.asarray(g["pos"]) - [0, 0, 0.05], q=g["quat"]), half_size=[6, 6, 0.05], material=rm)
        else:
            self.unbuilt.append(f"{g.get('name') or '?'}: a {kind} (not built)")

    def _load_scene(self, options):
        sp = self.sc
        self.unbuilt, self.soft, self.rolling = [], {}, {}
        # the fixed world: the table, its legs, the floor, the robot's frame, the camera's post
        b = self.scene.create_actor_builder()
        tex = sp.get("table_texture")
        for g in sp["world_geoms"]:
            if g.get("name") == "table_top" and tex and self.sc_dir is not None:
                pose = sapien.Pose(p=g["pos"], q=g["quat"])
                b.add_box_collision(pose=pose, half_size=g["size"], material=sapien.physx.PhysxMaterial(g["friction"][0], g["friction"][0], 0.0))
                b.add_box_visual(pose=pose, half_size=g["size"], material=self._material([1, 1, 1, 1], self.sc_dir / tex["file"]))
            else:
                self._add_geom(b, g)
        b.initial_pose = sapien.Pose()
        self.world = b.build_static(name="world")
        # the bodies of the scene
        self.bodies, self.start, self.attached = {}, {}, {}
        for B in sp["bodies"]:
            for k in B.get("members", []):                          # (bodies fixed to this one: where they are in it)
                self.attached[k["name"]] = (B["name"], sapien.Pose(p=k["pos"], q=k["quat"]))
            b = self.scene.create_actor_builder()
            for g in B["geoms"]:
                self._add_geom(b, g, mass=float(B["mass"]) if B["free"] else None)
            pose = sapien.Pose(p=B["pos"], q=B["quat"])
            b.initial_pose = pose
            if B["free"]:
                a = b.build(name=B["name"])
                for obj in a._objs:
                    c = obj.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
                    c.set_mass(max(float(B["mass"]), 1e-5))
                    c.set_cmass_local_pose(sapien.Pose(p=B["ipos"], q=B["iquat"]))
                    c.set_inertia(np.maximum(np.asarray(B["inertia"], float), 1e-9))
                    # A round thing with rolling friction in MuJoCo (condim 6, friction[2] = a length): PhysX has no
                    # rolling friction, so its spin is damped at the rate that stops it, rolling at 40 mm/s (a
                    # stroke's speed), over the distance MuJoCo's rolling friction would: 1226 / s per (length / radius).
                    roll = [g for g in B["geoms"] if g.get("condim", 3) >= 6 and g["type"] in ("sphere", "capsule", "cylinder", "ellipsoid")]
                    ad = 0.05
                    if roll:
                        ad = float(np.clip(1226.0 * roll[0]["friction"][2] / max(roll[0]["size"][0], 1e-4), 0.05, 400.0))
                        self.rolling[B["name"]] = round(ad, 1)
                    c.set_linear_damping(0.0); c.set_angular_damping(ad)
            else:
                a = b.build_static(name=B["name"])
            self.bodies[B["name"]] = a; self.start[B["name"]] = pose

    def _initialize_episode(self, env_idx, options):
        with torch.device(self.device):
            a = self.sc["arm"]
            q = np.r_[a["home_q"], 0.04, 0.04]
            self.agent.robot.set_pose(sapien.Pose(p=a["base_pos"], q=a["base_quat"]))
            self.agent.reset(torch.tensor(q, dtype=torch.float32)[None])
            for n, act in self.bodies.items():
                if n in self.start and not act.px_body_type == "static":
                    act.set_pose(self.start[n])
                    act.set_linear_velocity(torch.zeros(3)); act.set_angular_velocity(torch.zeros(3))

    def _after_reconfigure(self, options):
        for link in self.agent.robot.get_links():                   # (the arm holds itself up, as the real arm's
            link.disable_gravity = True                             #  controller does: no sag to take out)

    # ------------------------------------------------------------------ gym's needs
    def evaluate(self):
        return {}

    def _get_obs_extra(self, info):
        return {}

    def compute_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)

    def compute_normalized_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)
