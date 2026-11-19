"""The press skill (11-02, user: "press a button at the pixel Astra gives, e.g. the button of the coffee machine").
Shared by task_box.BoxProgram (the twin; so also real_box.py's programs) and press_bench.py (the kettle's lid button in
model/twin_6508_tasks.xml); real_box.RealBoxProgram replaces only press_down (the robot's arm is compliant).

  seed     Astra's pixel in the scene D415 image -> the camera's ray through it and the nearest surface within 6 px of
           it (else 12 px). At ~1.3 m a 16 mm button is ~8 px: its depth is often a nearer neighbour's (the D415 spreads
           an edge ~4 px; the kettle's lid in front of its handle put the point 10-12 cm short along the ray). So the
           button is on the ray, at or beyond the seed
  high     closed gripper, the wrist camera straight down over the ray 6 cm beyond the seed, high enough to see the ray
  look     from the seed to 12 cm beyond (a 26 x 26 cm height map, 2 mm cells). Caps: the parts of the map that stand
           out when it is cut at each level (1 mm steps), 5-35 mm across, >= 3 mm (here; 1.5 mm in the close look)
           over the ring 4-6 mm around them, with one top face (two buttons merged at a lower cut are not one cap).
           The cap nearest the scene camera's ray (its 3D distance to it: the pixel's error moves the ray, not along
           it), within 40 mm (the scene camera was 13-19 mm off the wrist camera on the robot, Astra's marks 15-20 mm).
           Not sure (no cap that near, or a second one within 12 mm as near): Astra marks the button in this view's
           colour image (one more call; PRESS_WRIST_MARK=0: never): the cap within 15 mm of the mark, else the mark
  close    the wrist camera centred on it from the D415's shortest range + 2.5 cm: the cap within 15 mm of it (else
  look     the surface there; nothing found at all: where the ray meets the height map). Its top face tilted > 35 deg
           (a front-panel button: the wrist camera sees it edge-on): refused, this skill presses from above
  room     per hand angle (every 15 deg; a full turn = either fingertip): how far the fingertip can go in before
           another part of the closed fin-ray fingers, hand or camera (clutter_bench's boxes, 3 mm bigger for the
           hand-eye error) meets something that is not the button (press_angle); the angles with the most room, then
           the smallest joint change; < 2 mm room at every angle (as measured): refused
  press    one fingertip's flat underside over the button: the TCP 8.5 mm off it along the closing axis (the V between
           the closed tips wedges a button: task_fast, 10-23). From 1 cm over the top, down (press_down): the twin,
           1 mm steps: the touch (> 0.3 N), then on to 8 mm past it or > 6 N (from the touch: the top the wrist camera
           reads can be 2-4 mm off); it gave < 2 mm before 6 N (something hard: a few mm of hand-eye error put the pad
           on a neighbour): once more from another angle. The robot (real_box): one slow line to a set depth under
           the top, the arm's compliance limiting the force
  after    fingers 10 mm apart (nothing wedged between the tips), up 10 cm. Events: "button (high look)", "button",
           "press angle", "pressed" (how far the tip went in after the touch, the peak force)
"""
from __future__ import annotations

import math
import os

import cv2
import numpy as np

import stack_bench as B
import task_bench as T
import task_fast as F

GRID = 0.002                 # height map cell (m)
WIN = 0.08                   # half size of the height map around the seed
CAP = (0.005, 0.035)         # a button's cap: across (m)
RISE = 0.0015                # ... standing at least this much over what is around it
NEAR = 0.040                 # the cap at most this far from the scene camera's ray (real 10-29: the scene camera
                             # 13-19 mm off the wrist camera after calibration; Astra's marks 15-20 mm off)
TILT_MAX = 35.0              # deg: steeper top faces are not pressed from above
PAD_OFF = 0.0085             # the pressing fingertip's centre off the TCP, along the closing axis
CLEAR = 0.003                # room check: points this much over the button's top are obstacles
SLACK = 0.003                # room check: the hand's parts this much bigger all round (the hand-eye error: the
                             # realistic D415 model's wrist mount 3 mm / 1 deg put the press point 4-7 mm off)
