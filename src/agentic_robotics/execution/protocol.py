"""The wire format between a skill program (sandboxed) and its world server: ZeroMQ REQ/REP over Unix sockets (ipc://),
each message = a JSON header + raw numpy buffers (floats survive the JSON round trip exactly: a trajectory sent through
the socket is bit-identical). Ported from embodied-agents src/ea/protocol.py (2026-11-09), unchanged except this text.

Requests a skill program may send (the world answers each with ok=True, or ok=False + why):
  hello                                      -> the server's identity (twin), motion allowed
  task                                       -> the task: kind, instruction, object name (nothing about the scene)
  state                                      -> joints q, dq, gripper width, f_ext (the arm's own force estimate)
  execute  t, q, grip, settle, phase, guard  -> runs a joint trajectory (limits checked first, server side)
  idle     T                                 -> time passes, the arm holds
  wait_gripper timeout                       -> until the fingers stop
  now                                        -> the robot clock (twin: sim time)
  camera   cam ("d415" | "L_wrist")          -> rgb uint8 (H,W,3), depth float32 (H,W) m (inf = none), K
  segment  phrase, [rgb]                     -> masks of that phrase in a frame this world delivered (perception service)
  segment_points pts, labels, [rgb]          -> masks at clicked pixels
  vlm      prompt, [rgb...]                  -> the experiment's model, one call per episode (perception/vlm.py)
  vlm_converse input, instructions, tools, previous, [rgb...]   -> one turn of a conversation (the VLM baselines only)
  event    what, data                        -> appended to the episode's event log
  done     result, usage                     -> the program's last word (the server judges and writes the record)
"""
from __future__ import annotations

import json

import numpy as np


def _default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)


def send(sock, header, arrays=None):
    arrays = [a for a in (arrays or []) if a is not None]
    header = dict(header, _arrays=[dict(dtype=str(a.dtype), shape=list(a.shape)) for a in arrays])
    sock.send_multipart([json.dumps(header, default=_default).encode()] + [np.ascontiguousarray(a).tobytes() for a in arrays])


def recv(sock):
    parts = sock.recv_multipart()
    header = json.loads(parts[0])
    arrays = [np.frombuffer(b, dtype=d["dtype"]).reshape(d["shape"]).copy() for b, d in zip(parts[1:], header.pop("_arrays", []))]
    return header, arrays


def jdump(o, **kw):
    return json.dumps(o, default=_default, **kw)
