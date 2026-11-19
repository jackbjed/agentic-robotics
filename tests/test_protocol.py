import numpy as np
import zmq

from agentic_robotics.execution import protocol


def test_roundtrip_exact(tmp_path):
    c = zmq.Context(); a = c.socket(zmq.PAIR); b = c.socket(zmq.PAIR)
    a.bind(f"ipc://{tmp_path}/s"); b.connect(f"ipc://{tmp_path}/s")
    t = (np.arange(50) * 0.0333333).tolist(); q = np.random.default_rng(0).normal(size=(50, 7))
    img = np.random.default_rng(1).integers(0, 255, (48, 64, 3), dtype=np.uint8)
    protocol.send(a, dict(cmd="execute", t=t, q=q.tolist()), [img])
    h, arr = protocol.recv(b)
    assert h["t"] == t and np.array_equal(np.asarray(h["q"]), q) and np.array_equal(arr[0], img)