PRESS_IN = 0.008             # (twin) the fingertip at most this far under the button's top
MIN_TRAVEL = 0.002           # a hand angle must leave the fingertip at least this far to go in
F_TOUCH, F_STOP = 0.3, 6.0   # (twin) N: the first touch; stop pressing
WIDE = 0.13                  # the first (high) look: half size of its height map, around the ray 6 cm beyond the seed
AHEAD = 0.06
NEAR_REF = 0.015             # the close look: the cap within this of the one the high look found
RISE_WIDE = 0.003            # the high look's caps rise at least this (its depth is noisier: a 1.7 mm bump on the
                             # kettle's lid, nearer the ray than the button, was taken for it)
HARD = 0.002                 # (twin) a press that gave less than this after the touch before F_STOP hit something
                             # hard (not the button: a few mm of hand-eye error put the pad on its neighbour): once
                             # more, from another hand angle (>= 60 deg away)
# the closed left hand in the TCP frame (clutter_bench.FINGER_BOXES at opening 0; u = along the closing axis, v =
# across, z up from the TCP): (u from, u to, half width across, bottom above the TCP), both fingers (+-u)
FINGERS = [(-0.001, 0.018, 0.013, -0.016), (0.001, 0.034, 0.013, 0.009), (-0.001, 0.041, 0.013, 0.048), (-0.001, 0.045, 0.018, 0.060)]
HAND = (0.107, 0.033, 0.108)                 # |u| < 107 mm, |v| < 33 mm, from 108 mm above the TCP
CAM = (0.045, 0.095, 0.058, 0.098)           # the wrist D415: v 45..95 mm on the camera's side, |u| < 58, from 98 mm up

WRIST_MARK = os.environ.get("PRESS_WRIST_MARK", "1") == "1"   # not sure which bump is the button: Astra marks it in the
AMBIG = 0.012                # high look's colour image (one more call): no cap near the ray, or the second nearest
                             # within this of the nearest
WRIST_PROMPT = """{task}
This is the robot's wrist camera, looking straight down from about {cm} cm above {obj}, {W}x{H} pixels; pixel
coordinates are x from the left edge, y from the top edge. The robot will press {obj} from above with a fingertip.
Give the centre of the button to press, as seen in this image (null if it is not in the image).
Respond with this JSON object and nothing else:
{{"point": [x, y] or null, "reasoning": "<1 sentence>"}}"""

PROMPT_LINE = ('- press: press a button from above with the closed gripper (a fingertip pushes it down; the skill finds the '
               'button\'s exact top with the wrist camera). Give "point": the centre of the button, as seen in this image.')


# ---------------------------------------------------------------- Astra's pixel -> a seed
def seed_from_pixel(obs, uv, r_px=12, r_fg=6):
    """-> dict(p, ray=(eye, dir, surface point)) for a pixel (full image) of the scene camera, or None (no depth near
    it). The depth: the nearest surface (the 10th percentile + 1 cm, median) within r_fg px of the pixel (a button is
    ~10 px across at the scene camera's ~1.2 m: a mark a few px off it lands on the table behind, ~10 cm farther along
    the ray: 11-02, 10 px off, the wrist then looked at the wrong place in 12 of 24 layouts); no depth there (a dark or
    glancing surface): the nearest window up to r_px with some. The point is on the pixel's own ray."""
    W, Hh, fx, fy, cx, cy = obs["K"]
    d = obs["R"] @ np.array([(uv[0] - cx) / fx, (uv[1] - cy) / fy, 1.0])
    u, v = int(round(uv[0])), int(round(uv[1]))
    if not (0 <= u < W and 0 <= v < Hh):
        return None
    dep = obs["depth"]
    for r in (r_fg, r_px):
        win = dep[max(v - r, 0):v + r + 1, max(u - r, 0):u + r + 1]
        z = win[np.isfinite(win) & (win > 0) & (win < 10)]
        if len(z) >= 5:
            near = z[z < np.percentile(z, 10) + 0.01]
            p = obs["eye"] + d * float(np.median(near))           # (d: camera-frame z = 1)
            return dict(p=p, ray=(obs["eye"].copy(), d / np.linalg.norm(d), p.copy()), depth_px=r)
    return None


