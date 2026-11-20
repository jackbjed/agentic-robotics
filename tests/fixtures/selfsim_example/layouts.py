"""A crude layout sampler for the platform's tests."""
import math

OBJECTS = {"red tape roll": "tape_red", "yellow tape roll": "tape_yellow", "white tape roll": "tape_white",
           "screwdriver": "screwdriver", "black block with holes": "block"}
TARGETS = ["red tape roll", "yellow tape roll", "white tape roll", "screwdriver"]


def free_spot(rng, taken, r, box=(-0.46, -0.15, -0.18, 0.20)):
    for _ in range(500):
        x, y = rng.uniform(box[0], box[1]), rng.uniform(box[2], box[3])
        if all(math.hypot(x - a, y - b) > r + rb for a, b, rb in taken):
            return x, y
    raise RuntimeError("no free spot")


def sample(task, rng):
    place, taken, joints = {}, [], {}
    if task in ("place-box", "close-box"):
        bx, by = rng.uniform(-0.36, -0.28), rng.uniform(0.04, 0.10)
        place["box"] = [bx, by, rng.uniform(-0.3, 0.3)]
        taken.append((bx, by, 0.12))
        joints["box_lid"] = rng.uniform(1.8, 2.0) if task == "place-box" else rng.uniform(1.3, 2.0)
    if task == "close-box":
        target = "white box"
        others = list(rng.choice(TARGETS, size=int(rng.integers(0, 3)), replace=False))
    else:
        target = str(rng.choice(TARGETS))
        others = [o for o in OBJECTS if o != target]
        others = list(rng.choice(others, size=int(rng.integers(1, 4)), replace=False))
    for name in ([target] if target in OBJECTS else []) + others:
        x, y = free_spot(rng, taken, 0.045)
        taken.append((x, y, 0.045))
        place[OBJECTS[name]] = [x, y, rng.uniform(-math.pi, math.pi)]
    return dict(target=target, place=place, joints=joints)
