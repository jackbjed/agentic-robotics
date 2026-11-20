# Your own simulator (`sim/`)

You build the simulator you practise in. The robot is given: your simulator runs the same robot model, motion
interface (`ctx.robot`, docs/robot.md), limits, timing, segmentation model and success checks as the real world. You
supply everything else: the scene, how it varies from episode to episode, and how the cameras' depth behaves. Nothing
of the real scene is given to you: measure it in the real world.

## What is given (the robot kit)

- **The left arm** (`L_` bodies) with its gripper and the wrist D415 camera (`L_wrist`), the **right arm** (`R_`,
  parked where it always is), the **frame the arms stand on**, and the **scene D415** (`d415`) on its mount. The cameras
  sit at the rig's calibrated poses, the ones `observe_frame` reports in the real world.
- The **physics options** (time step 0.002 s, implicit-fast integrator, elliptic friction cones, impratio 10) and the
  arm's controller, limits and force estimate. These are fixed.
- **Nothing else**: no table, no floor, no lights, no background (black), no objects, no box.

The world frame is the rig's (docs/robot.md): metres and radians, z up, x away from the robot. Its origin and the table
top's height come from the rig's calibration: check them against the depth you observe.

## Files

```
sim/
  sim.yaml      what your scene is (below)
  scene.xml     your scene: an MJCF file (MuJoCo 2.3.7)
  layouts.py    sample(task, rng): how the tasks' scenes vary
  assets/       meshes (.obj, .stl) and textures (.png) that scene.xml names (any sub-folder of sim/ works)
```

### `sim.yaml`

```yaml
scene: scene.xml
table: top                      # the geom (or a list of geoms) things rest on
objects:                        # the names the tasks use -> your bodies
  blue mug: mug                 # (an example: declare the real world's objects under ITS names, docs/tasks.md)
  green ball: ball
box:                            # needed for place-box and close-box
  name: grey crate              # (in this run: "white box")
  body: crate                   # its body
  lid_joint: crate_lid          # the lid's hinge: 0 = closed, opening positive (rad)
  inside: [[-0.10, 0.10], [-0.08, 0.08], [0.0, 0.07]]   # its interior in the box body's frame (m): "inside its walls"
sensors:                        # optional: your model of the cameras (default: exact depth, exact poses)
  depth:
    noise: [0.0005, 0.002]      # per-pixel Gaussian noise, sigma = a + b * z^2 (m)
    dropout: 0.01               # fraction of pixels without depth
    edge_holes_px: 2            # no depth within this many pixels of a depth step (> ~2 cm between neighbours)
    dark_holes: {below: 0.15, p: 0.6}   # where the colour's brightness (0-1) is below `below`: no depth with prob. p
    scale: 0.004                # per-episode depth scale error, uniform +- this fraction
  calibration_error:            # per episode: each camera's TRUE pose moved by up to (m, deg) from the one it reports
    d415: [0.005, 1.0]
    L_wrist: [0.003, 1.0]
  colour: {gain: [0.9, 1.1], noise: 2.0}   # per-episode exposure gain, per-pixel noise (sigma, 0-255)
```

The success checks use your declarations: the box's interior (`inside`: the object's centre of mass must be within it),
the lid's hinge (closed: < 8°), the box upright (its body's z axis within 10° of vertical), the table (an object whose
centre of mass is 5 cm below the top of your table geoms is "off the table").

### `scene.xml`

