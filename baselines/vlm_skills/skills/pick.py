"""Baseline: VLM plus skills (docs/baselines.md). The robot's own Astra-with-skills loop, panda_twin_tracking
astra_skills_real.py SkillEpisode (11-12/13, the version run on the real arm), from the vendored copy (ctx.library):
Astra decides, the skills act. Astra is called at the start and after every skill with what the skill reported, a
fresh scene image and a wrist image over the area it last pointed at; its tools: look, pick(object, point),
place(to, to_point), move(object, point, to, to_point), push(object, point, to_point), done. Points are pixels of
the latest images; the skills find the object by name near the point (SAM3), plan on the wrist depth, act and check.

SkillEpisode's own run(), tools, skills and instructions run unchanged. Changed, and only this:
  - set up over ctx.robot / ctx.perception (its __init__ builds the rig itself: make_rig);
  - Astra reached through the world server (ctx.vlm.converse: no key in this sandbox), its images sent with the call;
  - the task sentence: the clutter-pick task's terms (lift and hold, the 60 s robot-time limit);
  - the episode ends when robot time is up (no motion is possible after it)."""
import json
import time
import types

import cv2
import numpy as np

MAX_CALLS = 25                                       # skill calls (astra_skills_real.py --max-calls default)
MAX_COST = 1.0                                       # $ (its --max-cost default; the run's per-episode cap applies too)


def run(ctx):
    AS = ctx.library
    images = []

    def png_item(rgb):
        images.append(np.ascontiguousarray(rgb))
        return {"type": "input_image", "image": len(images) - 1, "detail": "high"}

    def jpg_item(path):
        return png_item(cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB))

    AS.png_item, AS.jpg_item = png_item, jpg_item    # (the module's image items: arrays sent with the next call)

    class Episode(AS.SkillEpisode):
        def __init__(self):
            """SkillEpisode.__init__ (backend twin) without make_rig: the sandbox's robot and perception."""
            self.a = types.SimpleNamespace(backend="twin", max_calls=MAX_CALLS, max_cost=MAX_COST, text=None)
            self.out = ctx.out
            self.S, self.arm, self.world, self.wper, self.L = ctx.robot, None, None, None, None
            self.per = per = ctx.perception
            self.prog = prog = AS.SkillProgram(self.S, per, confirm_plan=False)
            prog.targets, prog.avoid, prog.box, prog.sizes, prog.reserved = {}, {}, None, {}, []
            prog.steps = per.steps = []
            per.seeds, per.templates, per.track_roi, per.still_mask = [], [], None, None
            prog.est, prog.PUSH_MAX = None, 0.08
            self.task = (ctx.task["instruction"] +              # (CHANGED: the clutter-pick task's terms)
                         " It is done when the object is held up in the hand (picked: at least 5 cm over where it lay, "
                         "held by both fingers, nothing else lifted) for 1 second; then call done(). The arm stops moving "
                         "after 60 s of robot time (the time you take to decide does not count).")
            self.box_i = None
            self.scene, self.views = None, {}
            self.focus = np.asarray(AS.FOCUS0, float)
            self.dir = per.dir
            self.n_img = 0
            self.log, self.calls, self.cost, self.usage = [], 0, 0.0, dict(input=0, cached=0, output=0, reasoning=0)
            self.n_tools, self.ended = 0, None
            self.exchange = open(ctx.out / "exchange.jsonl", "w")

        def ask(self, inp, prev=None):
            if self.calls and ctx.time_left() <= 0:              # (CHANGED: the episode's robot time is up)
                self.ended = self.ended or "robot time limit"
                return dict(id=prev, status="completed", output=[])
            ims = list(images); images.clear()
            self.exchange.write(json.dumps({"request": {"previous_response_id": prev, "input": inp, "n_images": len(ims)}}) + "\n")
            t = time.time()
            r = ctx.vlm.converse(inp, ims, instructions=self.instructions, tools=AS.TOOLS, previous=prev, effort=AS.EFFORT,
                                 max_output_tokens=20000)
            self.exchange.write(json.dumps({"response": r}) + "\n"); self.exchange.flush()
            if not r.get("ok"):
                raise RuntimeError(f"Astra: {r.get('why')}")
            tk = r.get("tokens") or {}
            self.calls += 1; self.cost += float(r.get("usd") or 0)
            for k in self.usage:
                self.usage[k] += int(tk.get(k, 0) or 0)
            self.log.append(dict(robot_s_left=round(ctx.time_left(), 2), api=dict(s=round(time.time() - t, 1), tokens=tk,
                                                                                    cost=r.get("usd"), status=r.get("status"))))
            ctx.log("astra", call=self.calls, usd=r.get("usd"), calls=[it.get("name") for it in r.get("output", [])
                                                                       if it.get("type") == "function_call"])
            return dict(id=r["id"], status=r.get("status"), output=r.get("output") or [])

    ep = Episode()
    try:
        ep.run()
    except Exception as e:
        import traceback
        traceback.print_exc(); ep.ended = ep.ended or f"crash: {e!r}"[:300]
    (ctx.out / "run.json").write_text(json.dumps(dict(ended=ep.ended, skill_calls=ep.n_tools, astra_calls=ep.calls,
                                                     cost_usd=round(ep.cost, 4), usage=ep.usage, log=ep.log), default=str))
    ep.exchange.close()
    return dict(result=ep.ended, skill_calls=ep.n_tools, astra_calls=ep.calls, cost_usd=round(ep.cost, 4))
