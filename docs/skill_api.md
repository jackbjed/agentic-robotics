# Skill programs

A skill program is a Python module with an entry function:

```python
def run(ctx) -> dict:          # dict(result="ok" | <why it failed>, ...): recorded, not used for judging
    ...
```

It runs in its own process, inside a sandbox: no network, read-only code, one writable directory (`ctx.out`). Its only
link to the world is the episode's socket. The world server judges the episode on the true state, which the program
never sees. Robot time is limited per episode (60 s); after that, motion is refused. Wall time is limited too (900 s).

## `ctx`

| field | |
|---|---|
| `ctx.task` | `dict(kind="clutter_pick", instruction="Pick up the red tape roll and lift it.", object="red tape roll")`; other tasks (docs/tasks.md): `kind` pick / place / close_box / press / insert, and `destination` (place, insert) or `part` (press) |
| `ctx.robot` | the robot (docs/robot.md): motion, cameras, joint / gripper / force state |
| `ctx.segmenter` | segmentation (docs/perception.md): `segment(rgb, phrase)`, `segment_points(rgb, pts, labels)` |
| `ctx.library` | the robot skill library, the vendored `astra_skills_real` module (below) |
| `ctx.perception` | the library's perception object over `ctx.robot` and `ctx.segmenter` (`track_run.TrackPerception`) |
| `ctx.vlm` | when the run allows it: the vision-language model (Astra, gpt-6-astra), **one call per episode** (below) |
| `ctx.log(what, **data)` | an event in the episode's `events.jsonl`, stamped with robot time |
| `ctx.save_image(name, rgb)` | a PNG in the episode's `program/` directory |
| `ctx.out` | the writable `program/` directory |
| `ctx.time_left()` | robot seconds left before motion is refused |

## The model call (`ctx.vlm`, when the run allows it)

```python
obs = ctx.robot.observe_frame("d415")                         # the scene camera's first frame
r = ctx.vlm.ask('Give the pixel [x, y] of the centre of the red tape roll as JSON {"target": [x, y]}.', [obs])
if r["ok"]:
    text = r["text"]                                          # parse it yourself; it can be wrong or malformed
```

- **One call per episode.** A second call is refused (`ok: False`). The images are 1–4 frames this episode's cameras
  delivered, as `observe_frame` dicts or their `rgb`.
- **Cost.** A short pointing answer costs about $0.006; a long answer (a plan) costs more. The run's model budget is in
  the brief. When it is used up, calls are refused, so the program must work without an answer.
- **Time.** Robot time does not advance while the model answers. Its wait is recorded separately, along with every
  request and reply, in the episode's `events.jsonl`.
- **A pixel to 3D:** `P, ok = ctx.library.F.world(obs)`; then `P[v, u]` is the world point at pixel (u, v) if
  `ok[v, u]` holds (noisy depth: use a small neighbourhood).

## The library (`ctx.library`, source in the workspace's `reference/`)

These are the robot's own skills, as they ran on the real arm. Key pieces:

- `ctx.library.SkillProgram(robot, perception)`: the clutter pick program. Its methods are inherited from
  `track_run.TrackProgram`, `real_box.RealClutterProgram`, `clutter_bench.ClutterProgram`, `task_box.BoxProgram` and
  `task_fast.FastProgram`.
  - `locate(i, phrase)`: wrist look(s) and the object's mask. Returns `(obs, mask, Q, info)`, where `Q` is the
    object's 3D points.
  - `best_grasp(obs, mask, Q)`: grasp candidates from the depth points (every closing direction, the opening, the
    height where nothing collides). Returns `(g, cands, geo, support, Pn, is_t, Q)`; `g` is None if no grasp clears.
  - `approach(g, geo)` and `grasp(g, Q, geo, support)`: over the grasp, down, close, check what came up, lift.
    Returns "ok" or why not.
  - `pick_seg(i)`: the full clutter pick with its own clearing (drags, taking blockers away). Its path is not used by
    the initial skill.
  - `drag(...)`, `side_push_any(...)`, `take_away(...)`, `push_to_clear(...)`: ways to clear a target.
  - `events`: the program's event list.
- `ctx.library.SkillEpisode.pick_direct(ep, i)`: the robot's pick tool (locate → covering check → best grasp →
  approach → grasp, with one more direction on a blocked descent). Returns `(result, detail, image)`.
- Modules: `ctx.library.F` (task_fast: `world(obs)`, `project(obs, X)`, ...), `ctx.library.CB` (clutter_bench),
  `ctx.library.T` (task_bench: `Unreachable`, `FINGERTIP`), `ctx.library.B` (stack_bench: `down(yaw)`, `OPEN_W`).

## The initial clutter-pick skill (`initial_skills/clutter_pick/skills/pick.py`)

This is `pick_direct` unchanged: the no-practice baseline. Its starting point (Astra's mark on the robot) is replaced
by its own search: the object's mask in the scene camera, giving the median of its 3D points
(`helpers/scene.py find_in_scene`).

## Writing your own

Import your helpers as `from helpers.x import y`; the workspace root is on the path. Anything the library does can be
wrapped, subclassed or replaced in `skills/` and `helpers/`. Programs must not try to read the simulator's state or
files outside their workspace. The sandbox and the socket enforce this; there is nothing else to reach.
