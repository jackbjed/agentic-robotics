THE SKILL TO MAKE: put the cylinder on the stick.

On the table there are a cylinder with a hole through it and a stand with one vertical stick. Pick up the
cylinder and thread it onto the stick, so that the stick passes through its hole, and let go of it: at the end
the cylinder must be on the stick, the robot not touching it, and the stand upright. Then open the gripper and
return it to its initial pose. The hole can be as little as 1.5 mm wider than the stick.

The program should be robust to:
- different cylinders and stands, with different physical properties: visual appearances, shapes, sizes,
  colours, frictions, densities and other physical parameters;
- different spatial locations and configurations, and different spatial arrangements of the objects;
- the height at which the cylinder and the stand are;
- the presence of distractors;
- different table heights.

You receive no external test cases and no assets: no cylinders and no stands, only the robot, its cameras and
the table. With worlds of your own you create your own test cases and assets to make the skill.

Versions of this skill have now been run on the real robot; the episodes are yours to study. Specialize the skill to this real table and to this stand and its stick, the ones in the episodes: it no longer has to handle other stands or sticks. It must still handle different cylinders, and different heights of the cylinder and of the stand. Make it reliable on the real robot first, then as fast as you can in the rig's time.

Nothing is given for it but the robot, its cameras and the table: no objects, no assets, no test cases, no task in
the simulator. You make all of that yourself, as WORLDS (described below): what is on the table (bodies, soft
bodies, loose things, marks), the task's text, and the check that says whether an episode did the task. Then you
write the skill: one program run(robot, task) that does the task in your worlds' episodes from the cameras and the
arm's own measurements only, as every program here. The robot is the left arm alone; the other arm is not to be
used, and the simulator refuses it.

This session continues an earlier one, which ended with v38 as its result. You keep its versions (read_program),
its scenes and all its episode results (read_output, view_images), and the notes you wrote in it.

