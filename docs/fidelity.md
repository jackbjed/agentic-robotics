# Simulator-fidelity conditions (design; condition B is what runs now)

All conditions use **one** environment: the same world server, physics, robot path, judge and layouts. Only
configuration changes. Every training condition is evaluated on the same held-out target distribution: test layouts
with the real objects' geometry, rendered with the condition's own colour mode unless stated. Without physical
experiments, results are **simulation generalization**, not sim-to-real transfer.

| | rendering | object geometry and physics | status |
|---|---|---|---|
| **A** | **Splat composite**, from panda_twin's `composite_server.py`. The background is the empty-table Gaussian splat (scene v6507), with the real arms cut out and MuJoCo shadows. The objects are their own Gaussians, cut from the scans, at their simulated poses. MuJoCo draws the arm on top by depth. The depth is still MuJoCo's, through the D415 model. | the twin's (as B) | **Feasible.** The compositor and the object splats exist (`objsplat_6522.pt`, splat_v6507; manifest group `splats_6522`), but the world server does not yet call a compositor. That needs our own instance (it uses the GPU). The user's running servers on :8132–:8137 are not used. |
| **B** | **Basic:** a plain MuJoCo render (flat colours, no textures) from the true camera pose. The depth goes through the D415 model. | the twin's primitive or procedural collision models, sized from the scans and tape measurements | **Running** (milestone 1) |
| **C** | basic (as B) | **Different objects that keep the relevant affordances.** For example, other rolls, boxes or blocks of similar size and graspable width, not the scanned set. The target must stay a graspable roll. | **Not implemented.** It needs a second model file with substitute objects, and layouts generated with them (panda_pick_rgbd's layout generator, vendored or re-run). |

## What actually changes, and what does not

- **Colour only, between A and B.** The geometry, physics, depth and judge are identical, so A–B differences isolate
  rendering.
- **The splat background in A is not full photorealism.** It is a static scan with rigid object Gaussians. Unscanned
  object interiors are drawn by MuJoCo. Scans of short objects sit low (aligned by 11–20 mm). There is no
  view-dependent lighting change from the arm, beyond MuJoCo's shadow ratio.
- **Depth is never photoreal.** It is MuJoCo geometry through the fitted noise model in all conditions.
- **Object dynamics** (friction, mass, contact stiffness) are the twin's in A and B. C changes shapes. Changes in
  dynamics should be studied separately, for example with friction or mass variations on the same objects, so they
  are not confounded with appearance.
- **Perception backend.** With the `oracle` segmenter, colour hardly matters to the skill: masks are exact. A–B
  differences in skill development only mean something with SAM3 (or another real model) as the segmenter. Fidelity
  experiments therefore need the SAM3 backend first.

## Order of work

1. Wire SAM3 as a perception backend (our own server instance).
2. Add the compositor to the world server as a colour mode (A).
3. Build condition C's object set and layouts.
4. Run the same learning protocol per condition.
5. Evaluate all conditions on one held-out set.
