"""press, initial version (the robot's press skill, press_skill.py): the button's pixel in the scene camera from the
one model call (as on the robot: Astra marked it) -> a seed on that pixel's ray (press_skill.seed_from_pixel); then
PressSkill.f_press: a high wrist look along the ray, the button's cap found in the wrist depth (a small round bump), a
closer look, a press angle clear of the handle, one fingertip pressed straight down until it feels the button give
(force) or 6 mm past the cap's top, then up. Without the model (not allowed, refused, or no answer): the kettle's
highest points in the scene camera as the seed. The skill's second Astra mark (in the wrist image, when two bumps look
alike) is off: one call per episode."""
import json
import re

from helpers.program import add_step, log_events, setup_program
from helpers.scene import find_in_scene

PROMPT = """{instruction}
This is the robot's scene camera, {W}x{H} pixels; pixel coordinates are x from the left edge, y from the top edge.
The robot will press the button from above with a fingertip. Give the centre of the button to press, as seen in this
image (null if it is not in the image).
Respond with this JSON object and nothing else:
{{"point": [x, y] or null, "reasoning": "<1 sentence>"}}"""


def button_from_model(ctx, PS):
    if getattr(ctx, "vlm", None) is None:
        return None
    obs = ctx.robot.observe_frame("d415")
    H, W = obs["rgb"].shape[:2]
    r = ctx.vlm.ask(PROMPT.format(instruction=ctx.task["instruction"], W=W, H=H), [obs])
    if not r.get("ok"):
        ctx.log("model call refused", why=str(r.get("error", ""))[:120]); return None
    m = re.search(r"\{.*\}", r["text"], re.S)
    try:
        uv = json.loads(m.group(0))["point"] if m else None
    except (ValueError, KeyError):
        uv = None
    ctx.log("model: button pixel", point=uv)
    if not uv or len(uv) != 2:
        return None
    return PS.seed_from_pixel(obs, [float(uv[0]), float(uv[1])])


def run(ctx):
    PS = ctx.library.TR.RB.TB.PS                # (press_skill, as the robot program imports it)
    PS.WRIST_MARK = False
    prog = setup_program(ctx)
    k = ctx.task.get("hint") or button_from_model(ctx, PS) \
        or find_in_scene(ctx, ctx.task["object"], cap_z=None, top_frac=0.01)   # (hint: diagnostics only)
    if k is None:
        return dict(result="not found", detail="the scene camera does not show the kettle")
    i = add_step(ctx, prog, "press", ctx.task["object"], k["p"], k.get("ray"))
    AS = ctx.library
    try:
        r = prog.f_press(i)
    except (AS.T.Unreachable, AS.F.Unseen) as e:
        r = f"{type(e).__name__}: {str(e)[:150]}"
    log_events(ctx, prog)
    return dict(result=r)