The versions:
v0 (42 lines): probe: segmentation phrases, truth | practice: scene s1@w0 FAIL 0.0 s, scene s2@w0 FAIL 0.0 s, scene s3@w0 FAIL 0.0 s, scene s4@w0 FAIL 0.0 s, scene s1@w1 FAIL 0.0 s, scene s2@w1 FAIL 0.0 s, scene s3@w1 FAIL 0.0 s, scene s4@w1 FAIL 0.0 s, scene s1@w2 FAIL 0.0 s, scene s2@w2 FAIL 0.0 s, scene s3@w2 FAIL 0.0 s, scene s4@w2 FAIL 0.0 s, scene s1@w3 FAIL 2.1 s, scene s2@w3 FAIL 2.1 s, scene s3@w3 FAIL 2.1 s, scene s4@w3 FAIL 2.1 s
v1 (43 lines): probe2: wrist survey from z=560, segmentation phrases | practice: scene s1@w3 FAIL 4.8 s, scene s2@w3 FAIL 4.8 s, scene s3@w3 FAIL 4.8 s, scene s4@w3 FAIL 4.8 s
v2 (115 lines): mech dev: truth-based positions; squeeze stick and cylinder, touch-down, insert | practice: scene s1@w3 ok 67.2 s, scene s3@w3 ok 69.3 s
v3 (432 lines): v3: full skill: wrist survey (height residual + SAM), close-ups, squeeze stick/cyl, insert w/ search | practice: scene s1@w3 FAIL 7.0 s, scene s2@w3 FAIL 52.0 s, scene s3@w3 FAIL 65.7 s, scene s4@w3 FAIL 70.0 s, scene s5@w3 FAIL 0.0 s, scene s6@w3 FAIL 54.7 s, scene s7@w3 ok 70.8 s, scene s8@w3 ok 82.2 s
v4 (500 lines): v4: ring verified by close-up hole, stick by local rise; artifact masking | practice: scene s1@w4 FAIL 13.3 s, scene s2@w4 FAIL 182.0 s, scene s3@w4 FAIL 13.0 s, scene s4@w4 FAIL 13.5 s, scene s5@w4 ok 79.6 s, scene s6@w4 FAIL 13.9 s, scene s7@w4 ok 74.8 s, scene s8@w4 FAIL 14.7 s
v5 (513 lines): v5: debug stick detection | practice: scene s1@w4 FAIL 13.3 s, scene s3@w4 FAIL 13.0 s
v6 (513 lines): v6: stick opening disc 44mm, annulus 18-32 p60; ring gripped low | practice: scene s1@w4 ok 68.8 s, scene s2@w4 FAIL 183.0 s, scene s3@w4 ok 70.1 s, scene s4@w4 FAIL 13.5 s, scene s5@w4 ok 108.8 s, scene s6@w4 ok 95.9 s, scene s7@w4 ok 74.8 s, scene s8@w4 ok 101.3 s
v7 (714 lines): v7: survey from (-420,60,600); stick squeezed low a-b-a; put-back & retry when the search fails | practice: scene s1@w4 ok 76.9 s, scene s2@w4 ok 86.4 s, scene s3@w4 ok 84.9 s, scene s4@w4 ok 80.9 s, scene s5@w4 ok 90.9 s, scene s6@w4 ok 98.6 s, scene s7@w4 ok 82.7 s, scene s8@w4 ok 92.8 s, scene s9@w4 ok 149.9 s, scene s10@w4 ok 80.2 s, scene s11@w4 FAIL 22.5 s, scene s12@w4 ok 117.9 s, scene s13@w4 ok 336.2 s, scene s14@w4 FAIL 85.4 s, scene s15@w4 ok 89.4 s, scene s16@w4 ok 87.0 s
v8 (23 lines): v8: squeeze test on fixed / heavy sticks | practice: scene s1@w5 FAIL 57.8 s
v9 (774 lines): v9: iterative centring squeeze (arm compliance), grasp consistency check + re-look, ring hole by centre depth | practice: scene s9@w4 ok 97.8 s, scene s11@w4 FAIL 22.5 s, scene s12@w4 ok 81.0 s, scene s13@w4 ok 100.3 s, scene s14@w4 ok 112.7 s, scene s17@w4 ok 80.5 s, scene s18@w4 ok 104.0 s, scene s19@w4 ok 81.4 s
v10 (865 lines): v10: finger clearance (height grid) chooses closing dirs; better hole test + weak fallback | practice: scene s1@w6 ok 83.6 s, scene s2@w6 ok 93.1 s, scene s3@w6 FAIL 41.7 s, scene s4@w6 ok 81.4 s, scene s5@w6 FAIL 519.7 s, scene s6@w6 ok 102.3 s, scene s7@w6 ok 85.5 s, scene s8@w6 ok 95.0 s
v11 (897 lines): v11: stick candidates verified by pedestal + round widths; orientation fallbacks; robust clearance; faster search | practice: scene s3@w6 FAIL 50.2 s, scene s5@w6 ok 145.4 s, scene s9@w6 ok 83.8 s, scene s10@w6 ok 82.9 s, scene s11@w6 ok 132.4 s, scene s12@w6 ok 80.2 s, scene s13@w6 FAIL 61.4 s, scene s14@w6 ok 108.5 s
v12 (925 lines): v12: squeeze_at tries other closing angles when a move/turn is refused | practice: scene s3@w6 ok 82.5 s, scene s13@w6 FAIL 66.8 s, scene s15@w6 ok 102.9 s, scene s16@w6 ok 107.9 s, scene s17@w6 FAIL 77.0 s, scene s18@w6 ok 104.4 s, scene s19@w6 ok 90.7 s, scene s20@w6 ok 94.8 s
v13 (926 lines): v13: judge squeeze width only once centred | practice: scene s13@w6 ok 109.8 s, scene s17@w6 FAIL 77.0 s
v14 (929 lines): v14: blocked descent -> next closing angle | practice: scene s17@w6 ok 84.0 s, scene s1@w6 ok 89.9 s, scene s2@w6 ok 114.2 s, scene s4@w6 ok 95.6 s, scene s5@w6 ok 141.8 s, scene s6@w6 ok 113.0 s, scene s7@w6 ok 85.5 s, scene s8@w6 ok 103.1 s, scene s9@w6 ok 83.8 s, scene s10@w6 ok 205.7 s, scene s11@w6 ok 132.4 s, scene s12@w6 ok 80.2 s, scene s14@w6 ok 108.5 s, scene s15@w6 ok 102.9 s, scene s16@w6 ok 107.9 s, scene s18@w6 ok 104.4 s, scene s19@w6 ok 90.7 s, scene s21@w6 ok 93.9 s, scene s22@w6 ok 93.4 s, scene s23@w6 ok 91.0 s, scene s24@w6 FAIL 102.2 s, scene s25@w6 ok 100.6 s, scene s26@w6 ok 158.5 s, scene s27@w6 ok 131.0 s, scene s28@w6 ok 91.0 s
v15 (939 lines): v15: correct the loaded arm's sag over the stick (closed loop on measured TCP) | practice: scene s24@w6 ok 94.9 s, scene s29@w6 ok 115.9 s, scene s30@w6 ok 92.9 s, scene s31@w6 ok 88.0 s, scene s32@w6 ok 103.7 s, scene s26@w6 ok 158.6 s, scene s27@w6 ok 134.5 s, scene s10@w6 ok 83.4 s, scene s33@w6 ok 110.9 s, scene s34@w6 ok 102.2 s, scene s35@w6 ok 88.9 s, scene s36@w6 ok 85.6 s, scene s37@w6 ok 103.6 s, scene s38@w6 ok 105.4 s, scene s39@w6 ok 85.2 s, scene s40@w6 ok 109.8 s, scene s1@w7 ok 83.5 s, scene s2@w7 ok 123.4 s, scene s3@w7 ok 101.2 s, scene s4@w7 FAIL 15.3 s, scene s5@w7 ok 88.9 s, scene s6@w7 ok 138.7 s, scene s7@w7 ok 83.0 s, scene s8@w7 ok 112.6 s
v16 (944 lines): v16: stick candidates: drop ring-wall ones and duplicates before taking the top 7 | practice: scene s4@w7 ok 83.8 s, scene s9@w7 ok 93.9 s, scene s10@w7 ok 86.6 s, scene s11@w7 ok 93.3 s, scene s12@w7 ok 80.0 s, scene s13@w7 ok 100.4 s, scene s14@w7 ok 104.0 s, scene s15@w7 ok 104.5 s, scene s16@w7 ok 93.1 s, scene s17@w7 ok 82.1 s, scene s18@w7 ok 104.9 s, scene s19@w7 ok 95.1 s, scene s20@w7 ok 92.2 s, scene s21@w7 ok 121.5 s, scene s22@w7 ok 81.4 s, scene s23@w7 ok 113.8 s, scene s24@w7 ok 84.8 s, scene s25@w7 ok 123.2 s, scene s26@w7 ok 95.0 s, scene s27@w7 FAIL 323.1 s, scene s28@w7 FAIL 328.2 s, scene s29@w7 ok 100.6 s, scene s30@w7 ok 99.6 s, scene s31@w7 ok 79.7 s
v17 (954 lines): v17: stricter hole test; stick candidates ranked by plate fraction; stick must be round (1.5) and fit the seen hole | practice: scene s27@w7 ok 97.9 s, scene s28@w7 ok 116.3 s, scene s32@w7 ok 104.7 s, scene s33@w7 ok 127.7 s, scene s34@w7 ok 93.9 s, scene s35@w7 FAIL 116.2 s, scene s36@w7 ok 101.1 s, scene s37@w7 ok 89.5 s
v18 (967 lines): v18: finish(): lift clear, go to the start pose by a straight move, then home | practice: scene s35@w7 ok 131.3 s, scene s38@w7 ok 123.5 s, scene s39@w7 ok 108.6 s, scene s40@w7 ok 123.4 s, scene s41@w7 ok 115.6 s, scene s42@w7 ok 110.5 s, scene s43@w7 ok 86.7 s, scene s44@w7 ok 107.1 s, scene s1@w7 ok 91.3 s, scene s2@w7 FAIL 112.1 s, scene s3@w7 ok 111.9 s, scene s4@w7 ok 90.0 s, scene s5@w7 ok 95.0 s, scene s6@w7 ok 108.8 s, scene s7@w7 ok 89.0 s, scene s8@w7 ok 120.4 s
v19 (972 lines): v19: camera bias from a second look at the squeezed stick (the stand may have been dragged) | practice: scene s2@w7 ok 139.5 s, scene s9@w7 ok 112.1 s, scene s10@w7 ok 104.2 s, scene s11@w7 ok 125.8 s, scene s12@w7 ok 102.9 s, scene s13@w7 ok 115.0 s, scene s14@w7 ok 107.3 s, scene s15@w7 ok 116.7 s, scene s16@w7 ok 106.5 s, scene s17@w7 ok 131.7 s, scene s18@w7 FAIL 99.5 s, scene s19@w7 ok 113.1 s, scene s20@w7 ok 99.4 s, scene s21@w7 ok 124.0 s, scene s22@w7 ok 94.4 s, scene s23@w7 ok 126.0 s
v20 (989 lines): v20: centring steps by the width excess on later passes (stiff arm / fixed stand) | practice: scene s18@w7 ok 105.9 s, scene s24@w7 ok 104.2 s, scene s25@w7 ok 108.2 s, scene s26@w7 ok 143.8 s, scene s27@w7 ok 98.8 s, scene s28@w7 ok 130.4 s, scene s29@w7 FAIL 128.3 s, scene s30@w7 ok 107.6 s
v21 (996 lines): v21: close-ups always with the camera on the survey side (bias consistency); width-step only for the stick | practice: scene s29@w7 ok 119.7 s, scene s31@w7 ok 92.8 s, scene s32@w7 ok 116.9 s, scene s33@w7 ok 121.1 s, scene s34@w7 ok 106.5 s, scene s35@w7 ok 136.3 s, scene s36@w7 ok 105.3 s, scene s37@w7 ok 104.1 s, scene s1@w7 ok 93.5 s, scene s2@w7 ok 139.5 s, scene s3@w7 ok 92.5 s, scene s4@w7 ok 94.1 s, scene s5@w7 ok 102.3 s, scene s6@w7 ok 113.8 s, scene s7@w7 ok 93.5 s, scene s8@w7 ok 118.3 s, scene s9@w7 ok 112.1 s, scene s10@w7 ok 104.2 s, scene s11@w7 ok 126.0 s, scene s12@w7 ok 102.9 s, scene s13@w7 ok 110.5 s, scene s14@w7 ok 107.3 s, scene s15@w7 ok 116.7 s, scene s16@w7 ok 106.5 s, scene s17@w7 ok 131.7 s, scene s18@w7 ok 105.9 s, scene s19@w7 ok 113.1 s, scene s20@w7 ok 99.4 s, scene s21@w7 ok 122.7 s, scene s22@w7 ok 94.4 s, scene s23@w7 ok 126.0 s, scene s24@w7 ok 104.2 s
v22 (997 lines): v22: ring grasp height adapts to short rings | practice: scene s1@w8 ok 93.3 s, scene s2@w8 ok 108.7 s, scene s3@w8 ok 98.3 s, scene s4@w8 FAIL 76.8 s, scene s5@w8 ok 105.6 s, scene s6@w8 ok 127.8 s, scene s7@w8 ok 108.5 s, scene s8@w8 ok 118.4 s
v23 (1000 lines): v23: close-up poses go higher when refused | practice: scene s4@w9 FAIL 133.4 s, scene s9@w9 ok 113.4 s, scene s10@w9 ok 106.9 s, scene s11@w9 ok 124.7 s, scene s12@w9 ok 96.1 s, scene s13@w9 ok 127.5 s, scene s14@w9 ok 105.2 s, scene s15@w9 ok 104.1 s, scene s1@w11 ok 93.3 s, scene s2@w11 ok 108.7 s, scene s3@w11 ok 98.4 s, scene s4@w11 ok 97.4 s, scene s5@w11 ok 105.6 s, scene s6@w11 ok 161.6 s, scene s7@w11 ok 108.5 s, scene s8@w11 ok 118.4 s, scene s16@w11 ok 109.8 s, scene s17@w11 ok 125.6 s, scene s18@w11 ok 101.4 s, scene s19@w11 ok 109.8 s, scene s20@w11 ok 98.5 s, scene s21@w11 ok 128.1 s, scene s22@w11 ok 95.3 s, scene s23@w11 ok 125.9 s, scene s24@w11 ok 125.3 s, scene s25@w11 ok 107.9 s, scene s26@w11 ok 143.5 s, scene s27@w11 ok 97.4 s, scene s28@w11 ok 126.0 s, scene s29@w11 ok 113.0 s, scene s30@w11 ok 140.9 s, scene s31@w11 ok 97.7 s, scene s32@w11 ok 111.2 s, scene s33@w11 ok 127.3 s, scene s34@w11 ok 109.7 s, scene s35@w11 ok 131.7 s, scene s36@w11 ok 98.7 s, scene s37@w11 ok 102.9 s, scene s38@w11 ok 129.7 s, scene s39@w11 ok 113.8 s, scene s1@w12 ok 93.3 s, scene s2@w12 ok 107.8 s, scene s3@w12 FAIL 48.9 s, scene s4@w12 FAIL 356.6 s, scene s5@w12 ok 109.1 s, scene s6@w12 ok 112.3 s, scene s7@w12 ok 116.9 s, scene s8@w12 ok 121.7 s
v24 (6 lines): idle: does nothing, prints truth (checks scene stability and that success() fails) | practice: scene s4@w9 FAIL 2.0 s, scene s1@w9 FAIL 2.0 s, scene s2@w9 FAIL 2.0 s, scene s3@w9 FAIL 2.0 s, scene s4@w10 FAIL 2.0 s, scene s5@w10 FAIL 2.0 s, scene s6@w10 FAIL 2.0 s, scene s7@w10 FAIL 2.0 s, scene s8@w10 FAIL 2.0 s, scene s9@w10 FAIL 2.0 s, scene s10@w10 FAIL 2.0 s, scene s11@w10 FAIL 2.0 s, scene s4@w11 FAIL 2.0 s, scene s4@w13 FAIL 2.0 s, scene s9@w13 FAIL 2.0 s, scene s10@w13 FAIL 2.0 s, scene s11@w13 FAIL 2.0 s, scene s12@w13 FAIL 2.0 s, scene s13@w13 FAIL 2.0 s, scene s14@w13 FAIL 2.0 s, scene s15@w13 FAIL 2.0 s, scene s4@w14 FAIL 2.0 s, scene s11@w14 FAIL 2.0 s, scene s1@w14 FAIL 2.0 s, scene s3@w14 FAIL 2.0 s, scene s5@w14 FAIL 2.0 s, scene s9@w14 FAIL 2.0 s, scene s12@w14 FAIL 2.0 s, scene s13@w14 FAIL 2.0 s, scene s1@w15 FAIL 2.0 s, scene s2@w15 FAIL 2.0 s, scene s3@w15 FAIL 2.0 s, scene s4@w15 FAIL 2.0 s
v25 (1001 lines): v25: stick candidate score: taller counts more, tiny patches penalised, no SAM-ring penalty | practice: scene s1@w13 ok 93.3 s, scene s2@w13 ok 107.8 s, scene s3@w13 ok 121.2 s, scene s4@w13 ok 94.0 s, scene s5@w13 ok 109.1 s, scene s6@w13 ok 116.7 s, scene s7@w13 ok 116.9 s, scene s8@w13 ok 110.2 s, scene s9@w13 ok 123.9 s, scene s10@w13 ok 106.9 s, scene s11@w13 FAIL 249.2 s, scene s12@w13 ok 124.6 s, scene s13@w13 ok 136.3 s, scene s14@w13 ok 115.2 s, scene s15@w13 ok 104.1 s, scene s16@w13 ok 113.5 s
v26 (1010 lines): v26: ring candidates ranked (hole evidence + survey score); a failed grasp moves on to the next candidate | practice: scene s11@w13 ok 392.8 s, scene s17@w13 ok 153.6 s, scene s18@w13 ok 104.7 s, scene s19@w13 ok 151.0 s, scene s20@w13 ok 112.8 s, scene s21@w13 ok 113.9 s, scene s22@w13 ok 91.6 s, scene s23@w13 ok 114.6 s
v27 (1010 lines): v27: at most 2 grasp attempts per ring candidate | practice: scene s11@w13 ok 255.0 s, scene s24@w13 FAIL 107.2 s, scene s25@w13 ok 111.2 s, scene s26@w13 ok 131.0 s, scene s27@w13 ok 93.6 s, scene s28@w13 ok 129.6 s, scene s29@w13 ok 113.7 s, scene s30@w13 ok 110.8 s
v28 (22 lines): probe: fingertip touch on the table with stop_on_contact | practice: scene s1@w13 FAIL 26.1 s, scene s2@w13 FAIL 26.1 s
v29 (22 lines): probe: fingertip touch on the table (target z 12.5) | practice: scene s1@w13 FAIL 28.8 s, scene s3@w13 FAIL 28.8 s
v30 (1011 lines): v30: larger height margins for the grasp and the release (wrist camera heights ~5 mm off) | practice: scene s24@w13 FAIL 121.4 s, scene s1@w13 ok 94.6 s, scene s2@w13 ok 107.7 s, scene s3@w13 ok 121.0 s, scene s31@w13 ok 97.1 s, scene s32@w13 ok 111.0 s, scene s33@w13 ok 127.2 s, scene s34@w13 ok 109.4 s
v31 (1014 lines): v31: release the ring once 15 mm onto the stick (slides down); keep the sag-corrected xy after a search | practice: scene s24@w13 FAIL 119.9 s, scene s35@w13 ok 129.7 s, scene s36@w13 ok 105.9 s, scene s37@w13 ok 96.7 s, scene s38@w13 ok 144.5 s, scene s39@w13 ok 105.2 s, scene s40@w13 ok 255.3 s, scene s41@w13 ok 121.1 s
v32 (1018 lines): v32: gentle release (loosen, wait, open) | practice: scene s24@w13 FAIL 122.9 s, scene s40@w13 ok 258.3 s, scene s42@w13 ok 108.2 s, scene s43@w13 ok 89.3 s, scene s44@w13 ok 107.2 s, scene s5@w13 ok 107.3 s, scene s6@w13 ok 113.4 s, scene s7@w13 ok 96.5 s, scene s9@w13 ok 127.0 s, scene s10@w13 ok 107.3 s, scene s11@w13 FAIL 489.7 s, scene s12@w13 ok 106.3 s, scene s13@w13 ok 135.7 s, scene s14@w13 ok 126.7 s, scene s15@w13 ok 102.3 s, scene s16@w13 ok 112.5 s
v33 (1036 lines): v33: travel moves (up, across, down) between places | practice: scene s11@w13 FAIL 151.0 s, scene s17@w13 ok 153.3 s, scene s18@w13 ok 115.4 s, scene s19@w13 ok 171.4 s, scene s20@w13 ok 105.9 s, scene s21@w13 ok 126.0 s, scene s22@w13 ok 110.0 s, scene s23@w13 ok 135.5 s, scene s11@w14 ok 235.9 s, scene s24@w14 ok 147.8 s, scene s1@w14 FAIL 110.1 s, scene s2@w14 ok 119.6 s, scene s3@w14 FAIL 67.6 s, scene s4@w14 ok 103.7 s, scene s5@w14 ok 114.1 s, scene s6@w14 ok 134.1 s, scene s1@w15 ok 110.1 s, scene s3@w15 FAIL 67.6 s, scene s11@w15 ok 232.6 s, scene s24@w15 FAIL 146.8 s, scene s4@w15 ok 103.7 s, scene s7@w15 ok 108.5 s, scene s8@w15 ok 113.3 s, scene s9@w15 ok 135.9 s
v34 (1036 lines): v34: stick close-up opening 40 mm (thick sticks) | practice: scene s3@w15 ok 111.2 s, scene s24@w15 ok 143.5 s, scene s2@w15 ok 119.6 s, scene s5@w15 ok 114.2 s, scene s6@w15 ok 134.1 s, scene s10@w15 ok 119.4 s, scene s12@w15 ok 118.0 s, scene s13@w15 ok 150.1 s, scene s14@w15 ok 120.8 s, scene s15@w15 ok 121.4 s, scene s16@w15 ok 135.7 s, scene s17@w15 ok 145.6 s, scene s18@w15 ok 115.4 s, scene s19@w15 ok 150.1 s, scene s20@w15 ok 105.9 s, scene s21@w15 ok 119.4 s, scene s22@w15 ok 110.5 s, scene s23@w15 ok 156.6 s, scene s25@w15 ok 114.6 s, scene s26@w15 ok 140.4 s, scene s27@w15 ok 89.0 s, scene s28@w15 ok 128.0 s, scene s29@w15 ok 120.7 s, scene s30@w15 ok 117.4 s, scene s31@w15 ok 106.3 s, scene s32@w15 ok 125.1 s, scene s33@w15 ok 133.5 s, scene s34@w15 ok 116.5 s, scene s35@w15 ok 156.1 s, scene s36@w15 ok 122.2 s, scene s37@w15 ok 109.8 s, scene s38@w15 ok 169.5 s, scene s45@w15 ok 95.9 s, scene s46@w15 ok 144.8 s, scene s47@w15 ok 116.2 s, scene s48@w15 ok 106.0 s, scene s49@w15 ok 124.2 s, scene s50@w15 ok 201.4 s, scene s51@w15 ok 113.8 s, scene s52@w15 ok 130.6 s, scene s53@w15 ok 134.6 s, scene s54@w15 ok 108.0 s, scene s55@w15 ok 112.2 s, scene s56@w15 ok 97.5 s, scene s39@w15 ok 118.7 s, scene s40@w15 ok 127.5 s, scene s41@w15 ok 156.2 s, scene s42@w15 ok 109.7 s
v35 (1054 lines): v35: = v34 tidied (header doc, unused_* names for old code); no behaviour change | practice: scene s43@w15 ok 104.5 s, scene s44@w15 ok 119.9 s, scene s1@w15 ok 110.1 s, scene s4@w15 ok 107.1 s, scene s7@w15 ok 108.5 s, scene s8@w15 ok 113.4 s, scene s9@w15 ok 135.9 s, scene s11@w15 ok 235.9 s
v36 (1065 lines): v36: = v35 + on any exception: open, lift clear, go home | practice: scene s45@w15 ok 95.9 s, scene s46@w15 ok 144.8 s, scene s47@w15 ok 116.2 s, scene s48@w15 ok 106.0 s, scene s2@w15 ok 119.6 s, scene s3@w15 ok 111.2 s, scene s5@w15 ok 114.2 s, scene s6@w15 ok 134.1 s, scene s49@w16 ok 124.2 s, scene s50@w16 ok 201.4 s, scene s51@w16 ok 113.8 s, scene s52@w16 ok 130.6 s, scene s53@w16 ok 134.6 s, scene s54@w16 ok 107.1 s, scene s55@w16 ok 112.2 s, scene s56@w16 ok 97.5 s, scene s57@w16 ok 120.8 s, scene s58@w16 ok 127.5 s, scene s59@w16 ok 131.4 s, scene s60@w16 ok 118.3 s, scene s61@w16 ok 116.2 s, scene s62@w16 ok 144.1 s, scene s63@w16 ok 145.5 s, scene s64@w16 ok 125.1 s, scene s65@w16 ok 118.9 s, scene s66@w16 FAIL 62.6 s, scene s67@w16 ok 105.4 s, scene s68@w16 ok 119.7 s, scene s10@w16 ok 119.4 s, scene s12@w16 ok 118.0 s, scene s13@w16 ok 150.1 s, scene s14@w16 ok 120.8 s
v37 (1065 lines): v37: accept thick sticks' close-up patch up to 34 mm (24 mm stick + depth blur) | practice: scene s66@w16 FAIL 62.6 s, scene s15@w16 ok 121.4 s, scene s16@w16 ok 135.7 s, scene s17@w16 ok 145.6 s, scene s18@w16 ok 115.4 s, scene s19@w16 ok 150.1 s, scene s20@w16 ok 105.9 s, scene s21@w16 ok 119.4 s
v38 (1065 lines): v38: accept thick sticks' close-up patch up to 34 mm (24 mm stick + depth blur) [v37 had not changed] | practice: scene s66@w16 ok 108.9 s, scene s22@w16 ok 110.5 s, scene s23@w16 ok 156.6 s, scene s25@w16 ok 114.6 s, scene s26@w16 ok 140.4 s, scene s27@w16 ok 89.0 s, scene s28@w16 ok 128.0 s, scene s29@w16 ok 120.7 s, scene s69@w16 ok 122.5 s, scene s70@w16 ok 150.0 s, scene s71@w16 ok 136.3 s, scene s72@w16 ok 168.9 s, scene s73@w16 ok 129.6 s, scene s74@w16 ok 107.1 s, scene s75@w16 ok 126.9 s, scene s76@w16 ok 104.9 s, scene s77@w16 ok 125.8 s, scene s78@w16 ok 140.2 s, scene s79@w16 ok 118.9 s, scene s80@w16 ok 121.0 s, scene s30@w16 ok 117.4 s, scene s31@w16 ok 106.3 s, scene s32@w16 ok 125.1 s, scene s33@w16 ok 133.5 s, scene s81@w16 ok 108.0 s, scene s82@w16 ok 116.2 s, scene s83@w16 ok 161.2 s, scene s84@w16 ok 226.7 s, scene s85@w16 ok 118.4 s, scene s86@w16 ok 120.1 s, scene s87@w16 ok 123.5 s, scene s88@w16 ok 124.5 s, scene s89@w16 ok 165.2 s, scene s90@w16 ok 171.6 s, scene s91@w16 ok 115.4 s, scene s92@w16 ok 147.3 s, scene s34@w16 ok 116.5 s, scene s35@w16 ok 156.1 s, scene s36@w16 ok 122.2 s, scene s37@w16 ok 109.8 s

