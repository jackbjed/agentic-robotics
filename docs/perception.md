# Perception: cameras and segmentation

## Cameras (`ctx.robot.observe_frame(cam)`)

Two Intel RealSense D415s deliver 640×480 colour, with depth aligned to colour:

- `"d415"`, the **scene camera**, fixed, across the table, facing the robot. It sees the whole work area at about
  1.1–1.4 m. The robot's own arm can hide parts of the table from it.
- `"L_wrist"`, the **wrist camera**, on the hand, looking down past the fingers. It sees the area under the hand
  (minimum range 0.275 m).

**Depth.** Programs get panda_twin's D415 model, **fitted to real frames on 10-27** (`d415_model.py`):
- stereo noise;
- holes on dark objects and on the black table;
- edge fattening (4 px) and stereo shadows;
- flying pixels;
- the blind left band;
- 0.275 m minimum range;
- ±0.5 % depth scale.

Each episode also draws a **camera calibration error**: 5 mm / 1° for the scene camera and 3 mm / 1° for the wrist
camera, in random directions. `observe_frame` returns the NOMINAL pose (`R`, `eye`), so a 3D point computed from a
frame carries this error, as on the real robot. Not modelled: the scene camera's lateral and yaw errors were never
checked against reality, and its scale error at 1–1.5 m is not modelled.

**Colour.** In the default fidelity condition (B, `docs/fidelity.md`) colour is a plain MuJoCo render from the true
camera pose. It is not photoreal; the episode's light scaling is applied. `colour_noise: true` adds the d415 preset's
colour variation, which is sensor-inspired and not calibrated.

`F.world(obs)` (`ctx.library.F`) turns a frame into per-pixel 3D points in the world frame, using the believed pose,
plus a validity mask.

## Segmentation (`ctx.segmenter`)

| call | returns |
|---|---|
| `segment(rgb, phrase)` | `[(mask, score)]`, best first, for objects matching `phrase` in that frame. `rgb` must be a frame this episode's cameras delivered. |
| `segment_points(rgb, pts, labels)` | `[(mask, score)]` at clicked pixels (`labels` 1 = on the object). |

**Backend in milestone 1: `oracle`**, a stand-in for SAM3, not a perception model. Masks are exact over the object's
visible pixels in that very frame. It returns image masks only, never poses or positions:
- the target's name gives its mask with score 0.92;
- another object's name gives that object's mask, score 0.92;
- `"small object"` gives every visible object, score 0.6;
- masks under 20 px are dropped.

On the robot, the same calls go to SAM3 (text :8131, clicks :8134). SAM3 makes mistakes: wrong objects, partial
masks, low scores on overexposed colours. Results with the oracle are therefore upper bounds on perception. Switching
to SAM3 is a configuration change (`configs/tasks/*.yaml` `perception:`); it needs our own SAM3 server, which is not
set up yet.

Names the segmenter knows (oracle): "red tape roll", "yellow tape roll", "white tape roll", "black block with
holes", "coiled black cable", "screwdriver", "glue bottle", "sticky note pad", plus "small object".