A `<mujoco>` file whose sections may be `asset`, `default` (named classes only), `worldbody`, `contact`, `equality`,
`tendon`, and `visual` (only `<headlight>`). Not allowed: `compiler`, `option`, `include`, `keyframe`, `actuator`
(the physics options and the robot are the kit's). Angles are in radians. Names may not start with `L_`, `R_`, `obj_`,
`d415`, `frame_` or be `table_top`. Files (meshes, textures) are named relative to `sim/` and must lie inside it.

- **Every object and the box: a direct child of `<worldbody>` with a `<freejoint/>`** (it is placed and moves freely).
  Its body frame is the one your layouts place: put its origin where you want `[x, y, yaw]` to mean something (for
  example, the footprint's centre at the bottom).
- **The box's lid:** a child body of the box with a hinge joint, 0 = closed.
- **Mass and contact:** give masses (`mass=` on geoms, or `density=`; MuJoCo's default density is 1000 kg/m³) and
  friction (`friction="sliding torsional rolling"`). Thin walls and small objects may need `solref`/`solimp` or
  `margin` care: check them with `sim check` and in practice.
- **Shapes:** primitives (box, cylinder, capsule, sphere, ellipsoid) or meshes (MuJoCo collides with a mesh's convex
  hull: build a hollow or concave thing from several geoms or meshes).
- **Looks:** materials and `rgba`, textures (for example a photo of a surface, `<texture type="2d" file="assets/x.png"/>`
  in a `<material texture="..."/>`), lights (`<light>` in the worldbody; the kit has none, only MuJoCo's headlight).

A minimal example (not the real scene):

```xml
<mujoco>
  <asset><material name="wood" rgba="0.55 0.4 0.25 1"/></asset>
  <worldbody>
    <light name="lamp" pos="0 0 2" dir="0 0 -1" diffuse="0.6 0.6 0.6"/>
    <geom name="top" type="box" size="0.5 0.5 0.02" pos="0 0 -0.02" material="wood"/>
    <body name="mug" pos="0.3 0.3 0"><freejoint/>
      <geom type="cylinder" size="0.04 0.05" pos="0 0 0.05" rgba="0.1 0.2 0.8 1" mass="0.3"/>
    </body>
    <body name="ball" pos="0.3 0.4 0"><freejoint/>
      <geom type="sphere" size="0.03" pos="0 0 0.03" rgba="0.1 0.7 0.2 1" mass="0.05"/>
    </body>
    <body name="crate" pos="0.4 0.3 0"><freejoint/>
      <geom type="box" size="0.1 0.08 0.002" pos="0 0 0.002" rgba="0.5 0.5 0.5 1"/>
      <geom type="box" size="0.002 0.08 0.035" pos="0.1 0 0.035" rgba="0.5 0.5 0.5 1"/>
      <!-- ... the other walls ... -->
      <body name="lid_panel" pos="0 -0.08 0.07">
        <joint name="crate_lid" type="hinge" axis="1 0 0" range="0 3"/>
        <geom type="box" size="0.1 0.08 0.002" pos="0 0.08 0" rgba="0.5 0.5 0.5 1" mass="0.05"/>
      </body>
    </body>
  </worldbody>
</mujoco>
```

### `layouts.py`

```python
def sample(task, rng):          # task: "place-box" | "close-box" | "clutter-pick-any"; rng: numpy Generator
    return dict(
        target="blue mug",      # the object the task names (close-box: the box's name)
        place={"mug": [x, y, yaw],                     # resting on the table, upright, turned by yaw about z
               "crate": [x, y, z, qw, qx, qy, qz]},    # or a full pose (a quaternion)
        joints={"crate_lid": 1.9})                     # e.g. the lid's opening (rad)
```

Only the bodies in `place` are on the table (the others are parked out of sight). An episode in your simulator: the
layout applied (each `[x, y, yaw]` body resting on your table's top), the arm at home, 1 s of settling, then your
skill. The task told to the program comes from the target (its declared name, "Put the ... into the white box." ...).
Make the layouts vary as the real world's do (how many objects, how close, how turned, the lid's opening, where the box
stands): your skills will be tested on real-world layouts you have not seen. `sample` runs in the sandbox (numpy, the
standard library, your own files in `sim/`).

## The commands

- `real look --task T [--idx I] [--wrist "x,y,z[,yaw];..."]`: a real-world scene: both cameras at the arm's home pose,
  then the wrist camera with the hand pointing down at each TCP position (up to 8). One real-world episode.
- `real run explore/NAME.py --task T [--idx I] [--n K]`: your own program in the real world, with the skills' API
  (`run(ctx)`): look from anywhere, touch, push, open and close the lid, lift things (the gripper's opening and the arm's
  force estimate tell you sizes and weights). Save what you want to `ctx.out` (it appears in `program/`).
- `py analysis/NAME.py [args]`: your script in the sandbox, from the workspace's root: it reads anything in the workspace
  and writes `sim/` and `analysis/` (numpy, scipy, cv2, PIL, mujoco, yaml; no robot, no network; 10 min).
- `sim check [--n 3]`: builds your simulator (sim.yaml, scene.xml with the robot), samples n layouts per task, places and
  settles them, and reports what moved in a further second of settling, anything below the table, physics warnings,
  the deepest interpenetration; previews (your scene and wrist cameras at home, colour and depth) in
  `practice/checks/NNN/`.
- `sim render --layout FILE.yaml --like real_world/NNN/EE/observations/MMM_<cam>.json`: your simulator with that layout
  (the same keys as `sample`'s answer, as YAML: your estimate of where things stood in that real frame), the arm and
  gripper as in that frame, from the camera pose the program believed: `practice/renders/NNN/compare.png` (real |
  yours | overlay; real depth | yours | the difference) and the depth difference's statistics.
- `sim ready`: checks that your simulator builds and settles, saves it in `sessions/<session>/sim/` (and with the
  session's records), and switches the session to practice: the real world closes until the next session. You may
  still fix `sim/` afterwards (run `sim ready` again to save the fixed version); practice and the end-of-session
  development runs use `sim/` as it is then.
- `run --task T [--n 6] [--seed S] [--observations all]`: practice episodes in your simulator (`practice/runs/NNN/`).

## Real-world records

`real_world/NNN/EE/observations/MMM_<cam>_rgb.png`, `_depth.png` (16-bit, mm, 0 = no depth), `_depth_vis.png`, and
`MMM_<cam>.json`: `K = [W, H, fx, fy, cx, cy]`, `R` (3×3, camera to world, OpenCV axes: x right, y down, z forward),
`eye` (the camera's position), `q` (the arm's joints), `width` (the gripper's opening, m), `t` (robot s). A pixel
(u, v) with depth z is the world point `R @ [(u - cx) / fx * z, (v - cy) / fy * z, z] + eye` (with the real cameras'
calibration error: the pose is the one the robot believes). `keyframes.png` / `video.mp4`: both robot cameras, two
frames a second. `episode.json`: the task as told, robot time, your program's result or error.