Practice so far (successes/episodes, mean robot time of the successes): v0 in w0 0/4, in w1 0/4, in w2 0/4, in w3 0/4; v1 in w3 0/4; v2 in w3 2/2 (68 s); v3 in w3 2/8 (76 s); v4 in w4 2/8 (77 s); v5 in w4 0/2; v6 in w4 6/8 (87 s); v7 in w4 14/16 (111 s); v8 in w5 0/1; v9 in w4 7/8 (94 s); v10 in w6 6/8 (90 s); v11 in w6 6/8 (106 s); v12 in w6 6/8 (97 s); v13 in w6 1/2 (110 s); v14 in w6 24/25 (108 s); v15 in w6 16/16 (104 s), in w7 7/8 (104 s); v16 in w7 22/24 (96 s); v17 in w7 7/8 (104 s); v18 in w7 15/16 (108 s); v19 in w7 15/16 (115 s); v20 in w7 7/8 (114 s); v21 in w7 32/32 (110 s); v22 in w8 7/8 (109 s); v23 in w9 7/8 (111 s), in w11 32/32 (114 s), in w12 6/8 (110 s); v24 in w9 0/4, in w10 0/8, in w11 0/1, in w13 0/8, in w14 0/8, in w15 0/4; v25 in w13 15/16 (113 s); v26 in w13 8/8 (154 s); v27 in w13 7/8 (135 s); v28 in w13 0/2; v29 in w13 0/2; v30 in w13 7/8 (110 s); v31 in w13 7/8 (137 s); v32 in w13 14/16 (121 s); v33 in w13 7/8 (131 s), in w14 6/8 (143 s), in w15 6/8 (134 s); v34 in w15 48/48 (126 s); v35 in w15 8/8 (129 s); v36 in w15 8/8 (118 s), in w16 23/24 (126 s); v37 in w16 7/8 (128 s); v38 in w16 40/40 (130 s). Submitted: v38.

Your notes from the earlier session:
# Notes: cylinder-on-stick skill

## Status (keep updated)
- FINAL CANDIDATE: v38 with world w16 (= w15 + LAST cleared per episode). v38: 24/24 in w16.
  summary.txt written (session summary for final submit).
- SUBMITTED: v34 with world w15 (stress: gaps 5/8, 3-8 distractors, friction 0.2-1.2, stand density
  300-12000, + w11 features). v34 in w15: 24/24 so far (s1-s21 mix). w15 ring = 24 wall boxes 0.5 mm short of
  the bottom + 8 full-height "feet" boxes inside the wall (96 coplanar contacts made the ring creep/jump;
  thin feet tunnelled), free-joint damping 0.003.
- v25-v34: stick score (taller counts, tiny patches penalised); ring candidates ranked + next candidate on
  failed grasp; max 2 grasp attempts; grasp/release height margins (wrist heights ~5 mm off); release ring
  once 15 mm on the stick (slides down) and keep sag-corrected xy after search; gentle release; travel()
  up-across-down between places (straight moves swept fingers through things); stick close-up opening 40 mm.
- Fingertip touch on the table with stop_on_contact is NOT reliable (70 N, no stop) -> never touch rigid.
- TCP may not go below z=10 (refused).
- SUBMITTED: v23 with world w11 (hard+). v23: 40/40 in w11 (s1-s8, s16-s39). w11 = w7 + sticks 5-24,
  rings 12-55 tall, OD<=68, ring/stand never black (sim depth loses black things entirely), ring free joint
  damping 0.0002 (s4 ring flew away at t~0.7s spontaneously without it), ring density 500-8000.
