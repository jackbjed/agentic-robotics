This is the REAL robot, not a simulation. What differs from the description above:

- The cameras are real RealSense D415s. A wrist-camera frame often has no depth on a thin object seen from close (the
  top of a stick about 13 mm across from 350 mm away: nan there, and beside it), and can hold small patches of wrong
  depth, mostly near the image's edges. The scene camera, farther away, usually has depth on such an object. Check a
  measurement against another view before moving on it.
- An ordinary move ends with the arm settling on its target for up to 2 s, and still a little off it: about 1 to 3 mm,
  sometimes more in z. robot.state("left")["tcp_mm"] is where the arm really is.
- move(..., stop_on_contact=True) is the arm's own guarded line at 5 mm/s (200 mm take 40 s). The arm's joints have
  friction, and on this arm such a move very often stops within its first millimetres and answers why = "contact" with
  nothing touched. A stop is a contact only if robot.state() puts the tool where something can be touched. A long way
  down is better made as an ordinary move to just above where a touch can happen.
- An ordinary move that presses on something is not stopped before the arm's safety stop (about 35 N: the move then
  answers a text that starts with "SAFETY STOP"; the third one ends the episode). Do not push with ordinary moves.
- robot.state("left")["force_n"] is the arm's estimate from its joint torques, set to zero at the start: it is noisy and
  drifts by a few N as the arm changes its pose.
- robot.show(label, image) keeps an image (H x W x 3 uint8 RGB) for the operator, at most 12 an episode: use it to show
  what the program detected. It costs no robot time.
- Nothing judges the result but the operator, who watches: at the end the arm must have let go and be clear of the
  objects.