# ---------------------------------------------------------------- the button in the wrist depth
def height_map(P, ok, c, half=WIN, cell=GRID):
    """Top surface (max z per cell) of the points within half of c (xy); cells without points NaN; a cell > 4 mm
    over the median of its known 3x3 neighbours (a stray pixel) dropped. -> H (i along x, j along y), and per valid
    point its cell (for going back to the points)."""
    n = int(round(2 * half / cell))
    o = np.asarray(c[:2], float) - half
    rel = P[..., :2] - o
    m = ok & (rel[..., 0] >= 0) & (rel[..., 0] < 2 * half) & (rel[..., 1] >= 0) & (rel[..., 1] < 2 * half) & (P[..., 2] > -0.03)
    Q = P[m]
    ij = np.clip(np.floor((Q[:, :2] - o) / cell).astype(int), 0, n - 1)
    H = np.full((n, n), -np.inf)
    np.maximum.at(H, (ij[:, 0], ij[:, 1]), Q[:, 2])
    H[~np.isfinite(H)] = np.nan
    pad = np.pad(H, 1, constant_values=np.nan)
    nb = np.stack([pad[1 + di:n + 1 + di, 1 + dj:n + 1 + dj] for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj])
    with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore")
        med = np.nanmedian(nb, 0)
    H[np.isfinite(med) & (H > med + 0.004)] = np.nan
    return H, Q, ij, o


def caps_of(H, cell=GRID, rise_min=RISE):
    """The small raised parts of a height map (see the module's doc). Cuts from the top down, 1 mm apart; each part
    grows as the cut goes lower until it joins another one (two parts of the cut above, >= 3 cells each and standing
    >= 2 mm over this cut, in one component: the button and, 15 mm away, the top of the kettle handle's grip; not the
    noise patches of one top face, ~1 mm proud of it, as with the realistic D415 depth): its last cut before that is
    the cap.
    -> [dict(face (n, n) bool, top, rise, size)]"""
    known = np.isfinite(H)
    if known.sum() < 20:
        return []
    Hf = np.where(known, H, -np.inf)
    zs = H[known]
    found, closed = {}, set()
    k3, k7 = np.ones((3, 3), np.uint8), np.ones((7, 7), np.uint8)
    prev_lab, prev_area, prev_peak, prev_top = None, None, None, None
    for L in np.arange(zs.max() - 0.001, np.percentile(zs, 1), -0.001):
        mask = (Hf >= L).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        peaks = {}
        for k in range(1, n):
            comp = lab == k
            peak = tuple(np.unravel_index(np.argmax(np.where(comp, Hf, -np.inf)), H.shape))
            peaks[k] = peak
            if prev_lab is not None:                           # joins two parts of the cut above: their caps end
                olds = [j for j in np.unique(prev_lab[comp]) if j > 0 and prev_area[j] >= 3 and prev_top[j] >= L + 0.002]
                if len(olds) >= 2:
                    closed.update(prev_peak[j] for j in olds)
            if peak in closed:
                continue
            ext = max(st[k, cv2.CC_STAT_WIDTH], st[k, cv2.CC_STAT_HEIGHT]) * cell
            if ext > CAP[1]:
                closed.add(peak); continue                         # (only grows from here)
            if ext < CAP[0]:
                continue
            top = float(Hf[comp].max())
            ring = (cv2.dilate(comp.astype(np.uint8), k7) > 0) & ~(cv2.dilate(comp.astype(np.uint8), k3) > 0)
            rk = ring & known
            if rk.sum() < 0.3 * ring.sum():
                continue                                         # (mostly unseen around it)
            rise = top - float(np.percentile(H[rk], 90))
            if rise < rise_min:
                continue
            face = comp & (Hf >= top - 0.002)
            if cv2.connectedComponents(face.astype(np.uint8), connectivity=8)[0] != 2:
                continue                                         # (two tops: two buttons merged)
            found[peak] = dict(face=face, top=top, rise=rise, size=ext)   # (the lowest cut before it joins anything)
        tops = np.full(n, -np.inf); np.maximum.at(tops, lab.ravel(), Hf.ravel())
        prev_lab, prev_area, prev_peak, prev_top = lab, st[:, cv2.CC_STAT_AREA], peaks, tops
    return list(found.values())