- v22: grasp height adapts to short rings; v23: close-up poses go higher if refused (keep-out of right arm).
- (older) SUBMITTED: v21 with world w7 (hard). v21: 32/32 in w7 (s1-s24, s29-s37).
- skill.py = working file of the program (has dead code run_old / squeeze_center_old: remove before final).
- world.py = w7 source (DARK, PLAT, CAMS flags). w6 = cluttered w/o camera errors; w4 = plain; w5 = squeeze test.
- v15 sag fix, v16 stick cand. dedupe, v17 plate fraction + strict hole, v18 finish() homing,
  v19 bias from re-look at squeezed stick, v20 width-excess step (fixed stand), v21 close-ups same camera side.

## Tool facts
- practice: one sub-command per Bash call (no &&, no cat/heredoc), --world/--version take ints.
- run_episodes output shows only first 12 + last 25 printed lines; use read_output for the middle.
- MJCF: no comments, no <custom>; hidden sites (rgba 0, pos*1e-4) carry world params to success().
- Table pattern size_mm ranges: wood 15..45, stripes 8..50, speckle 1.5..6 (others: omit).
- World module globals ARE shared between robot() wrapper and success() (LAST state -> home check).

## World (w6)
- Ring = 24 boxes, hole dia = ds + c (c 1.5..5, half the time <2), OD <= 66, H 15..55, density 300-8000.
- Stand: box/round base 60..140 x 6..25 thick, stick ds 5..20, L = H+20..H+90 (<=160), flat/round top,
  stick off-centre 30%. Platforms 35% each (15..70 mm). Table h -40..40, colours/patterns.
- Distractors 1-6: box, sphere, solid cyl, lying capsule, tall thin box, ring-sized solid decoy, disc.
- success: ring axis within clearance of stick axis at bottom+top, bottom between base and L-H, stand tilt<5,
  rel tilt<8, robot not touching ring/stand, final TCP within 25mm of start & opening>70 (via LAST).

## Skill (v14) pipeline
1. Survey: wrist cam from TCP (-420,60,600) looking down. Table plane fit; height image; opening (44mm disc)
   residual -> stick candidates (local rise vs annulus 18-32mm p60); SAM ring/cylinder-with-hole/washer +
   compact objects -> ring candidates. Height grid (3mm) for finger clearance.
2. Ring: close-up (camera 318 mm above top) -> top patch, filled -> centre/OD/hole (deep centre or hole blob
   low) -> accept first holed+plausible; else best weak.
3. Stick: close-ups -> thin+high patch; pedestal (base above table plane >=3) first; squeeze verifies round.
4. Stick squeeze a-b-a low on the stick (base+25) with iterative centring (center_along: if the arm gets
   pushed (>0.35mm) step 1.8x push and re-close) -> exact stick xy. Bias closeup-vs-squeeze corrects ring xy.
5. Ring grasp: squeeze a-b at sup+22, consistency check (w1~w2~OD) else re-closeup & retry.
6. Insert: travel high, turn fingers to free direction, descend stop_on_contact to base+2; if stuck on top ->
   spiral search (<=3.3mm); if fails put ring back, re-squeeze stick, retry once. Release, lift, home.
- squeeze_at: tries other closing angles when a move/turn is refused (wrist flip / reach) or blocked.

## Findings
- Arm is compliant: closing on a fixed/heavy thing off-centre pushes the arm ~2-3 mm and reads wider (fixed
  16 mm stick at 6 mm offset read 22.5). => iterate; judge widths only once centred.
- Gripping a tall ring high tips it -> grip low (fingertips ~6 mm above support).
- Wrist close-up xy error up to ~7 mm (camera calibration), same for ring & stick -> bias transfer works.
- stop_on_contact with held ring onto rigid support gave SAFETY STOP 51 N once (v2 test) - avoid hard touches.
- Times ~80-140 s; search can be long (time guard at 470 s).

- Loaded arm sags ~1.5 mm in xy (heavy ring): close the loop on measured TCP before descending (v15).
- Camera bias depends on gripper yaw (camera side flips if closing sign flips) -> always same side (v21).
- Squeezing a free stand drags it to the TCP -> first close-up vs squeeze says nothing about camera bias;
  re-look at the stick after squeezing gives the true bias (up to 13 mm in w7).
- robot.home() can be refused near tall things -> finish(): lift, straight move to start pose, then home.

## Ideas / TODO
- Run w4 regression; more scenes (s21+); harder variants (low-contrast colours, light tall stands).
- Clean dead code; final summary file.


