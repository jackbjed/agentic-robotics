# Practice episodes are not reproducible when they use segmentation: where the variation comes from

Investigated on 2026-11-27 with frozen inputs (a practice world file, a seed, a program), apart from the running
sessions: nothing of theirs was changed. Scripts: `out/audit_2026-11-27/practice_nondeterminism/` (local archive; the
inputs, the rendered images and the service's answers are kept there with a manifest).

**Observation.** The same program, practice world and seed, run three times, gave different segmentation scores from
the first call on (the brush at t = 0: 0.21, 0.19, 0.20) and different outcomes (one run solved the scene, two did
not). The same kind of episode without segmentation was identical run to run (log, true positions, the world's
check), and target (ManiSkill) episodes with segmentation were identical run to run on the scenes tested.

**Diagnosis, as far as the tested configurations support it: the practice simulator's colour render of the head
camera, not the segmentation service.** It rests on one practice world, one seed, one machine and GPU, and the image
sizes and settings listed below; other scenes, seeds or machines were not tested.

| Checked | Result |
|---|---|
| The scene a seed gives (its MJCF), in 9 processes | identical |
| The bodies' positions after settling, to 1e-9 m | identical |
| Depth images of all three cameras | identical |
| Colour images of the two wrist cameras | identical |
| Colour image of the HEAD camera | differs: 1 to 6 pixels of 307,200, by one grey level, between processes and between two renders in one process |
| The head camera's raw render (747 x 480, before the resize) rendered 12 times from one state | 11 distinct images; up to 14 pixels differ, by one level |
| The resize (OpenCV INTER_AREA) of one raw image, 5 times, many threads and one | identical |
| The same raw render with multisampling off (`offsamples = 0`) | identical every time |
| ... with shadows off | identical every time |
| ... at 640 x 480 instead of 747 x 480 (multisampling and shadows on) | identical every time |
| The segmentation service on ONE frozen image: 8 calls in a row, 8 calls four at a time, 4 calls with other images in between; a practice image and a target image; four phrases | identical scores (to the last digit) and identical masks every time |
| The segmentation service on 5 renders of the same practice scene (the few-pixel differences above) | different: 5 distinct mask sets; scores move in the third decimal (the dustpan 0.436 to 0.443; a low-score cube 0.314 to 0.320); one mask's area 171 instead of 194 pixels |

In these tests: the head camera is rendered wider than the image (747 pixels, for its non-square lens) with 4x multisampling and
shadows; under those three together MuJoCo's offscreen render is not bit-reproducible (a handful of pixels, one grey
level). The segmentation model (bfloat16 autocast, a 0.1 score threshold) is deterministic for a given image but turns
those pixels into slightly different scores and masks; a program that thresholds or ranks them takes another branch,
and the episode diverges.

**Not the cause in these tests:** the service's configuration (one instance, `--conf 0.1`, one lock around the model, no cache, no
seed anywhere), its state (answers do not depend on what was asked before, nor on concurrency), the scene generator,
the physics.

**What it means for the experiments.** A practice success rate over a set of seeds is noisier than the number of
seeds suggests, for conditions C and D alike; a seed does not pin an episode down. The target's renders were
bit-reproducible in everything tested (three identical runs of two development scenes; every judged success of the
first run replayed identically), so development feedback and held-out evaluation are not touched by this.

**Not changed.** The current rendering is kept as it is, for the running sessions and in the fixes branch.

**For future runs.** Before small differences between practice scores are interpreted, either test a deterministic
rendering configuration (in the tests above each of these gave bit-identical renders: multisampling off for the
offscreen render; shadows off; the head camera rendered at the image's own width) and confirm that repeated episodes
then agree, or evaluate repeated executions of the same episodes and report their spread. Each rendering change
alters what a practice image looks like, so it belongs with a new run, not with the ones made so far.
