"""The execution boundary for agent-written code (skill programs and practice-world files).

Agent-written code never runs in a process that holds the simulator, the judge, the true state or any file of the
experiment. It runs in a child process inside a bubblewrap sandbox (the project's sandbox: unprivileged namespaces, no
network, a read-only view of the system and of the Python environment, nothing of the repository or the home
directory, no writable file) and reaches its host ONLY through one socket that carries

  a skill program:   the documented robot calls (look, state, move, turn, home, wait, time_s, set_gripper, segment,
                     show), their results, and what the program prints;
  a world file:      scene(scene, rng) -> its scene dict, and success(state), whose `state` answers the documented
                     queries about the TRUE state of the practice world (the author's own world: that is permitted).

wire.py     the messages (JSON + numpy arrays in .npy form; never pickle: the host decodes what untrusted code sent)
frame.py    the camera frame a program gets (plain numpy; used on both sides)
child.py    what runs inside the sandbox
host.py     what the runners call: run_program(...), World(...)"""
