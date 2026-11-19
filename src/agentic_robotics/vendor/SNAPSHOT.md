# vendor/: frozen copies, never edited

Byte-identical copies made 2026-11-18 of the code this platform runs, so that results do not depend on the original
projects (which stay unchanged: panda_twin is the reference implementation). Imported only through
`agentic_robotics.sim.twin` (environment flags first). Large and third-party assets are not here: see
`assets/manifest.yaml` (the Panda meshes from deoxys, the finger mesh, the layout banks; linked or copied with
sha256 checks by `scripts/link_assets.py`).

The model XML is panda_pick_rgbd's vendored copy of panda_twin's `model/twin_6522_wedge.xml` (its only change there:
mesh paths relative, meshdir="assets"; the compiled model was checked identical on 2026-11-15). The meshes of its own
objects (assets/twin_6522/) are copied; the Panda and finger meshes come from the manifest.

| file | sha256 (16) | source (root: path) | identical to the source on 2026-11-18 |
|---|---|---|---|
| panda_twin/code/clutter_bench.py | 88abbc4545916e82 | panda_twin: code/clutter_bench.py | yes |
| panda_twin/code/d415_model.py | d0a90aab90f16797 | panda_twin: code/d415_model.py | yes |
| panda_twin/code/press_skill.py | c4eccb3c1bb96e0d | panda_twin: code/press_skill.py | yes |
| panda_twin/code/real/calib.json | d91b3ae633dd99bd | panda_twin: code/real/calib.json | yes |
| panda_twin/code/real/panda_fk.py | a86bf77d7eac5bd1 | panda_twin: code/real/panda_fk.py | yes |
| panda_twin/code/real/real_box.py | 651c906dbc4728a6 | panda_twin: code/real/real_box.py | yes |
| panda_twin/code/real/real_sim.py | 4546303fced1c2d3 | panda_twin: code/real/real_sim.py | yes |
| panda_twin/code/real/recorder.py | 9a2ac673b2ed0b5d | panda_twin: code/real/recorder.py | yes |
| panda_twin/code/real/rig.json | 3b4057e9142b7908 | panda_twin: code/real/rig.json | yes |
| panda_twin/code/real/track.py | 18b408888ca4bcfe | panda_twin: code/real/track.py | yes |
| panda_twin/code/rl/clutter_env.py | 13467abe4e23246d | panda_twin: code/rl/clutter_env.py | yes |
| panda_twin/code/rl/oracle.py | 01d0e560ec92c8e9 | panda_twin: code/rl/oracle.py | yes |
| panda_twin/code/stack_bench.py | fcfdb8d2f1fadc58 | panda_twin: code/stack_bench.py | yes |
| panda_twin/code/task_bench.py | 9f4377811f0df8fd | panda_twin: code/task_bench.py | yes |
| panda_twin/code/task_box.py | 4c06b1ccd6a16535 | panda_twin: code/task_box.py | yes |
| panda_twin/code/task_fast.py | fbe03ad443311dfd | panda_twin: code/task_fast.py | yes |
| panda_twin/model/twin_6522_wedge.xml | dfb9b76856c9fc10 | panda_pick_rgbd: vendor/panda_twin/model/twin_6522_wedge.xml | yes |
| panda_twin_tracking/astra_skills_real.py | 5900f528cd5e35d7 | panda_twin_tracking: astra_skills_real.py | yes |
| panda_twin_tracking/track_run.py | 51abc7c872a9aa6b | panda_twin_tracking: track_run.py | yes |
| services/sam3_server.py | d4dac8ecbc57d2ee | dexteleop: dexteleop-vla/twin/sam3_server.py | yes |
| services/sam3_points_server.py | 84f59a47f3e33c23 | panda_twin: code/sam3_points_server.py | yes |
| services/composite_server.py | 6149daf70634ae95 | panda_twin: code/composite_server.py | yes |
| services/splat_util.py | 3fe1ede0b184a91d | panda_twin: code/splat_util.py | yes |

The services/ files run as our OWN server processes (bin/ar services; configs/services.yaml) with existing
environments used read-only (SAM3: the sam3 env, transformers + facebook/sam3; compositor: the twin env, gsplat).
