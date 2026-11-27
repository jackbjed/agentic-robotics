"""Messages between a host and its sandboxed child: one length-prefixed blob = a JSON document + numpy arrays in .npy
form (allow_pickle=False). Encodes None, bool, int, float, str, list, tuple, dict with string keys, numpy arrays and
scalars, and camera frames. Nothing here unpickles or evaluates anything."""
import io
import json
import struct

import numpy as np

MAX_BYTES = 512 * 1024 * 1024
_OK_KINDS = "biuf"                                                 # bool, int, unsigned, float arrays only (no object arrays)


class Closed(EOFError):
    """The other side is gone."""


def _enc(x, blobs, frame_cls):
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, np.integer)) and not isinstance(x, (bool, np.bool_)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if np.isfinite(v) else {"~f": repr(v)}
    if isinstance(x, np.ndarray):
        if x.dtype.kind not in _OK_KINDS:
            raise TypeError(f"an array of dtype {x.dtype} cannot be sent")
        blobs.append(np.ascontiguousarray(x)); return {"~nd": len(blobs) - 1}
    if frame_cls is not None and isinstance(x, frame_cls):
        return {"~frame": _enc(x.wire(), blobs, frame_cls)}
    if isinstance(x, tuple):
        return {"~t": [_enc(v, blobs, frame_cls) for v in x]}
    if isinstance(x, list):
        return [_enc(v, blobs, frame_cls) for v in x]
    if isinstance(x, dict):
        if not all(isinstance(k, str) for k in x):
            return {"~d": [[_enc(k, blobs, frame_cls), _enc(v, blobs, frame_cls)] for k, v in x.items()]}
        return {"~o": {k: _enc(v, blobs, frame_cls) for k, v in x.items()}}
    raise TypeError(f"a value of type {type(x).__name__} cannot be sent")


def _dec(x, blobs, frame_cls):
    if isinstance(x, list):
        return [_dec(v, blobs, frame_cls) for v in x]
    if isinstance(x, dict):
        (k, v), = x.items()
        if k == "~o":
            return {a: _dec(b, blobs, frame_cls) for a, b in v.items()}
        if k == "~t":
            return tuple(_dec(b, blobs, frame_cls) for b in v)
        if k == "~nd":
            return blobs[int(v)]
        if k == "~f":
            return float(v)
        if k == "~d":
            return {_hashable(_dec(a, blobs, frame_cls)): _dec(b, blobs, frame_cls) for a, b in v}
        if k == "~frame":
            o = _dec(v, blobs, frame_cls)
            if frame_cls is None:
                raise TypeError("a frame was sent where none is expected")
            return frame_cls.from_wire(o)
        raise ValueError("unknown tag")
    return x


def _hashable(k):
    return tuple(_hashable(v) for v in k) if isinstance(k, (list, tuple)) else k


def dumps(msg, frame_cls=None):
    blobs = []
    head = json.dumps(_enc(msg, blobs, frame_cls), allow_nan=False).encode()
    out = io.BytesIO()
    out.write(struct.pack(">II", len(head), len(blobs))); out.write(head)
    for b in blobs:
        buf = io.BytesIO(); np.lib.format.write_array(buf, b, allow_pickle=False)
        out.write(struct.pack(">Q", buf.getbuffer().nbytes)); out.write(buf.getvalue())
    return out.getvalue()


def loads(data, frame_cls=None):
    f = io.BytesIO(data)
    nh, nb = struct.unpack(">II", f.read(8))
    head = json.loads(f.read(nh).decode())
    blobs = []
    for _ in range(nb):
        n, = struct.unpack(">Q", f.read(8))
        a = np.lib.format.read_array(io.BytesIO(f.read(n)), allow_pickle=False)
        if a.dtype.kind not in _OK_KINDS:
            raise TypeError("an array of that dtype is not accepted")
        blobs.append(a)
    return _dec(head, blobs, frame_cls)


class Channel:
    def __init__(self, sock, frame_cls=None):
        self.sock, self.frame_cls = sock, frame_cls

    def send(self, msg):
        data = dumps(msg, self.frame_cls)
        try:
            self.sock.sendall(struct.pack(">Q", len(data)) + data)
        except OSError as e:
            raise Closed(str(e))

    def _read(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                c = self.sock.recv(min(n - len(buf), 1 << 20))
            except OSError as e:
                raise Closed(str(e))
            if not c:
                raise Closed("the other side closed the connection")
            buf += c
        return bytes(buf)

    def recv(self):
        n, = struct.unpack(">Q", self._read(8))
        if n > MAX_BYTES:
            raise Closed(f"a message of {n} bytes is refused")
        return loads(self._read(n), self.frame_cls)
