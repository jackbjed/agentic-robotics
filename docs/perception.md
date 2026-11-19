# Perception: cameras and segmentation

## Cameras (`ctx.robot.observe_frame(cam)`)

Two Intel RealSense D415s deliver 640×480 colour, with depth aligned to colour:

- `"d415"`, the **scene camera**, fixed, across the table, facing the robot. It sees the whole work area at about
  1.1–1.4 m, at a low angle. The robot's arm can hide parts of the table from it.
- `"L_wrist"`, the **wrist camera**, on the hand, looking straight down past the fingers. It sees the area under the
  hand (minimum range 0.275 m), sharper and from above.

**Depth: realistic and noisy, in every condition.** Programs get panda_twin's D415 model, **fitted to real frames**:
- stereo noise;
- holes on dark objects and on the black table;
- edge fattening and stereo shadows;
- flying pixels;
- the blind left band;
- 0.275 m minimum range;
- ±0.5 % depth scale.

On test L0 #10, the scene camera's valid depth fell from 74 % of pixels (ideal) to 56 %, with a median error of
15 mm. The wrist camera's fell from 100 % to 80 %, with a median error of 1.6 mm. Each episode also draws a **camera
calibration error** (scene 5 mm / 1°, wrist 3 mm / 1°). `observe_frame` returns the NOMINAL pose (`R`, `eye`), so 3D
points computed from a frame carry this error, as on the real robot. `F.world(obs)` (`ctx.library.F`) gives
per-pixel 3D points in the world frame, using the believed pose, plus a validity mask.

**Colour** depends on the condition (docs/fidelity.md):
- plain MuJoCo rendering (B, C);
- the splat composite (A): real scanned table and objects, MuJoCo's arm.

## Segmentation (`ctx.segmenter`)

| call | returns |
|---|---|
| `segment(rgb, phrase)` | `[(mask, score)]`, best first, at most 6: the masks matching the text `phrase` in that frame |
| `segment_points(rgb, pts, labels)` | `[(mask, score)]`: SAM3's 3 candidate masks for clicks at pixels `pts` (`[[x, y], ...]`); `labels` 1 = on the thing, 0 = not on it |

`rgb` must be a frame this episode's cameras delivered (`observe_frame(...)["rgb"]`). The backend is set by the
experiment, not the program.

### `sam3`: the real model (the default from milestone 2 on)

SAM3 (Meta's Segment Anything 3, `facebook/sam3`) is the same model and settings the real robot program uses:
- text prompts with a detection threshold of 0.1;
- the image enlarged to 1008 px on its long side;
- at most 6 masks, sorted by score.

It runs on our own server; programs reach it only through the world. Using it well:
- **Any text works.** The phrase is free text: a name ("red tape roll"), a colour plus shape ("red cylinder", "red
  ring"), or a category ("small object", which returns the separate things on the table). Different phrases can work
  better in different views. An object seen from the side (scene camera) and from above (wrist camera) may need
  different words.
- **Scores are confidences, not truths.** SAM3 can:
  - miss the object (empty list, or only low scores);
  - return part of it;
  - return **another object**: a similar colour, the other roll, a red part of a different object such as a red
    handle or a red bottle base.

  Check masks against what you expect (size, place, depth) before acting on them. Consider several masks, not only
  the first.
- **Clicks** (`segment_points`) segment a thing you have located some other way, for example a blob in the depth
  image, or the thing in the way of a grasp, without naming it.
- **Each call takes about 0.3–0.5 s of wall time.** Robot time does not advance while it runs; it is recorded
  separately as perception time. Calls are not budgeted, but the episode's wall limit applies.

### `oracle`: milestone 1 only

Exact masks over the object's visible pixels: a stand-in, not a model. It answers only to the exact names of the
objects, plus "small object". Results with it are upper bounds on perception.

## What perception never gives

Object poses, the target's position, or anything about objects outside the frame. Everything comes from the frames
the cameras delivered.