def ray_dist(X, ray):
    e, d = ray[0], ray[1]
    v = np.asarray(X, float) - e
    return float(np.linalg.norm(v - (v @ d) * d))


def tilt_of(Q):
    """Angle (deg) of the plane through points Q from the horizontal; None if too few."""
    if len(Q) < 12:
        return None
    w, V = np.linalg.eigh(np.cov((Q - Q.mean(0)).T))
    return float(math.degrees(math.acos(min(1.0, abs(V[2, 0])))))


def find_button(obs, seed, ray=None, half=WIN, ref=None, rise=RISE):
    """The button near seed (world) in a wrist look: dict(c = its top centre, kind cap|surface, dist_mm from the scene
    ray, rise_mm, size_mm, tilt_deg, n_caps). half: the height map's half size. ref: a point (the high look's cap):
    the cap nearest it (xy, within NEAR_REF) instead of the one nearest the ray. Raises Unseen if there is no surface."""
    P, ok = F.world(obs)
    H, Q, ij, o = height_map(P, ok, seed, half=half)
    if np.isfinite(H).sum() < 30:
        raise F.Unseen("no depth around the button's point")
    if ray is None:
        ray = (np.asarray(seed, float) + [0, 0, 1.0], np.array([0, 0, -1.0]), np.asarray(seed, float))
    best, dists, capX = None, [], []
    caps = caps_of(H, rise_min=rise)
    for cp in caps:
        sel = cp["face"][ij[:, 0], ij[:, 1]]
        pts = Q[sel]
        if len(pts) < 4:
            continue
        top = float(np.percentile(pts[:, 2], 90))
        X = np.r_[pts[:, :2].mean(0), top]
        dist = ray_dist(X, ray) if ref is None else float(np.linalg.norm(X[:2] - np.asarray(ref)[:2]))
        dists.append(dist); capX.append(X)
        if best is None or dist < best[0]:
            best = (dist, X, cp, pts)
    if best is not None and best[0] <= (NEAR if ref is None else NEAR_REF):
        dist, X, cp, pts = best
        flat = Q[(np.linalg.norm(Q[:, :2] - X[:2], axis=1) < min(cp["size"] / 2, 0.012)) & (Q[:, 2] > X[2] - 0.003)]
        tilt = tilt_of(flat)
        face_r = float(np.percentile(np.linalg.norm(pts[:, :2] - X[:2], axis=1), 95))   # (its top face's radius)
        return dict(c=X, kind="cap", dist_mm=round(dist * 1000, 1), rise_mm=round(cp["rise"] * 1000, 1),
                    size_mm=round(cp["size"] * 1000), face_mm=round(2 * face_r * 1000), tilt_deg=None if tilt is None else round(tilt, 1),
                    n_caps=len(caps), next_cap_mm=round(sorted(dists)[1] * 1000, 1) if len(dists) > 1 else None, caps=capX)
    if ref is not None:                                            # (no cap at the point found before: its surface)
        near = np.linalg.norm(Q[:, :2] - np.asarray(ref)[:2], axis=1) < 0.006
        if near.sum() >= 5:
            X = np.r_[np.asarray(ref)[:2], float(np.percentile(Q[near, 2], 90))]
            disc = Q[(np.linalg.norm(Q[:, :2] - X[:2], axis=1) < 0.008) & (np.abs(Q[:, 2] - X[2]) < 0.015)]
            tilt = tilt_of(disc)
            return dict(c=X, kind="surface", dist_mm=None, rise_mm=None, size_mm=None,
                        tilt_deg=None if tilt is None else round(tilt, 1), n_caps=len(caps), caps=capX)
    # no cap there (a flush button): where the scene camera's ray meets the height map
    e, d = ray[0], ray[1]
    t0 = float((np.asarray(seed, float) - e) @ d)
    hit = None
    for t in np.arange(t0 - 0.06, t0 + 0.18, 0.001):            # (the button is at or beyond the seed: seed_from_pixel)
        X = e + t * d
        i, j = np.floor((X[:2] - o) / GRID).astype(int)
        if 0 <= i < H.shape[0] and 0 <= j < H.shape[1] and np.isfinite(H[i, j]) and X[2] <= H[i, j]:
            hit = np.r_[X[:2], H[i, j]]; break
    if hit is None:
        near = np.linalg.norm(Q[:, :2] - np.asarray(seed)[:2], axis=1) < 0.008
        if near.sum() < 5:
            raise F.Unseen("no surface at the button's point")
        hit = np.r_[np.asarray(seed)[:2], float(np.percentile(Q[near, 2], 90))]
    disc = Q[(np.linalg.norm(Q[:, :2] - hit[:2], axis=1) < 0.008) & (np.abs(Q[:, 2] - hit[2]) < 0.015)]
    tilt = tilt_of(disc)
    return dict(c=hit, kind="surface", dist_mm=None, rise_mm=None, size_mm=None,
                tilt_deg=None if tilt is None else round(tilt, 1), n_caps=len(caps),
                nearest_cap_mm=None if best is None else round(best[0] * 1000, 1), caps=capX)


