"""The bimanual X5 robot in ManiSkill (assets/x5/generated/dual_x5.urdf, made by x5/build.py). Same interface as
x5/sim_mujoco.py: reset(), step(target16), q(), tcp(side), cube(), render(cam). ManiSkill environment (robomme)."""
import math
import sys
from pathlib import Path

import numpy as np
import sapien
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mani_skill.agents.base_agent import BaseAgent, Keyframe  # noqa: E402
from mani_skill.agents.controllers import PDJointPosControllerConfig, deepcopy_dict  # noqa: E402
from mani_skill.agents.registration import register_agent  # noqa: E402
from mani_skill.envs.sapien_env import BaseEnv  # noqa: E402
from mani_skill.sensors.camera import CameraConfig  # noqa: E402
from mani_skill.utils import sapien_utils  # noqa: E402
from mani_skill.utils.registration import register_env  # noqa: E402
from mani_skill.utils.structs.types import SceneConfig, SimConfig  # noqa: E402

from x5 import spec as S  # noqa: E402

NAMES = S.joint_names()
IS_ARM = [n[2:] in S.ARM_JOINTS for n in NAMES]


@register_agent()
class DualX5(BaseAgent):
    uid = "dual_x5"
    urdf_path = str(S.GEN / "dual_x5.urdf")
    urdf_config = dict(_materials=dict(all=dict(static_friction=1.0, dynamic_friction=1.0, restitution=0.0)),
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


@register_env("M2M-X5-v1", max_episode_steps=10 ** 9)
class X5Env(BaseEnv):
    SUPPORTED_ROBOTS = ["dual_x5"]

    def __init__(self, *args, **kw):
        kw.pop("robot_uids", None)
        super().__init__(*args, robot_uids="dual_x5", **kw)

    @property
    def _default_sim_config(self):
        return SimConfig(sim_freq=int(round(1 / S.DT)), control_freq=int(round(1 / S.DT / S.CTRL_EVERY)),
                         scene_config=SceneConfig(solver_position_iterations=25, solver_velocity_iterations=2,
                                                  contact_offset=0.002, rest_offset=0.0))

    def _cam(self, name, c):
        f = c["height"] / 2 / math.tan(math.radians(c["fovy_deg"]) / 2)
        K = np.array([[f, 0, c["width"] / 2], [0, f, c["height"] / 2], [0, 0, 1.0]])
        return CameraConfig(name, pose=sapien_utils.look_at(eye=list(c["eye"]), target=list(c["target"])), width=c["width"],
                            height=c["height"], intrinsic=K, near=0.01, far=20.0, shader_pack="default")

    @property
    def _default_sensor_configs(self):
        return [self._cam("head", S.HEAD_CAM), self._cam("side", S.SIDE_CAM)]

    @property
    def _default_human_render_camera_configs(self):
        return []

    def _load_agent(self, options):
        super()._load_agent(options, sapien.Pose())

    def _load_lighting(self, options):
        self.scene.set_ambient_light([0.55, 0.55, 0.55])
        self.scene.add_directional_light([-0.1, 0.1, -1.0], [2.3, 2.3, 2.3], shadow=True, shadow_scale=3, shadow_map_size=2048)
        self.scene.add_directional_light([0.6, 0.5, -0.6], [0.5, 0.5, 0.5])

    @staticmethod
    def _lin(rgba):
        return [(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in rgba[:3]] + [rgba[3]]

    def _load_scene(self, options):
        t, c = S.TABLE, S.CUBE
        b = self.scene.create_actor_builder()
        mat = sapien.physx.PhysxMaterial(t["friction"], t["friction"], 0.0)
        b.add_box_collision(pose=sapien.Pose(p=t["pos"]), half_size=[v / 2 for v in t["size"]], material=mat)
        b.add_box_visual(pose=sapien.Pose(p=t["pos"]), half_size=[v / 2 for v in t["size"]],
                         material=sapien.render.RenderMaterial(base_color=self._lin(t["rgba"]), roughness=0.8, specular=0.1))
        b.add_box_collision(pose=sapien.Pose(p=[0, 0, -0.05]), half_size=[4, 4, 0.05], material=mat)
        b.add_box_visual(pose=sapien.Pose(p=[0, 0, -0.05]), half_size=[4, 4, 0.05],
                         material=sapien.render.RenderMaterial(base_color=self._lin([0.85, 0.85, 0.83, 1]), roughness=0.9, specular=0.05))
        b.initial_pose = sapien.Pose()
        self.world = b.build_static(name="world")
        b = self.scene.create_actor_builder()
        cm = sapien.physx.PhysxMaterial(c["friction"], c["friction"], 0.0)
        b.add_box_collision(half_size=[c["half"]] * 3, material=cm)
        b.add_box_visual(half_size=[c["half"]] * 3, material=sapien.render.RenderMaterial(base_color=self._lin(c["rgba"]), roughness=0.7, specular=0.2))
        b.initial_pose = sapien.Pose(p=c["pos"])
        self.cube = b.build(name="cube")
        I = c["mass"] * (2 * c["half"]) ** 2 / 6
        for obj in self.cube._objs:
            comp = obj.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
            comp.set_mass(c["mass"]); comp.set_inertia([I, I, I])

    def _after_reconfigure(self, options):
        for link in self.agent.robot.get_links():
            link.disable_gravity = True                             # (RoboDojo: disable_gravity=True)
            link.set_collision_group_bit(group=2, bit_idx=30, bit=1)   # (the robot's links do not touch each other:
            #                                                             their shapes are hulls that overlap at the joints)
        for j in self.agent.robot.get_active_joints():
            if j.name[2:] in S.ARM_JOINTS:
                for o in j._objs:
                    o.set_armature(np.array([S.ARM_ARMATURE], dtype=np.float32))

    def _initialize_episode(self, env_idx, options):
        with torch.device(self.device):
            q = np.zeros(16); q[[6, 7, 14, 15]] = S.GRIP_OPEN
            self.agent.robot.set_pose(sapien.Pose())
            self.agent.reset(torch.tensor(self._order(q), dtype=torch.float32)[None])
            self.cube.set_pose(sapien.Pose(p=S.CUBE["pos"]))
            self.cube.set_linear_velocity(torch.zeros(3)); self.cube.set_angular_velocity(torch.zeros(3))

    def _order(self, q):
        """A 16-vector in spec.joint_names() order -> the articulation's active-joint order."""
        act = [j.name for j in self.agent.robot.get_active_joints()]
        return np.array([q[NAMES.index(n)] for n in act])

    def evaluate(self):
        return {}

    def _get_obs_extra(self, info):
        return {}

    def compute_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)

    def compute_normalized_dense_reward(self, obs, action, info):
        return torch.zeros(self.num_envs, device=self.device)


class Sim:
    name = "maniskill"

    def __init__(self):
        import gymnasium as gym
        self.env = gym.make("M2M-X5-v1", obs_mode="none", control_mode="pd_joint_pos", sim_backend="cpu", render_backend="gpu")
        self.names = NAMES
        self.reset()

    def home(self):
        q = np.zeros(16); q[[6, 7, 14, 15]] = S.GRIP_OPEN
        return q

    def reset(self):
        self.env.reset(seed=0)
        self.U = self.env.unwrapped
        self.robot = self.U.agent.robot
        act = [j.name for j in self.robot.get_active_joints()]
        self.perm = np.array([act.index(n) for n in NAMES])          # (articulation order -> ours)
        # the controller's action is in ITS joint order: the order of the names it was given (ours)
        self.t = 0.0
        return self.home()

    def step(self, target):
        self.env.step(torch.tensor(np.asarray(target, float), dtype=torch.float32)[None])
        self.t += S.DT * S.CTRL_EVERY

    def q(self):
        return self.robot.get_qpos()[0].cpu().numpy().astype(float)[self.perm]

    def tcp(self, side):
        T = self.U.agent.hand[side].pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3] + T[:3, 0] * S.TCP_X, T[:3, :3].copy()

    def cube(self):
        T = self.U.cube.pose.to_transformation_matrix()[0].cpu().numpy().astype(float)
        return T[:3, 3].copy(), T[:3, :3].copy()

    def render(self, cam="head"):
        U = self.U
        U.scene.update_render(update_sensors=True, update_human_render_cameras=False)
        s = U._sensors[cam]; s.capture()
        col = s.camera.get_picture("Color"); col = (col[0] if isinstance(col, (list, tuple)) else col).cpu().numpy()
        col = col[0] if col.ndim == 4 else col
        return np.ascontiguousarray((np.clip(col[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8))
