# Skill programs (written from scratch)

A skill program is a Python module with an entry function:

```python
def run(ctx) -> dict:          # dict(result="ok" | <why it failed>, ...): recorded, not used for judging
    ...
```

It runs in its own process, inside a sandbox: no network, read-only code, one writable directory (`ctx.out`). Its only
link to the world is the episode's socket. The world server judges the episode on the true state, which the program
never sees. Robot time is limited per episode (60 s); after that, motion is refused. Wall time is limited too (900 s).

**In this run there is no skill library.** Nothing is written yet: you write the skill itself (how to find the
object, choose a grasp, approach, grasp, check and lift, recover) as code, from the robot interface, the cameras and
their depth, the segmentation model and the vision-language model below. The robot's own code is not importable from
your program (an import of it raises `ImportError`); the Python standard library, `numpy`, `cv2` (OpenCV) and `scipy`
are available, and your own modules in `helpers/`.

## `ctx`

| field | |
|---|---|
| `ctx.task` | `dict(kind="clutter_pick", instruction="Pick up the red tape roll and lift it.", object="red tape roll")` |
| `ctx.robot` | the robot (docs/robot.md): motion, cameras, joint / gripper / force state |
| `ctx.segmenter` | segmentation (docs/perception.md): `segment(rgb, phrase)`, `segment_points(rgb, pts, labels)` |
| `ctx.vlm` | when the run allows it: the vision-language model (Astra, gpt-6-astra), **one call per episode** (below) |
| `ctx.tools` | the robot's conventions and the camera geometry (below) |
| `ctx.log(what, **data)` | an event in the episode's `events.jsonl`, stamped with robot time |
| `ctx.save_image(name, rgb)` | a PNG in the episode's `program/` directory |
| `ctx.out` | the writable `program/` directory |
| `ctx.time_left()` | robot seconds left before motion is refused |

## `ctx.tools`

Conventions and geometry only; nothing here looks, plans or moves.

| | |
|---|---|
| `down(yaw)` | the hand's rotation pointing straight down, the fingers closing along the table direction `yaw` (rad); `yaw` and `yaw + pi` are the same grasp |
| `world(obs)` | `(P, ok)`: every pixel's 3D point in the world frame (m), `P[v, u]`, from the frame's depth and the camera pose the program believes (`obs["R"]`, `obs["eye"]`); `ok[v, u]` = the pixel has depth. Raw: the depth's noise, holes and flying pixels are all there |
| `project(obs, X)` | world points `X` ((3,) or (N, 3)) -> `(uv, z)`: their pixels in that frame and their depth along its optical axis |
| `FINGERTIP` | 0.0157 m: the fingertip point = TCP + `R[:, 2] * FINGERTIP` |
| `OPEN_W` | 0.04 m per finger: fully open (80 mm between the fingers) |
| `MIN_DEPTH` | 0.275 m: nothing nearer to a camera has depth |
| `Unreachable`, `RobotStop` | the robot's errors (docs/robot.md): catch them |

## The model call (`ctx.vlm`, when the run allows it)

```python
obs = ctx.robot.observe_frame("d415")                         # the scene camera's first frame
r = ctx.vlm.ask('Give the pixel [x, y] of the centre of the red tape roll as JSON {"target": [x, y]}.', [obs])
if r["ok"]:
    text = r["text"]                                          # parse it yourself; it can be wrong or malformed
```

- **One call per episode.** A second call is refused (`ok: False`). The images are 1–4 frames this episode's cameras
  delivered, as `observe_frame` dicts or their `rgb`.
- **Cost.** A short pointing answer costs about $0.006–0.011; a long answer (a plan) costs more. The run's model budget is in
  the brief. When it is used up, calls are refused, so the program must work without an answer.
- **Time.** Robot time does not advance while the model answers. Its wait is recorded separately, along with every
  request and reply, in the episode's `events.jsonl`.
- **A pixel to 3D:** `P, ok = ctx.tools.world(obs)`; then `P[v, u]` is the world point at pixel (u, v) if `ok[v, u]`
  holds (noisy depth: use a small neighbourhood).

## Writing it

Import your helpers as `from helpers.x import y`; the workspace root is on the path. Programs must not try to read the
simulator's state, the platform's files or files outside their workspace. The sandbox and the socket enforce this;
there is nothing else to reach. A version that imports the robot's own code, or reaches for it some other way
(`sys.modules`, `importlib`, `__globals__`, ...), is rejected.