# ---------------------------------------------------------------- the skill (a mixin for task_fast.FastProgram's subclasses)
class PressSkill:
    PRESS_IN = PRESS_IN

    def f_press(self, i):
        S, per = self.S, self.per
        sd = per.seeds[i]; s = np.asarray(sd["p"], float); ray = sd.get("ray")
        if ray is None:
            ray = (s + [0, 0, 1.0], np.array([0, 0, -1.0]), s)
        sA = s + AHEAD * np.asarray(ray[1])                        # the high look: over the ray from the seed to 12 cm
        y0 = self.default_yaw(sA[:2])                               # beyond it
        hA, h = self.look_height(WIDE - 0.01), self.look_height(0.04)
        Rl = B.down(self.best_yaw(np.r_[sA[:2], sA[2] + hA], (y0, y0 + math.pi)))
        X = self.look_tcp(sA, Rl, sA[2] + hA)
        if hasattr(self, "transit"):
            self.transit(X[:2], Rl)
        S.move(X, Rl, 0.0, 1.8, "above the button"); S.hold(0.2)
        bA = find_button(per.wrist(), sA, ray, half=WIDE, rise=RISE_WIDE)
        unsure = bA["kind"] != "cap" or (bA.get("next_cap_mm") is not None and bA["next_cap_mm"] - bA["dist_mm"] < AMBIG * 1000)
        self.event("button (high look)", unsure=bool(unsure),
                   **{k: (np.round(v * 1000, 1).tolist() if k == "c" else v) for k, v in bA.items() if k != "caps"})
        ref = bA["c"] if bA["kind"] == "cap" else None
        if unsure and WRIST_MARK:
            m = self.wrist_mark(i, bA, hA)
            if m is not None:
                ref = m["c"]
                self.event("button (wrist mark)", **{k: (np.round(v * 1000, 1).tolist() if k == "c" else v) for k, v in m.items()})
        s2 = ref if ref is not None else s                         # the close look, centred on it
        S.move(self.look_tcp(s2, Rl, s2[2] + h), Rl, 0.0, 1.0, "look closer"); S.hold(0.2)
        obs = per.wrist()
        b = find_button(obs, s2, ray, ref=ref)
        c = b["c"]
        self.event("button", **{k: (np.round(v * 1000, 1).tolist() if k == "c" else v) for k, v in b.items() if k != "caps"})
        truth = getattr(per, "truth_button", None)
        if truth is not None:
            self.log_err("button (wrist depth)", i, c, truth())
        if b["tilt_deg"] is not None and b["tilt_deg"] > TILT_MAX:
            return f"the button's face is tilted {b['tilt_deg']:.0f} deg: this skill presses from above"
        tried = []
        for k in range(2):
            a, travel = self.press_angle(obs, b, avoid=tried)
            if a is None:
                if tried:
                    break
                return f"no room for the fingers around the button (the fingertip could go {travel * 1000:.1f} mm in at most)"
            R = B.down(a)
            tcp = c[:2] + PAD_OFF * R[:2, 1]                      # (the -u fingertip over the button)
            self.event("press angle", a_deg=round(math.degrees(a)), travel_mm=round(travel * 1000, 1))
            S.move(np.r_[tcp, max(S.p_cmd[2], c[2] + T.FINGERTIP + 0.06)], R, 0.0, 1.2, "turn the hand")
            S.move(np.r_[tcp, c[2] + T.FINGERTIP + 0.03], R, 0.0, 1.0, "over the button")
            r = self.press_down(tcp, R, c)
            self.event("pressed", **r)
            tried.append(a)
            if k == 0 and r.get("gave_mm") is not None and r["gave_mm"] < HARD * 1000 and r.get("f_max", 0) >= F_STOP:
                self.event("pressed something hard: once more, another hand angle")
                S.move(S.p_cmd + [0, 0, 0.02], R, 0.0, 0.5, "off the button")
                continue
            break
        S.move(S.p_cmd + [0, 0, 0.001], R, 0.0, 0.2, "unload")
        S.move(S.p_cmd, R, 0.005, 0.4, "fingers apart")          # (nothing wedged between the closed tips)
        S.move(S.p_cmd + [0, 0, 0.10], R, 0.005, 0.8, "retreat"); S.hold(0.4)
        return "ok"

    def wrist_mark(self, i, b, h):
        """Astra marks the button in the wrist camera's colour image from where the hand is (the high look). -> dict(c =
        the cap within NEAR_REF of the mark's 3D point (the wrist depth there), else that point; kind) or None. (The
        bench's reference plan, per.gt: per.wrist_mark_gt instead, the true button's pixel.)"""
        per = self.per
        obs = per.observe("L_wrist")
        if obs.get("rgb") is None and getattr(per, "renderer", None) is not None:   # (task_box's twin looks: depth only)
            obs = B.Perception.observe(per, "L_wrist")
        W_, H_ = int(obs["K"][0]), int(obs["K"][1])
        uv = None
        if getattr(per, "gt", False) and hasattr(per, "wrist_mark_gt"):
            uv = per.wrist_mark_gt(obs)
        elif obs.get("rgb") is None:
            self.event("wrist mark: no colour image")
            return None
        else:
            obj = self.steps[i][1] or "the button"
            prompt = WRIST_PROMPT.format(task=per.task_text, cm=round((h + self.cam_up) * 100), obj=obj, W=W_, H=H_)
            js, txt, info = per.ask([{"role": "user", "content": [{"type": "text", "text": prompt}, per.img_block(obs["rgb"])]}])
            per.astra_log.append(dict(t=float(self.S.d.time), keys=["press: wrist mark"], rounds=[dict(reply=txt, **info)], final=js,
                                      img=f"{per.tag}_press_wrist_{i}.jpg"))
            pt = (js or {}).get("point")
            if isinstance(pt, (list, tuple)) and len(pt) == 2 and all(isinstance(q, (int, float)) for q in pt):
                uv = np.asarray(pt, float)
                cv2.imwrite(str(per.dir / f"{per.tag}_press_wrist_{i}.jpg"),
                            cv2.cvtColor(per.draw(obs["rgb"], [((float(uv[0]), float(uv[1])), per.COLS["place"])]), cv2.COLOR_RGB2BGR))
        if uv is None or not (0 <= uv[0] < W_ and 0 <= uv[1] < H_):
            self.event("wrist mark: none")
            return None
        P, ok = F.world(obs)
        u, v = int(round(uv[0])), int(round(uv[1]))
        win, okw = P[max(v - 3, 0):v + 4, max(u - 3, 0):u + 4], ok[max(v - 3, 0):v + 4, max(u - 3, 0):u + 4]
        if okw.sum() < 3:
            self.event("wrist mark: no depth there", uv=[u, v])
            return None
        Q = win[okw]
        X = np.r_[np.median(Q[:, :2], 0), float(np.percentile(Q[:, 2], 90))]
        near = [(float(np.linalg.norm(np.asarray(c_)[:2] - X[:2])), c_) for c_ in b.get("caps") or []]
        near = [q for q in near if q[0] < NEAR_REF]
        if near:
            d_, c_ = min(near, key=lambda q: q[0])
            return dict(c=np.asarray(c_, float), kind="cap", mark_mm=round(d_ * 1000, 1), uv=[u, v])
        return dict(c=X, kind="point", mark_mm=None, uv=[u, v])

    def press_angle(self, obs, b, avoid=()):
        """Hand angle for the press on button b (find_button): (yaw, travel) with travel = how far the fingertip can go
        under the button's top before another part of the closed hand (FINGERS, HAND, CAM; the fingertip pad taken
        flat at the tip's level; SLACK bigger all round) meets a point of the wrist look that is not the cap (its top face + 0.5 mm, or + 4 mm
        at the top's height: the D415 spreads an edge ~2 mm outwards at 30 cm; not the cap's lowest cut, which can hold
        a neighbour: the kettle's grip, its top 3 mm under the button's, 1.5 mm off its edge); a flush button ("surface"): only points over its top + CLEAR count, travel PRESS_IN. Of the
        angles the arm reaches, those with the most travel (up to PRESS_IN; the kettle's lid opens at 2.5 mm, and the
        top of its handle's grip, 3 mm under the button's top, 15 mm away, took all but ~1.7 mm of that at some
        angles; SLACK bigger: ranked for a few mm of hand-eye error), then the smallest joint change. (None, best
        travel) if no angle leaves MIN_TRAVEL (as measured, without SLACK)."""
        S = self.S
        c = b["c"]
        P, ok = F.world(obs)
        rel = P[..., :2] - c[:2]
        rr = np.hypot(rel[..., 0], rel[..., 1])
        if b["kind"] == "cap":
            fr = (b.get("face_mm") or 10) / 2000
            cap = (rr < fr + 0.0005) | ((rr < fr + 0.004) & (P[..., 2] > c[2] - 0.0015))
            m = ok & (rr < 0.16) & ~cap & (P[..., 2] > c[2] - self.PRESS_IN - 0.003)
        else:
            m = ok & (rr < 0.16) & (P[..., 2] > c[2] + CLEAR)
        Q = P[m][::2]
        cs = 1.0 if self.cam_pos[0] >= 0 else -1.0              # (the camera's side, across the fingers)
        parts = [(u0, u1, hw, bot, 1.0) for u0, u1, hw, bot in FINGERS] + [(u0, u1, hw, bot, -1.0) for u0, u1, hw, bot in FINGERS]
        res = []
        for a in np.radians(np.arange(0, 360, 15.0)):
            if any(abs((a - a0 + math.pi) % (2 * math.pi) - math.pi) < math.radians(60) for a0 in avoid):
                continue
            R = B.down(a); u, v = R[:2, 1], R[:2, 0]
            tcp = c[:2] + PAD_OFF * u
            r2 = Q[:, :2] - tcp; pu, pv, z = r2 @ u, r2 @ v, Q[:, 2]
            tr = []
            for sl in (SLACK, 0.0):                             # per point: the fingertip's lowest height before it touches
                lift = np.full(len(Q), -np.inf)
                for u0, u1, hw, bot, sg in parts:
                    inside = (sg * pu >= u0 - sl) & (sg * pu <= u1 + sl) & (np.abs(pv) < hw + sl)
                    lift = np.where(inside, np.maximum(lift, z - (bot + T.FINGERTIP)), lift)
                inside = (np.abs(pu) < HAND[0] + sl) & (np.abs(pv) < HAND[1] + sl)
                lift = np.where(inside, np.maximum(lift, z - (HAND[2] + T.FINGERTIP)), lift)
                inside = (cs * pv > CAM[0] - sl) & (cs * pv < CAM[1] + sl) & (np.abs(pu) < CAM[2] + sl)
                lift = np.where(inside, np.maximum(lift, z - (CAM[3] + T.FINGERTIP)), lift)
                z_stop = float(lift.max()) if len(lift) else -np.inf
                tr.append(self.PRESS_IN if b["kind"] != "cap" else float(np.clip(c[2] - z_stop, -0.05, self.PRESS_IN)))
            travel, travel0 = tr
            q, e = S.ik(np.r_[tcp, c[2] + T.FINGERTIP + 0.03], R, S.q_cmd)
            if e > 0.002:
                continue
            q2, e2 = S.ik(np.r_[tcp, c[2] - travel + T.FINGERTIP], R, q)
            if e2 > 0.002:
                continue
            cost = float(np.abs(q - S.q_cmd).max()) + 2 * (np.minimum(q - S.lo, S.hi - q).min() < 0.15)
            res.append((round(travel, 4), cost, float(a), travel0))
        if not res:
            if avoid:
                return None, 0.0
            raise T.Unreachable("no hand angle reaches the button")
        if max(r_[3] for r_ in res) < MIN_TRAVEL:
            return None, max(r_[3] for r_ in res)
        tmax = max(r_[0] for r_ in res)
        tr, _, a, _ = min((r_ for r_ in res if r_[0] >= min(tmax, self.PRESS_IN) - 0.0005), key=lambda r_: r_[1])
        return a, tr

    def finger_force(self):
        """(Twin) The contact force on the fingers from everything but the arm. (task_fast's hand_force counts what the
        fingers touch as held: touching the kettle's button, its whole weight on the table, 13 N.)"""
        S, m = self.S, self.S.m
        arm = {b for b in range(m.nbody) if m.body(b).name.startswith("L_")}
        return S.force(S.fingers, set(range(m.nbody)) - arm)

    def press_down(self, tcp, R, c):
        """(Twin) From 1 cm over the button's top, down 1 mm at a time: the touch (> F_TOUCH), then on until the tip
        is PRESS_IN past the touch or the fingers feel > F_STOP (the median of 3 readings over the step's settling);
        hold. Counted from the touch, not from the top the wrist camera read (the realistic D415 model read it 2-4 mm
        high: the press stopped at the true top). -> what happened (the event)."""
        S = self.S
        cmd = np.r_[tcp, c[2] + T.FINGERTIP + 0.010]
        S.move(cmd, R, 0.0, 0.6, "onto the button")
        z_touch, f_max, j = None, 0.0, 0
        lowest = c[2] - 0.006 - self.PRESS_IN
        while True:
            cmd = cmd.copy(); cmd[2] -= 0.001; j += 1
            S.move(cmd, R, 0.0, 0.06, "press the button" if j == 1 else None)
            fs = []
            for _ in range(3):                                   # (the median of 3: the stiff twin arm stepping onto a
                S.hold(0.012); fs.append(self.finger_force())    # 1 g button cap reads one-frame spikes)
            f = float(np.median(fs)); f_max = max(f_max, f)
            tip = cmd[2] - T.FINGERTIP
            if z_touch is None and f > F_TOUCH:
                z_touch = tip + 0.001
            if f > F_STOP or (z_touch is not None and tip <= z_touch - self.PRESS_IN + 1e-6) or tip <= lowest:
                break
        S.hold(0.3)
        return dict(in_mm=round((c[2] - tip) * 1000, 1), gave_mm=None if z_touch is None else round((z_touch - tip) * 1000, 1),
                    touched_mm=None if z_touch is None else round((c[2] - z_touch) * 1000, 1), f_max=round(f_max, 1))