Your worlds (read_world):
w0 (243 lines): w0: ring (24 boxes) + stand with stick, platforms, distractors, table height/colour vary
w1 (250 lines): w0b: params in MJCF comment
w2 (249 lines): w2: params in <custom numeric>, table pattern size fixed
w3 (255 lines): w3: params in hidden sites
w4 (258 lines): w4: = w3 with table pattern sizes in range
w5 (22 lines): w5: test world: fixed stick + heavy stand, for squeeze tests
w6 (271 lines): w6 (cluttered): as w4 but gaps 10/15 mm, 1-6 distractors incl. tall thin boxes, solid ring-sized decoys, discs; ds 5-20, OD<=66
w7 (282 lines): w7 (hard): w6 + platforms 15-120 mm, 12% dark colours, random camera pose errors (wrist 1-5 mm/0.3-2 deg, scene 2-8 mm/0.3-1.5 deg) and depth noise 0.12-0.3 px
w8 (283 lines): w8 (hard+): w7 + stick 5-24 mm, ring height 12-55, OD up to 68
w9 (285 lines): w9: = w8 but ring and stand never black (max channel >= 0.3); distractors may be dark
w10 (285 lines): w10: = w9 with ring density 500-8000 (stability check)
w11 (285 lines): w11: = w10 with a tiny damping on the ring's free joint (start-up instability in s4)
w12 (286 lines): w12 (stress): w11 + gaps 5/8 mm, 3-8 distractors, frictions 0.2-1.2, stand density 300-12000
w13 (286 lines): w13: = w12 (stress) with ring free-joint damping 0.003
w14 (296 lines): w14 (stress): = w13 with the ring standing on 8 thin feet (its 24 wall boxes stop 0.5 mm short of the bottom): no creeping
w15 (297 lines): w15 (stress): ring feet are full-height boxes inside the wall (w14's thin feet tunnelled through the base)
w16 (298 lines): w16 (final): = w15 (stress world) + the recorded last robot state is cleared per episode. Task: put the ring-like cylinder (hole = stick + 1.5..5 mm) on the stand's stick, let go, open, go home.

The real robot's episodes so far: 4 episodes, R1 (v38: FAILED), R2 (v38: FAILED), R3 (v38: FAILED), R4 (v38: FAILED). Start with list_real_episodes.

v38, the version that ran there:

```python
"""Put the cylinder (with a hole) on the stick of the stand. Entry point: run(robot, task).

1. survey: the wrist camera looks down on the whole work area from high up; table plane fit; candidates for
   the stick (thin things much higher than their surroundings: depth opening residual) and for the ring
   (SAM 'ring' / 'cylinder with a hole' / 'washer' masks and compact objects).
2. close-ups (camera ~320 mm above, always from the same side): the ring = a round top with a hole that is
   deeper than its top; the stick = a thin high patch standing on a plate.
3. the stick is centred between the fingers three times (a, b, a: 90 degrees apart, low on the stick): its
   position is then the TCP's, to a few tenths of a mm, whether the stand slides or not (if it does not, the
   compliant arm is pushed aside: step by the push / by the width excess and close again).
4. a second look at the squeezed stick measures the camera's calibration error; the ring's position is
   corrected by it; the ring is centred the same way (a, b) and held low.
5. carried high, the measured TCP brought over the stick (the loaded arm sags), lowered with stop-on-contact;
   if it stops on the stick's top: a small spiral search; released once 15 mm on the stick (it slides down).
6. open, lift clear, back to the start pose.
The functions named unused_* are earlier versions kept for reference; they are not called.
"""
import math
import numpy as np
import cv2

ARM = "left"
DOWN = (0.0, 0.0, -1.0)
C0 = (0.0, -1.0, 0.0)
SURVEY = (-420.0, 60.0, 600.0)


# ------------------------------------------------------------------ robot helpers
def st(robot):
    return robot.state(ARM)


def tcp(robot):
    return np.array(st(robot)["tcp_mm"], float)


def cdir(robot):
    return tuple(float(x) for x in st(robot)["closing_dir"])


def mv(robot, xyz, stop=False, closing=None):
    c = closing if closing is not None else cdir(robot)
    r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, stop)
    if not r.get("moved", True) or (not r.get("done") and r.get("why") != "contact"):
        print("MOVE", [round(float(v), 1) for v in xyz], "stop" if stop else "", r)
    return r


TRAVEL = {"z": 300.0}     # above everything on the table (set after the survey)


def travel(robot, xyz, closing=None):
    """go there over the top of everything: up, across, down (straight lines between places can sweep the
    open fingers through the things in between)"""
    p = tcp(robot)
    zt = max(TRAVEL["z"], float(xyz[2]))
    if math.hypot(xyz[0] - p[0], xyz[1] - p[1]) > 15:
        if p[2] < zt - 1:
            mv(robot, (p[0], p[1], zt))
        mv(robot, (xyz[0], xyz[1], zt), closing=closing)
    return mv(robot, xyz, closing=closing)


def turn(robot, ang):
    p = tcp(robot)
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), ang)
    if r.get("moved"):
        return ang
    r = robot.turn(ARM, p.tolist(), (0, 0, 1), -ang)
    if r.get("moved"):
        return -ang
    print("TURN refused", r.get("why"))
    return 0


def truth(robot):
    try:
        return robot.truth()
    except Exception:
        return None


# ------------------------------------------------------------------ image helpers
def disc(px):
    px = max(3, int(round(px))) | 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px, px))


def fill_nan(a, iters=8, default=0.0):
    w = np.where(np.isfinite(a), a, -1e4).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    for _ in range(iters):
        m = w < -1e3
        if not m.any():
            break
        d = cv2.dilate(w, k)
        w[m] = d[m]
    w[w < -1e3] = default
    return w


def table_plane(P):
    z = P[..., 2]
    v = np.isfinite(z)
    zs = z[v]
    hist, edges = np.histogram(zs, bins=np.arange(-100, 400, 2.0))
    z0 = edges[int(np.argmax(hist))] + 1.0
    X = P[v & (np.abs(z - z0) < 8)]
    if len(X) > 40000:
        X = X[:: len(X) // 40000 + 1]
    coef = np.array([0.0, 0.0, z0])
    for _ in range(3):
        A = np.c_[X[:, 0], X[:, 1], np.ones(len(X))]
        coef = np.linalg.lstsq(A, X[:, 2], rcond=None)[0]
        r = X[:, 2] - A @ coef
        X = X[np.abs(r) < 4]
    return coef


def ray_point(f, u, v, z):
    d = f.R @ np.array([(u - f.cx) / f.fx, (v - f.cy) / f.fy, 1.0])
    t = (z - f.eye[2]) / d[2]
    return np.asarray(f.eye, float) + t * d


def mask_iou(a, b):
    i = np.logical_and(a, b).sum()
    u = np.logical_or(a, b).sum()
    return i / max(u, 1)


def annulus(shape, u, v, r0, r1):
    """boolean mask of pixels with r0 <= distance to (u, v) < r1 (pixels), plus the window slices"""
    H, W = shape
    R = int(math.ceil(r1)) + 1
    v0, v1, u0, u1 = max(0, int(v) - R), min(H, int(v) + R + 1), max(0, int(u) - R), min(W, int(u) + R + 1)
    yy, xx = np.mgrid[v0:v1, u0:u1]
    rr = np.hypot(xx - u, yy - v)
    return (slice(v0, v1), slice(u0, u1)), (rr >= r0) & (rr < r1)


# ------------------------------------------------------------------ survey
def survey(robot):
    mv(robot, SURVEY, closing=C0)
    f = robot.look(["left_wrist"])["left_wrist"]
    P = f.points().copy()
    dep = f.depth
    Hh, Ww = dep.shape
    bad = ~np.isfinite(dep) | (dep < 330)
    bad[int(Hh * 0.78):, :int(Ww * 0.3)] = True      # the fingertips
    bad[int(Hh * 0.78):, int(Ww * 0.7):] = True
    P[bad] = np.nan
    coef = table_plane(P)
    zp = coef[0] * P[..., 0] + coef[1] * P[..., 1] + coef[2]
    h = P[..., 2] - zp
    h[h > 330] = np.nan
    valid = np.isfinite(h)
    hf = fill_nan(h)
    rng_mm = float(np.nanmedian(dep[valid]))
    pxt = f.fx / rng_mm                                  # px per mm on the table
    op = cv2.morphologyEx(hf, cv2.MORPH_OPEN, disc(44 * pxt))
    res = hf - op
    sam = {}
    for ph in ("stick", "ring", "cylinder with a hole", "washer"):
        try:
            sam[ph] = [(m.astype(bool), float(s)) for m, s in robot.segment(f, ph)]
        except Exception as e:
            print("segment fail", ph, e)
            sam[ph] = []
    # ---- stick candidates: thin things much higher than their close surroundings
    sticks = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats((res > 15).astype(np.uint8), 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 3:
            continue
        x0, y0, w0, h0 = stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]
        sl = (slice(y0, y0 + h0), slice(x0, x0 + w0))
        comp = lab[sl] == i
        hv = np.where(comp, hf[sl], -1e9)
        vv, uu = np.unravel_index(int(np.argmax(hv)), hv.shape)
        v, u = vv + y0, uu + x0
        top = float(hf[v, u])
        pxmm = f.fx / max(150.0, rng_mm - top)
        win, ann = annulus(hf.shape, u, v, 18 * pxmm, 32 * pxmm)
        hv_ann = h[win][ann]
        hv_ann = hv_ann[np.isfinite(hv_ann)]
        if len(hv_ann) < 10:
            continue
        a75 = float(np.percentile(hv_ann, 60))
        ped = float(np.median(hv_ann))
        lrise = top - a75
        if lrise < 15:
            continue
        patch = comp & (hf[sl] > top - 6)
        eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
        zabs = top + float(coef[2] + coef[0] * -300)
        p = ray_point(f, u, v, top + coef[2])
        samv = max([s for m, s in sam["stick"] if m[max(0, v - 4):v + 5, max(0, u - 4):u + 5].any()] + [0.0])
        ringv = max([s for ph in ("ring", "cylinder with a hole", "washer") for m, s in sam[ph][:3] if m[v, u]] + [0.0])
        # tall and thin (a top patch of a few mm is an edge artefact, not a stick), on a plate, SAM's word
        score = min(3.0, lrise / 30.0) + (0.8 if 7 <= eqd < 36 else (-0.5 if eqd < 5 else 0.0)) \
            + (0.4 if ped > 4 else 0.0) + 1.2 * samv
        sticks.append(dict(uv=(int(u), int(v)), xyz=p, top=float(p[2]), lrise=lrise, eqd=eqd, ped=ped + coef[2],
                           sam=samv, ringv=ringv, score=score))
    sticks.sort(key=lambda s: -s["score"])
    # ---- ring candidates: SAM masks and compact objects
    cands = []
    for ph in ("ring", "cylinder with a hole", "washer"):
        for m, s in sam[ph][:4]:
            if m.sum() < 20:
                continue
            for r in cands:
                if mask_iou(r["mask"], m) > 0.5:
                    r["sam"] += s
                    break
            else:
                cands.append(dict(mask=m, sam=s))
    objm = ((hf > 8) & valid).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(objm, 8)
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        eqd = 2 * math.sqrt(a / math.pi) / pxt
        if 15 < eqd < 85:
            m = lab == i
            if not any(mask_iou(r["mask"], m) > 0.3 for r in cands):
                cands.append(dict(mask=m, sam=0.0))
    rings = []
    for r in cands:
        m = r["mask"]
        hm = h[m]
        hm = hm[np.isfinite(hm)]
        if len(hm) < 10:
            continue
        top = float(np.percentile(hm, 80))
        m8 = m.astype(np.uint8)
        out = cv2.dilate(m8, disc(50 * pxt)).astype(bool) & ~cv2.dilate(m8, disc(12 * pxt)).astype(bool) & valid
        sup = float(np.percentile(h[out], 25)) if out.sum() > 10 else 0.0
        vs, us = np.nonzero(m)
        u, v = float(us.mean()), float(vs.mean())
        pxmm = f.fx / max(150.0, rng_mm - top)
        p = ray_point(f, u, v, top + coef[2])
        eqd = 2 * math.sqrt(m.sum() / math.pi) / pxmm
        cnts, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        per = max(cv2.arcLength(max(cnts, key=len), True), 1.0)
        circ = 4 * math.pi * m.sum() / per ** 2
        rise = top - sup
        plaus = (10 < rise < 75) and (16 < eqd < 85)
        r.update(uv=(int(u), int(v)), xyz=p, top=float(p[2]), rise=rise, eqd=eqd, circ=circ,
                 score=r["sam"] + (0.8 if plaus else -1.5) + (0.4 if circ > 0.7 else 0.0))
        rings.append(r)
    rings.sort(key=lambda r: -r["score"])
    zmax = float(np.nanpercentile(P[..., 2], 99.8))
    HG = height_grid(P, float(coef[2] + coef[0] * -300 + coef[1] * 50))
    T = truth(robot)
    if T:
        sp = np.array(T["w_stand"], float)
        (uv,), _ = f.project(sp[None])
        u, v = int(uv[0]), int(uv[1])
        sl = (slice(max(0, v - 40), v + 20), slice(max(0, u - 15), u + 15))
        print("DBG stand px", u, v, "h max %.1f res max %.1f valid %.2f" % (np.nanmax(h[sl]), res[sl].max(),
                                                                          np.isfinite(h[sl]).mean()))
        vv, uu = np.unravel_index(int(np.argmax(res[sl])), res[sl].shape)
        print("DBG at max res", uu + sl[1].start, vv + sl[0].start, "hf %.1f op %.1f" % (
            hf[sl][vv, uu], op[sl][vv, uu]), "ncomp", n)
        robot.show("hf", np.clip(hf * 2, 0, 255).astype(np.uint8))
        robot.show("res", np.clip(res * 4, 0, 255).astype(np.uint8))
    return dict(f=f, coef=coef, sticks=sticks, rings=rings, zmax=zmax, HG=HG)


def cam_offset(f, robot):
    t = tcp(robot)
    return np.asarray(f.eye[:2], float) - t[:2]


# ------------------------------------------------------------------ close-ups
def closeup(robot, xy, top_z, camoff, rise=185.0):
    z = min(780.0, max(top_z + rise, 260.0))
    tx, ty = xy[0] - camoff[0], xy[1] - camoff[1]
    tx = min(90.0, max(-700.0, tx))
    for dz in (0.0, 80.0, 160.0):        # the arm may refuse a pose (e.g. near the parked arm): go higher
        r = travel(robot, (tx, ty, min(780.0, z + dz)), closing=C0)
        if r.get("done"):
            break
    # the same camera side as in the survey, always (its calibration error turns with it)
    if float(np.dot(st(robot)["closing_dir"], C0)) < 0:
        print("closeup: gripper flipped, turning it back")
        if turn(robot, 180) == 0:
            turn(robot, 90)
            turn(robot, 90)
    f = robot.look(["left_wrist"])["left_wrist"]
    return f


def stick_closeup(f, guess_xy, guess_top, coef=None):
    P = f.points()
    z = P[..., 2]
    if not np.isfinite(z).any():
        return None
    zf = fill_nan(z, default=float(np.nanmin(z)))
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    op = cv2.morphologyEx(zf, cv2.MORPH_OPEN, disc(40 * pxmm))   # wider than the thickest stick (+ blur)
    res = zf - op
    n, lab, stats, cents = cv2.connectedComponentsWithStats((res > 12).astype(np.uint8), 8)
    best = None
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 4:
            continue
        comp = lab == i
        zv = np.where(comp, zf, -1e9)
        v, u = np.unravel_index(int(np.argmax(zv)), zv.shape)
        dpx = math.hypot(u - u0, v - v0)
        if dpx > 40 * pxmm:
            continue
        sc = zv[v, u] - 0.5 * dpx / pxmm
        if best is None or sc > best[0]:
            best = (sc, i)
    if best is None:
        return None
    comp = lab == best[1]
    zc = np.where(comp & np.isfinite(z), z, np.nan)
    if not np.isfinite(zc).any():
        return None
    top = float(np.nanpercentile(zc, 97))
    patch = comp & np.isfinite(z) & (z > top - 4)
    if patch.sum() < 3:
        patch = comp & np.isfinite(z) & (z > top - 8)
    xy = P[patch][:, :2].mean(axis=0)
    eqd = 2 * math.sqrt(patch.sum() / math.pi) / pxmm
    vs, us = np.nonzero(patch)
    uc, vc = float(us.mean()), float(vs.mean())
    rp = eqd / 2
    win, ann = annulus(z.shape, uc, vc, (rp + 3) * pxmm, (rp + 14) * pxmm)
    zz = z[win][ann & (res[win] < 4)]
    zz = zz[np.isfinite(zz)]
    base = float(np.percentile(zz, 70)) if len(zz) > 15 else None
    win2, ann2 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 16) * pxmm)
    zz2 = z[win2][ann2]
    zz2 = zz2[np.isfinite(zz2)]
    lrise = top - float(np.percentile(zz2, 75)) if len(zz2) > 10 else 0.0
    plate = 0.0
    if coef is not None:            # how much of the ring 5-20 mm around it is a plate above the table
        zt = coef[0] * xy[0] + coef[1] * xy[1] + coef[2]
        win3, ann3 = annulus(z.shape, uc, vc, (rp + 5) * pxmm, (rp + 20) * pxmm)
        zz3 = z[win3][ann3]
        zz3 = zz3[np.isfinite(zz3)]
        if len(zz3) > 20:
            plate = float(np.mean((zz3 > zt + 3.5) & (zz3 < top - 10)))
    return dict(xy=xy, top=top, eqd=eqd, base=base, lrise=lrise, plate=plate, uv=(uc, vc))


def ring_closeup(f, guess_xy, guess_top, guess_d):
    P = f.points()
    z = P[..., 2]
    (uv,), _ = f.project(np.array([[guess_xy[0], guess_xy[1], guess_top]]))
    u0, v0 = float(uv[0]), float(uv[1])
    pxmm = f.fx / max(150.0, float(f.eye[2] - guess_top))
    gd = min(max(guess_d, 20.0), 75.0)
    yy, xx = np.mgrid[0:z.shape[0], 0:z.shape[1]]
    rr = np.hypot(xx - u0, yy - v0) / pxmm
    win = (rr < gd / 2 + 12) & np.isfinite(z)
    if win.sum() < 30:
        return None
    ztop = float(np.percentile(z[win], 92))
    patch = (np.abs(np.nan_to_num(z, nan=-1e4) - ztop) < 4) & (rr < gd / 2 + 35)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(patch.astype(np.uint8), 8)
    if n < 2:
        return None
    best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA] - 3 * pxmm * math.hypot(cents[i][0] - u0,
                                                                                             cents[i][1] - v0))
    pm = (lab == best).astype(np.uint8)
    cnts, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(pm)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    fm = filled.astype(bool)
    hole = fm & ~pm.astype(bool)
    vs, us = np.nonzero(fm)
    uc, vc = float(us.mean()), float(vs.mean())
    c3 = ray_point(f, uc, vc, ztop)
    od = 2 * math.sqrt(fm.sum() / math.pi) / pxmm
    hole_d = 2 * math.sqrt(hole.sum() / math.pi) / pxmm
    zh = z[hole]
    low = float(np.mean(~np.isfinite(zh) | (np.nan_to_num(zh, nan=-1e4) < ztop - 6))) if hole.any() else 0.0
    high = float(np.mean(np.nan_to_num(zh, nan=-1e4) > ztop + 6)) if hole.any() else 0.0
    rc = np.hypot(xx - uc, yy - vc) / pxmm
    ann = (rc > od / 2 + 6) & (rc < od / 2 + 22) & np.isfinite(z) & (z < ztop - 6)
    sup = float(np.percentile(z[ann], 30)) if ann.sum() > 20 else None
    cen = rc < 2.5
    zc = z[cen]
    deep = float(np.mean(~np.isfinite(zc) | (np.nan_to_num(zc, nan=-1e4) < ztop - 6))) if cen.any() else 0.0
    # the largest blob of the hole region near the centre, and how much of it is deeper than the top
    hd2, lowc = 0.0, 0.0
    nh, hl, hst, hce = cv2.connectedComponentsWithStats(hole.astype(np.uint8), 8)
    for i in range(1, nh):
        if math.hypot(hce[i][0] - uc, hce[i][1] - vc) / pxmm > 4:
            continue
        d_ = 2 * math.sqrt(hst[i, cv2.CC_STAT_AREA] / math.pi) / pxmm
        if d_ > hd2:
            zz_ = z[hl == i]
            hd2 = d_
            lowc = float(np.mean(~np.isfinite(zz_) | (np.nan_to_num(zz_, nan=-1e4) < ztop - 6)))
    img = f.rgb.copy()
    cv2.drawContours(img, cnts, -1, (255, 0, 0), 1)
    cv2.circle(img, (int(round(uc)), int(round(vc))), 3, (0, 255, 0), -1)
    return dict(xy=c3[:2], top=ztop, od=od, hole_d=hole_d, low=low, high=high, deep=deep, hd2=hd2, lowc=lowc,
                sup=sup, uv=(uc, vc), img=img)


# ------------------------------------------------------------------ free directions for the fingers
def height_grid(P, z_table, x0=-680.0, x1=40.0, y0=-420.0, y1=520.0, cell=3.0):
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    G = np.full((nx, ny), -1e4, np.float32)
    X = P[np.isfinite(P[..., 2])]
    ix = ((X[:, 0] - x0) / cell).astype(int)
    iy = ((X[:, 1] - y0) / cell).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    np.maximum.at(G, (ix[ok], iy[ok]), X[ok, 2].astype(np.float32))
    G = fill_nan(np.where(G < -1e3, np.nan, G), iters=4, default=z_table)
    return dict(G=G, x0=x0, y0=y0, cell=cell)


def region_max(HG, centre, cdir_xy, along0, along1, across):
    """highest surface in the two finger regions: along the closing line from along0 to along1 on both sides,
    +-across beside it."""
    c = np.asarray(cdir_xy, float)
    c = c / np.linalg.norm(c)
    n = np.array([-c[1], c[0]])
    a = np.arange(along0, along1 + 0.1, 2.5)
    b = np.arange(-across, across + 0.1, 2.5)
    A, B = np.meshgrid(a, b)
    pts = []
    for s in (1, -1):
        pts.append(centre[None, :] + (s * A.ravel())[:, None] * c[None, :] + B.ravel()[:, None] * n[None, :])
    pts = np.concatenate(pts)
    ix = ((pts[:, 0] - HG["x0"]) / HG["cell"]).astype(int)
    iy = ((pts[:, 1] - HG["y0"]) / HG["cell"]).astype(int)
    G = HG["G"]
    ok = (ix >= 0) & (ix < G.shape[0]) & (iy >= 0) & (iy < G.shape[1])
    if not ok.any():
        return -1e4
    return float(np.percentile(G[ix[ok], iy[ok]], 95))


def move_closing(robot, xyz, angles):
    """move there with the first closing direction (angle about z from +x) that the arm accepts"""
    for th in angles:
        c = (math.cos(math.radians(th)), math.sin(math.radians(th)), 0.0)
        r = robot.move(ARM, [float(v) for v in xyz], DOWN, c, False)
        if r.get("done"):
            return th
        print("move with closing", round(th), "refused:", r.get("why", "")[:80])
        if r.get("moved"):
            break
    r = mv(robot, xyz)
    return None


def best_closing(HG, centre, inner, z_tip, angles=(0, 30, 60, 90, 120, 150), extra=(45, 90)):
    """the closing direction (angle about z, from +x) whose finger regions (and those turned by `extra`) are
    clearest below the fingertips' height z_tip; returns (angle_deg, clearance_mm)"""
    best = None
    for th in angles:
        hs = []
        for e in (0,) + tuple(extra):
            t = math.radians(th + e)
            hs.append(region_max(HG, np.asarray(centre, float), (math.cos(t), math.sin(t)), inner, 58.0, 21.0))
        clr = z_tip - max(hs)
        if best is None or clr > best[1] + 0.5:
            best = (th, clr)
    return best


# ------------------------------------------------------------------ squeeze
def squeeze_center(robot, label, min_w, max_w=80.0, passes=2):
    """Centre the thing between the fingers along the closing line, then (turned 90 degrees) along the other,
    (and back again if passes=3). Returns the widths or None."""
    w1 = center_along(robot, label + "-a", min_w, max_w)
    if w1 < min_w:                      # missed it: catch it along the other line first, then come back
        robot.set_gripper(ARM, 80)
        a = turn(robot, 90)
        if a == 0:
            return "TURNFAIL"
        w = center_along(robot, label + "-rot", min_w, max_w)
        robot.set_gripper(ARM, 80)
        if not (min_w <= w <= max_w):
            return None
        if turn(robot, -a) == 0:
            return "TURNFAIL"
        w1 = center_along(robot, label + "-a2", min_w, max_w)
    if not (min_w <= w1 <= max_w):
        robot.set_gripper(ARM, 80)
        return None
    ws = [w1]
    for k in range(passes - 1):
        robot.set_gripper(ARM, min(80, ws[-1] + 25))
        if turn(robot, 90 if k % 2 == 0 else -90) == 0:
            robot.set_gripper(ARM, 80)
            return "TURNFAIL"
        w = center_along(robot, label + "-" + "bc"[k % 2], min_w, max_w,
                         ref_w=ws[0] if label.startswith("STICK") else None)
        ws.append(w)
        if not (min_w <= w <= max_w):
            robot.set_gripper(ARM, 80)
            return None
    print(label, "widths", np.round(ws, 2), "tcp", np.round(tcp(robot), 2))
    return ws


def squeeze_at(robot, label, xy, z_safe, z_grip, angles, min_w, max_w, passes):
    """go down at xy to z_grip with one of the closing angles and centre the thing there; when the arm
    refuses a move or a turn, come back up and try the next angle. Returns the widths or None."""
    tried = []
    for th in angles:
        if any(abs(((th - t) + 90) % 180 - 90) < 10 for t in tried):
            continue
        tried.append(th)
        travel(robot, (xy[0], xy[1], z_safe))
        if move_closing(robot, (xy[0], xy[1], z_safe), [th]) is None:
            continue
        r = mv(robot, (xy[0], xy[1], z_grip), stop=True)
        if not r.get("moved", True) or (r.get("why") and r.get("why") != "contact"):
            print(label, "descent refused/stopped at angle", round(th), (r.get("why") or "")[:70])
            p = tcp(robot)
            mv(robot, (p[0], p[1], z_safe))
            continue
        if r.get("why"):
            p = tcp(robot)
            print(label, "descent touched at", np.round(p, 1), "angle", round(th))
            if p[2] > z_grip + 8:            # something under the fingers: try another direction
                mv(robot, (p[0], p[1], z_safe))
                continue
            mv(robot, (p[0], p[1], p[2] + 3))
        ws = squeeze_center(robot, label, min_w, max_w, passes)
        if isinstance(ws, str):
            print(label, "turn refused at angle", round(th))
            robot.set_gripper(ARM, 80)
            p = tcp(robot)
            mv(robot, (p[0], p[1], z_safe))
            continue
        return ws
    return None


def unused_squeeze_center_first(robot, label, min_w, max_w=80.0):
    """(first version, kept for reference only)"""
    w1 = robot.set_gripper(ARM, 0)
    print(label, "squeeze1 w=%.2f" % w1)
    if w1 < min_w:
        robot.set_gripper(ARM, 80)
        a = turn(robot, 90)
        w = robot.set_gripper(ARM, 0)
        print(label, "retry rotated w=%.2f" % w)
        if w < min_w or w > max_w:
            robot.set_gripper(ARM, 80)
            return None
        robot.set_gripper(ARM, 80)
        turn(robot, -a)
        w1 = robot.set_gripper(ARM, 0)
        print(label, "squeeze1b w=%.2f" % w1)
        if w1 < min_w:
            robot.set_gripper(ARM, 80)
            return None
    if w1 > max_w:
        robot.set_gripper(ARM, 80)
        return None
    robot.set_gripper(ARM, min(80, w1 + 25))
    turn(robot, 90)
    w2 = robot.set_gripper(ARM, 0)
    print(label, "squeeze2 w=%.2f" % w2, "tcp", np.round(tcp(robot), 2))
    if w2 < min_w or w2 > max_w:
        robot.set_gripper(ARM, 80)
        return None
    return w1, w2


def fmt(d):
    return {k: (np.round(v, 1).tolist() if isinstance(v, np.ndarray) else (round(v, 2) if isinstance(v, float) else v))
            for k, v in d.items() if k not in ("mask", "img")}


def center_along(robot, label, min_w, max_w, iters=6, ref_w=None):
    """Close on the thing between the fingers along the current closing line. A free thing is pushed to the
    middle; one that does not move stops the nearer finger (and pushes the compliant arm a little toward
    itself): then step the arm toward it and close again, until it is centred. With ref_w (its width, known
    from another line) the step is the whole offset (w - ref_w)/2. Leaves the gripper closed; returns w."""
    w = 0.0
    for it in range(iters):
        p0 = tcp(robot)
        w = robot.set_gripper(ARM, 0)
        s1 = st(robot)
        p1 = np.array(s1["tcp_mm"], float)
        d = p1 - p0
        d[2] = 0.0
        nd = float(np.linalg.norm(d))
        print(label, "w %.2f push %.2f" % (w, nd))
        # (a thing that does not move reads wider than it is while it is off-centre: judge w once centred)
        if w < min_w or w > max_w + 25:
            return w
        excess = (w - ref_w) if ref_w is not None else 0.0
        if nd < 0.35 and excess < 0.8:
            return w
        c = np.array(s1["closing_dir"], float)
        c[2] = 0.0
        c = c / max(np.linalg.norm(c), 1e-6)
        if nd >= 0.1:
            u = d / nd
        else:                               # the force on the arm points toward the thing
            fo = np.array(s1["force_n"], float)
            u = c * (1.0 if float(fo @ c) >= 0 else -1.0)
        step = 1.8 * nd
        if ref_w is not None and excess > 0.8:
            step = max(step, excess / 2 + nd)
        robot.set_gripper(ARM, min(80, w + 20))
        t = p0 + min(step, 15.0) * u
        mv(robot, (t[0], t[1], p0[2]))
    return w


# ------------------------------------------------------------------ main
def unused_first_main(robot, task):
    """(the first version's main loop, kept for reference only: run() below is the skill)"""
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = survey(robot)
    f = S["f"]
    camoff = cam_offset(f, robot)
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1))
    for s in S["sticks"][:5]:
        print("STICKC", fmt(s))
    for r in S["rings"][:5]:
        print("RINGC", fmt(r))
    ann = f.rgb.copy()
    for s in S["sticks"][:4]:
        cv2.circle(ann, s["uv"], 6, (255, 0, 0), 1)
    for r in S["rings"][:4]:
        cv2.circle(ann, r["uv"], 10, (0, 255, 0), 1)
    robot.show("survey", ann)
    # ---------------- find the ring (a hole that is lower than its top)
    ring = None
    for rc in S["rings"][:5]:
        fc = closeup(robot, rc["xyz"][:2], rc["top"], camoff)
        cy = ring_closeup(fc, rc["xyz"][:2], rc["top"], rc["eqd"])
        print("RING closeup", None if cy is None else fmt(cy))
        if cy and cy["hole_d"] >= 3.5 and cy["low"] > 0.5 and 16 <= cy["od"] <= 72 and \
                (cy["sup"] is None or cy["top"] - cy["sup"] >= 8):
            ring = cy
            break
    if ring is None:
        print("no ring found")
        robot.home(ARM)
        return
    # ---------------- find the stick
    stick = None
    for sc in S["sticks"][:6]:
        if np.linalg.norm(sc["xyz"][:2] - ring["xy"]) < ring["od"] / 2 + 10:
            continue
        fs = closeup(robot, sc["xyz"][:2], sc["top"], camoff)
        sk = stick_closeup(fs, sc["xyz"][:2], sc["top"])
        print("STICK closeup", None if sk is None else fmt(sk))
        if sk and sk["eqd"] <= 34 and sk["lrise"] >= 18 and np.linalg.norm(sk["xy"] - ring["xy"]) > ring["od"] / 2 + 8:
            if sk["base"] is None:
                sk["base"] = sc["ped"]
            sk["base"] = max(sk["base"], sc["ped"])
            stick = sk
            break
    if stick is None:
        print("no stick found")
        robot.home(ARM)
        return
    base_top, stick_top = stick["base"], stick["top"]
    mv(robot, (stick["xy"][0], stick["xy"][1], stick_top + 50), closing=C0)
    zg = max(base_top + 16 + 9, stick_top - 24, 14.0)
    zg = min(zg, stick_top - 10)
    r = mv(robot, (stick["xy"][0], stick["xy"][1], zg), stop=True)
    if r.get("why"):
        print("stick descent stopped", r.get("why"), np.round(tcp(robot), 1))
        p = tcp(robot)
        mv(robot, (p[0], p[1], p[2] + 3))
    sq = squeeze_center(robot, "STICK", 2.0, 30.0)
    Sxy = tcp(robot)[:2].copy()
    robot.set_gripper(ARM, 80)
    t = truth(robot)
    print("STICK at", np.round(Sxy, 2), "sq", sq)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 45, p[2])))
    if sq is None:
        print("stick squeeze failed")
        robot.home(ARM)
        return
    bias = stick["xy"] - Sxy
    print("closeup bias", np.round(bias, 2))
    if np.linalg.norm(bias) > 15:
        bias = np.zeros(2)
    # ---------------- grasp the cylinder
    cxy = ring["xy"] - bias
    sup = ring["sup"] if ring["sup"] is not None else ring["top"] - 30
    ctop = ring["top"]
    mv(robot, (cxy[0], cxy[1], ctop + 40), closing=C0)
    zc = max(sup + 20, 13.0)        # grip low: pushing a tall ring high would tip it
    r = mv(robot, (cxy[0], cxy[1], zc), stop=True)
    if r.get("why"):
        print("cyl descent stopped", r.get("why"), np.round(tcp(robot), 1))
    sq = squeeze_center(robot, "CYL", 8.0, 78.0)
    if sq is None:
        print("cylinder grasp failed")
        robot.set_gripper(ARM, 80)
        robot.home(ARM)
        return
    C = tcp(robot)
    db = C[2] - sup
    t = truth(robot)
    if t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2), "db true", round(C[2] - t["w_cyl"][2], 1),
              "db est", round(db, 1))
    # ---------------- insert
    zt = max(stick_top, S["zmax"], ctop) + db + 35
    mv(robot, (C[0], C[1], zt))
    mv(robot, (Sxy[0], Sxy[1], zt))
    z1 = stick_top + 12 + db
    mv(robot, (Sxy[0], Sxy[1], z1))
    zend = max(base_top + 2 + db, base_top + 19, 13.0, stick_top - 95)
    r = mv(robot, (Sxy[0], Sxy[1], zend), stop=True)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zend", round(zend, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stick_top, 1))
    if r.get("why") and zn - db > stick_top - 10:
        found = False
        for rad in (0.7, 1.4, 2.1, 2.8, 3.5, 4.5):
            for k in range(8):
                a = 2 * math.pi * k / 8 + rad
                x, y = Sxy[0] + rad * math.cos(a), Sxy[1] + rad * math.sin(a)
                mv(robot, (tcp(robot)[0], tcp(robot)[1], zn + 1.5))
                mv(robot, (x, y, zn + 1.5))
                mv(robot, (x, y, zn - 6), stop=True)
                if tcp(robot)[2] < zn - 3:
                    found = True
                    break
            if found:
                break
        print("search", found, np.round(tcp(robot), 2))
        if found:
            p = tcp(robot)
            mv(robot, (p[0], p[1], zend), stop=True)
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 40, p[2] + 10)))
    robot.home(ARM)
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("JUDGE", j)


# ================================================================== v7 main
HOME = (-478.0, 41.0, 350.0)


def finish(robot, z_clear=0.0):
    """open, lift straight up clear of everything, go to the start pose (straight, then by the joints)"""
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    zc = min(700.0, max(z_clear + 70.0, p[2] + 10, 300.0))
    mv(robot, (p[0], p[1], zc))
    for z in (max(zc, HOME[2]), 500.0):
        mv(robot, (HOME[0], HOME[1], z), closing=C0)
        r = robot.home(ARM)
        if r.get("moved") or r.get("done"):
            return
        print("home refused:", r.get("why"))
    mv(robot, HOME, closing=C0)
def find_ring(robot, S, camoff):
    """the ring candidates, best first: a hole seen from close by (depth) and what the survey said (SAM,
    size, roundness). Stops looking early at a clear one."""
    found = []
    for rc in S["rings"][:6]:
        fc = closeup(robot, rc["xyz"][:2], rc["top"], camoff)
        cy = ring_closeup(fc, rc["xyz"][:2], rc["top"], rc["eqd"])
        print("RING closeup", None if cy is None else fmt(cy))
        if cy is None:
            continue
        if len(found) < 3:
            robot.show("ring_closeup", cy["img"])
        plaus = 16 <= cy["od"] <= 72 and (cy["sup"] is None or cy["top"] - cy["sup"] >= 8)
        if not plaus:
            continue
        holed = cy["deep"] >= 0.5 or (cy["hd2"] >= 2.5 and cy["lowc"] >= 0.8)
        hole_ev = 2.0 if holed else min(1.0, cy["deep"] + cy["hd2"] / 5 + cy["lowc"] * 0.3)
        cy["rank"] = hole_ev + min(rc["score"], 3.5)
        found.append(cy)
        if holed and rc["score"] >= 1.5:
            break
    found.sort(key=lambda c: -c["rank"])
    print("ring candidates", [(np.round(c["xy"], 0).tolist(), round(c["rank"], 2)) for c in found])
    return found


def find_stick(robot, S, camoff, ring):
    """yields the stick candidates that look like a stick from close by, those on a plate first"""
    deferred = []
    cands = []
    for sc in S["sticks"]:              # not the ring's wall, one per place
        if np.linalg.norm(sc["xyz"][:2] - ring["xy"]) < ring["od"] / 2 + 10:
            continue
        if any(np.linalg.norm(sc["xyz"][:2] - o["xyz"][:2]) < 20 for o in cands):
            continue
        cands.append(sc)
    for sc in cands[:7]:
        fs = closeup(robot, sc["xyz"][:2], sc["top"], camoff)
        sk = stick_closeup(fs, sc["xyz"][:2], sc["top"], S["coef"])
        print("STICK closeup", None if sk is None else fmt(sk))
        if sk and sk["eqd"] <= 34 and sk["lrise"] >= 18 and np.linalg.norm(sk["xy"] - ring["xy"]) > ring["od"] / 2 + 8:
            if sk["base"] is None:
                sk["base"] = sc["ped"]
            sk["base"] = max(sk["base"], sc["ped"])
            c = S["coef"]
            ped = sk["base"] - (c[0] * sk["xy"][0] + c[1] * sk["xy"][1] + c[2])
            print("  pedestal %.1f plate %.2f" % (ped, sk["plate"]))
            if ped >= 3.0 and sk["plate"] >= 0.4:
                yield sk
            else:
                deferred.append(sk)       # no plate under it: perhaps not a stand
    deferred.sort(key=lambda s: -s["plate"])
    for sk in deferred:
        yield sk


def squeeze_stick(robot, stick, HG=None):
    """Centre the stick between the fingers along a, b, a (low on the stick, so that the stand does not
    tip). Returns (xy, widths) or None."""
    base_top, stick_top = stick["base"], stick["top"]
    x, y = stick["xy"]
    zg = min(max(base_top + 25, 14.0), stick_top - 10)
    th = -90.0
    if HG is not None:
        th, clr = best_closing(HG, np.array([x, y]), 24.0, zg - 16, extra=(45, 90))
        print("stick closing angle", th, "clearance %.1f" % clr)
    ws = None
    for attempt in range(2):
        ws = squeeze_at(robot, "STICK", (x, y), stick_top + 50, zg, [th, th + 90, th + 45, th - 45, th + 22, th - 22],
                        2.0, 26.0, 3)
        if ws is not None:
            break
        robot.set_gripper(ARM, 80)
        p = tcp(robot)
        x, y = p[0], p[1]
        zg = min(zg + 15, stick_top - 10)   # perhaps the base or something big: try higher
    if ws is None:
        return None
    Sxy = tcp(robot)[:2].copy()
    print("STICK widths", np.round(ws, 2), "at", np.round(Sxy, 2))
    robot.set_gripper(ARM, 80)
    p = tcp(robot)
    mv(robot, (p[0], p[1], max(stick_top + 45, p[2])))
    if not all(2 <= w <= 26 for w in ws):
        return None
    return Sxy, ws


def grasp_ring(robot, cxy, ring, camoff=None, bias=None, HG=None):
    for attempt in range(3):
        sup = ring["sup"] if ring["sup"] is not None else ring["top"] - 30
        # grip low (pushing a tall ring high would tip it); fingertips 3-6 mm above the support
        # (the wrist camera's heights can be ~5 mm off: keep the fingertips 6-8 mm above the support seen)
        zc = max(sup + 16.0 + min(8.0, max(6.0, (ring["top"] - sup) / 2)), 13.0)
        th = -90.0
        if HG is not None:
            th, clr = best_closing(HG, np.asarray(cxy, float), max(ring["od"] / 2 + 8, 30.0), zc - 16, extra=(45, 90))
            print("ring closing angle", th, "clearance %.1f" % clr)
        sq = squeeze_at(robot, "CYL", cxy, ring["top"] + 40, zc, [th, th + 90, th + 45, th - 45, th + 22, th - 22],
                        8.0, 78.0, 2)
        good = sq is not None and abs(sq[0] - sq[1]) <= 1.5 and abs(sq[1] - ring["od"]) <= 9
        if good:
            break
        print("CYL grasp not consistent", sq, "od est", round(ring["od"], 1))
        robot.set_gripper(ARM, 80)
        p = tcp(robot)
        mv(robot, (p[0], p[1], ring["top"] + 40))
        if camoff is None or attempt == 1 or robot.time_s() > 400:
            return None
        fc = closeup(robot, p[:2], ring["top"], camoff)
        cy = ring_closeup(fc, p[:2], ring["top"], ring["od"])
        print("RING re-closeup", None if cy is None else fmt(cy))
        if cy is None or not (16 <= cy["od"] <= 72):
            return None
        ring = cy
        cxy = cy["xy"] - (bias if bias is not None else 0.0)
    C = tcp(robot)
    t = truth(robot)
    if t:
        print("CYL truth err", np.round(np.array(t["w_cyl"][:2]) - C[:2], 2), "db true", round(C[2] - t["w_cyl"][2], 1),
              "db est", round(C[2] - sup, 1))
    return dict(C=C, db=C[2] - sup, w=sq)


def insert(robot, Sxy, stick, db, zt, od=40.0, HG=None):
    base_top, stick_top = stick["base"], stick["top"]
    C = tcp(robot)
    mv(robot, (C[0], C[1], zt))
    mv(robot, (Sxy[0], Sxy[1], zt))
    zend = max(base_top + 5 + db, base_top + 23, 13.0, stick_top - 95)   # then the ring drops the rest
    if HG is not None:
        c = np.array(cdir(robot)[:2])
        a0 = math.degrees(math.atan2(c[1], c[0]))
        th, clr = best_closing(HG, np.asarray(Sxy, float), max(od / 2 + 4, 24.0), zend - 16,
                               angles=[a0 + d for d in (0, 30, -30, 60, -60, 90)], extra=())
        rot = ((th - a0 + 90) % 180) - 90
        print("insert closing angle", round(th), "turn", round(rot), "clearance %.1f" % clr)
        if abs(rot) > 5:
            turn(robot, rot)
    z1 = stick_top + 12 + db
    # the loaded arm sags: correct the command until the measured TCP (= the ring's axis) is over the stick
    cmd = np.array([Sxy[0], Sxy[1], z1], float)
    mv(robot, cmd)
    for _ in range(4):
        err = np.asarray(Sxy, float) - tcp(robot)[:2]
        if np.linalg.norm(err) < 0.15:
            break
        cmd[:2] += err
        mv(robot, cmd)
    corr = cmd[:2] - np.asarray(Sxy, float)
    print("insert: xy correction for the sag", np.round(corr, 2), "residual", np.round(np.asarray(Sxy) - tcp(robot)[:2], 2))
    # hold it only until it is well onto the stick, then let it slide down by itself (a long held descent
    # would press it against the stick wherever the arm drifts)
    zeng = max(zend, stick_top - 15 + db)
    r = mv(robot, (cmd[0], cmd[1], zeng), stop=True)
    zn = tcp(robot)[2]
    print("insert", r.get("why"), "z", round(zn, 1), "zeng", round(zeng, 1), "bottom", round(zn - db, 1),
          "stick_top", round(stick_top, 1))
    if not (r.get("why") and zn - db > stick_top - 8):
        return True
    for rad in (0.5, 1.0, 1.5, 2.1, 2.8):
        nk = 6 if rad < 1 else (8 if rad < 2 else 12)
        for k in range(nk):
            if robot.time_s() > 470:
                print("search: out of time")
                return False
            a = 2 * math.pi * k / nk + rad
            x, y = Sxy[0] + corr[0] + rad * math.cos(a), Sxy[1] + corr[1] + rad * math.sin(a)
            mv(robot, (x, y, zn + 2.0))
            mv(robot, (x, y, zn - 6), stop=True)
            if tcp(robot)[2] < zn - 3:
                print("search found at offset", round(rad, 1), round(math.degrees(a) % 360))
                if tcp(robot)[2] > zeng + 1:
                    mv(robot, (x, y, zeng), stop=True)     # the commanded xy: the measured one is off by the sag
                return True
    print("search failed")
    return False


def run(robot, task):
    try:
        main(robot, task)
    except Exception as e:                  # whatever went wrong: let go and go home
        print("ERROR", repr(e))
        try:
            finish(robot, TRAVEL["z"])
        except Exception as e2:
            print("ERROR in finish", repr(e2))


def main(robot, task):
    T0 = truth(robot)
    if T0:
        print("TRUTH", {k: [round(float(x), 1) for x in v] for k, v in T0.items()})
    robot.set_gripper(ARM, 80)
    S = survey(robot)
    f = S["f"]
    camoff = cam_offset(f, robot)
    TRAVEL["z"] = min(600.0, max(200.0, S["zmax"] + 60.0))    # fingertips 16 mm below the TCP: 44 mm clear
    print("plane", np.round(S["coef"], 4), "zmax", round(S["zmax"], 1), "camoff", np.round(camoff, 1))
    for s in S["sticks"][:3]:
        print("STICKC", fmt(s))
    for r in S["rings"][:3]:
        print("RINGC", fmt(r))
    ann = f.rgb.copy()
    for s in S["sticks"][:4]:
        cv2.circle(ann, s["uv"], 6, (255, 0, 0), 1)
    for r in S["rings"][:4]:
        cv2.circle(ann, r["uv"], 10, (0, 255, 0), 1)
    robot.show("survey", ann)
    rings = find_ring(robot, S, camoff)
    if not rings:
        print("no ring found")
        finish(robot, S["zmax"])
        return
    ring = rings[0]
    stick, sq = None, None
    for cand in find_stick(robot, S, camoff, ring):
        sq = squeeze_stick(robot, cand, S["HG"])
        # round (same width both ways) and thin enough for the hole we saw (seen holes look ~4 mm too small)
        fits = ring["hd2"] < 3 or max(sq[1]) <= ring["hd2"] + 7 if sq is not None else False
        if sq is not None and max(sq[1]) - min(sq[1]) <= 1.5 and fits:
            stick = cand
            break
        print("not a round stick:", None if sq is None else np.round(sq[1], 1))
        if robot.time_s() > 300:
            break
    if stick is None:
        print("no stick found")
        finish(robot, S["zmax"])
        return
    cxy = np.array(ring["xy"], float)
    bias_done = False
    bias = np.zeros(2)
    ok = False
    for attempt in range(2):
        if attempt > 0:
            sq = squeeze_stick(robot, stick, S["HG"])
        if sq is None:
            print("stick squeeze failed")
            break
        Sxy = sq[0]
        if not bias_done:
            # the camera's calibration error: look again at the stick, now that we know where it is
            # (the squeeze may have dragged the stand, so the first look does not tell)
            fs2 = closeup(robot, Sxy, stick["top"], camoff)
            sk2 = stick_closeup(fs2, Sxy, stick["top"], S["coef"])
            b = (sk2["xy"] - Sxy) if sk2 is not None else np.zeros(2)
            print("first look - squeeze", np.round(stick["xy"] - Sxy, 2))
            print("closeup bias", np.round(b, 2))
            if np.linalg.norm(b) < 15:
                bias = b
                cxy = cxy - bias
            bias_done = True
        stick["xy"] = Sxy
        g = grasp_ring(robot, cxy, ring, camoff, bias, S["HG"])
        while g is None and len(rings) > 1 and robot.time_s() < 420:
            rings = rings[1:]               # that was not it: the next candidate
            ring = rings[0]
            print("cylinder grasp failed: trying the next candidate at", np.round(ring["xy"], 1))
            cxy = np.array(ring["xy"], float) - bias
            g = grasp_ring(robot, cxy, ring, camoff, bias, S["HG"])
        if g is None:
            print("cylinder grasp failed")
            break
        db = g["db"]
        zt = max(stick["top"], S["zmax"], ring["top"]) + db + 35
        if insert(robot, Sxy, stick, db, zt, g["w"][-1], S["HG"]):
            ok = True
            w = g["w"][-1]
            for dw in (1.5, 5.0):            # let go gently: first just loosen, then open
                robot.set_gripper(ARM, min(80.0, w + dw))
                robot.wait(0.3)
            break
        # put the cylinder back where it was and measure the stick again
        C = g["C"]
        mv(robot, (tcp(robot)[0], tcp(robot)[1], zt))
        mv(robot, (C[0], C[1], zt))
        mv(robot, (C[0], C[1], C[2] + 1.0), stop=True)
        robot.set_gripper(ARM, 80)
        cxy = C[:2].copy()
        p = tcp(robot)
        mv(robot, (p[0], p[1], ring["top"] + 40))
    finish(robot, max(stick["top"], S["zmax"]))
    j = None
    try:
        j = robot.judge()
    except Exception:
        pass
    print("RESULT", ok, "JUDGE", j)
```

In your workspace: program.py is v38; worlds/ holds your worlds (w0.py, ...) as files to go on from; notes.md holds those notes.
