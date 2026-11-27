# Condition F: oracle-simulator practice with sparse external evaluation

D learns with a revisable approximate simulator. F practises directly in the target simulator, with the correct target
physics. Both write their own practice success checks, and both receive the same limited feedback of the independent
target judge (the same submissions, on the same development panel, as B, C and D). F's extra target-simulator practice
makes it an **oracle reference, not a target-interaction-matched comparison**.

| Allowances | Original | Expanded |
|---|---|---|
| Settings file (`xsim/configs/`) | `sweep_blocks_F_oracle_5x5_usd80_sim300.json` | `sweep_blocks_F_oracle_10x10_usd120_sim600.json` |
| Model expenditure after initialization | $80 | $120 |
| Practice interactions in the target simulator (executed scene checks counted) | 300 | 600 |
| Official development episodes (submissions x scenes) | 5 x 5 | 10 x 10 |
| Target-simulator interactions in all, at most | 325 | 700 |
| The same for B, C, D | 25 | 100 |
| Compared with | the 5 x 5 experiment | the 10 x 10 experiment |

F starts from the same initial program A as the experiment it is compared with (the snapshot of
`out/xsim/sweep_blocks/rep0`) and is judged on the same development panel (the first 5, or the first 10, scenes of the
frozen 20), in an experiment root of its own made with `xsim/fork_root.py`. Not run so far.

## What F may and may not do

| F may | F may not |
|---|---|
| choose its practice scenes: fresh ones, or its own places and turns of the dustpan, the brush and the three cubes, their colours, and the table's look (one of the ten development tables, or a plain colour) | change the things' geometry or physical properties, their number, the robot, its commands, the sensors or the physics; name a table of the final appearance test |
| write and revise its own `success(state)`, from the TRUE state of the practice scene (the queries of D's practice-world state: `pos`, `rot`, `start`, `start_rot`, `touching`, `speed`, `held`, `arm`) | see or change the official evaluator, its verdict on a practice episode, or its measurements (they are computed privately, in `private/oracle_practice/`, and stay unavailable to it) |
| read the true positions in a practice episode's log, and the scene as built | read the target's source, the development panel's files, or any final test configuration |
| submit to the official evaluator as B, C and D do, and read that feedback | get anything of the held-out evaluation |

The skill program itself, in practice as in every evaluation, has the documented robot interface and nothing else: it
runs in a sandbox of its own, apart from the simulator and apart from the self-check.

A practice episode ends when the program returns, when F's own `success(state)` says so, when the robot time is
spent, or on an error. The official verdict is not exposed to F and does not control practice termination.

## Fresh practice scenes, and how they differ from the development and test scenes

A fresh scene is drawn by `sweep_blocks/scene_gen.sample`, with the same generator the frozen sets were drawn with
(`make_panel.candidate` for the places and turns, `looks.draw` for the colours), from a random stream of its own. The
differences:

- **Not filtered by the scripted reference.** A development or test scene was kept only if the scripted sweeping
  reference solved it. Fresh practice scenes are not checked with it. Of the 375 unmirrored draws made for the frozen
  sets, 158 (42 %) were solved by the reference and 217 were not solved by the reference; "not solved by the
  reference" is a statement about that one scripted program, not that a scene is unsolvable. So more than half of
  F's fresh practice scenes are of a kind that is absent from the development and test sets.
- **Physical-validity checks instead.** A scene is built only if every thing lies on the table (20 mm from its edge),
  clear of the arms' bases and the camera stand's foot, and nothing overlaps anything (outlines 4 mm apart); after
  the simulator has let it settle, every thing must be within 5 mm of where it was asked to be and the dustpan level
  within 3 degrees. A raw draw that these rules refuse is drawn again: 12 of 600 raw draws (2 %), all for the dustpan's
  and the brush's outlines overlapping. The frozen sets had no such rule (all 20 development scenes pass it).
- **Tables.** Each fresh scene takes one of the ten development tables at random; in a frozen set every table comes
  equally often (in turn, shuffled).
- **No test shifts by default, but placements are free.** Fresh scenes are never mirrored and never have the larger
  or the heavier, slippery cubes of the test's shifted sets, and the tables of the appearance test cannot be asked
  for. Sizes and physics cannot be asked for at all. Placements can: F may ask for any valid arrangement, a
  left-right mirrored one included. It is told nothing of what the held-out sets contain.
- **Never a frozen scene.** The generator does not read the development or test files, and its stream
  (`[seed, 0x5EED]`) is not one they were drawn from.
