"""World classes beyond the vendored ones. TasksBoxSim = task_box.BoxSim on the multi-task model
(scripts/build_tasks_model.py: + the kettle, the stand with three sticks, the wooden cylinder) with the kettle's latch
as task_bench.TaskSim has it: the lid hinge is held by the equality obj_kettle_latch until the lid-release button is
2.5 mm in (checked every physics substep), then the lid's spring opens it. The button cap's contact is stiffened as
panda_twin's press bench does (PRESS_STIFF_BUTTON, solref 0.004 s: a 1.4 g cap so soft that a fingertip sinks 1.5-3 mm
before it moves, otherwise). MuJoCo 2.3.7: eq_active lives in the model."""
from __future__ import annotations

from .twin import TB


class TasksBoxSim(TB.BoxSim):
    def __init__(self):
        super().__init__()
        m = self.m
        self.latch = m.eq("obj_kettle_latch").id
        self.btn_q = m.joint("obj_kettle_button_slide").qposadr[0]
        self.kettle_lid_q = m.joint("obj_kettle_lid_hinge").qposadr[0]
        g = m.geom("obj_kettle_btn").id
        m.geom_priority[g] = 5; m.geom_solref[g] = [0.004, 1.0]

    @property
    def latched(self):
        arr = self.d.eq_active if hasattr(self.d, "eq_active") else self.m.eq_active
        return bool(arr[self.latch])

    def set_latch(self, on):
        arr = self.d.eq_active if hasattr(self.d, "eq_active") else self.m.eq_active
        arr[self.latch] = int(on)

    def physics(self, n_frames=1):
        m, d, mj = self.m, self.d, self.mj
        for _ in range(n_frames):
            for _ in range(self.sub):
                if self.gravcomp:
                    d.qfrc_applied[self.arm_dofs] = d.qfrc_bias[self.arm_dofs]
                mj.mj_step(m, d)
                if self.latched and d.qpos[self.btn_q] < -0.0025:     # the button releases the lid
                    self.set_latch(False)
            self.rec.append((float(d.time), d.qpos.copy()))
