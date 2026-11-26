import math
import numpy as np
import cv2

ARM = "left"
HOVER = 160.0        # hover height above the rim (grasp) / the matching height above the hang pose


def hover_off(m, gdir, c):
    """hover offset from a grasp TCP (grasp dir gdir from the mug axis, actual closing dir c) that puts the wrist
    camera (50 mm beside the TCP along z x c) right above the mug's axis: same offset in the gripper frame at
    the grasp hover and at the hang hover, so the camera's calibration error cancels."""
    return -m["Rm"] * unit([gdir[0], gdir[1], 0.0]) - 50.0 * perp(unit([c[0], c[1], 0.0]))
CLEAR = 11.0         # branch top below the handle's inner top while threading
LOWER = 5.0          # lowering before release
Z_SAFE = 560.0
UP = np.array([0.0, 0.0, 1.0])
DEBUG = True


def gd(m):
    """grasp depth: TCP this far below the rim (pinch low on the wall: the mug slides rather than tips)."""
    g = min(m["H"] - 48.0, m["z_rim"] - 14.0)
    return float(np.clip(g, 14.0, m["H"] - 25.0))


def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def hz(a):
    return np.array([math.cos(a), math.sin(a), 0.0])


def angof(v):
    return math.atan2(v[1], v[0])


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def perp(v):
    return np.array([-v[1], v[0], 0.0])


def seg_dist(P, a, b):
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-9:
        return np.linalg.norm(P - a, axis=1)
    t = np.clip(((P - a) @ ab) / L2, 0.0, 1.0)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


def circle_fit(xy):
    x, y = xy[:, 0], xy[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(x))]
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-6))
    return np.array([cx, cy]), r


def ransac_circle(xy, rmin, rmax, tol, rng, iters=300):
    n = len(xy)
    if n < 8:
        return None
    best, bestc = None, 0
    for _ in range(iters):
        i = rng.choice(n, 3, replace=False)
        try:
            c, r = circle_fit(xy[i])
        except Exception:
            continue
        if not (rmin < r < rmax):
            continue
        d = np.abs(np.linalg.norm(xy - c, axis=1) - r)
        cnt = int((d < tol).sum())
        if cnt > bestc:
            bestc, best = cnt, (c, r)
    if best is None:
        return None
    c, r = best
    inl = np.abs(np.linalg.norm(xy - c, axis=1) - r) < tol
    c, r = circle_fit(xy[inl])
    return c, r, int(inl.sum())


def ransac_line(P, tol, rng, iters=200):
    n = len(P)
    if n < 10:
        return None
    best, bestc = None, 0
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        d = P[j] - P[i]
        L = np.linalg.norm(d)
        if L < 20:
            continue
        d = d / L
        q = P - P[i]
        dist = np.linalg.norm(q - np.outer(q @ d, d), axis=1)
        inl = dist < tol
        c = int(inl.sum())
        if c > bestc:
            bestc, best = c, inl
    if best is None:
        return None
    Q = P[best]
    c0 = Q.mean(axis=0)
    u, s, vt = np.linalg.svd(Q - c0, full_matrices=False)
    d = vt[0]
    q = P - c0
    dist = np.linalg.norm(q - np.outer(q @ d, d), axis=1)
    inl = dist < tol
    return c0, d, inl


# ----------------------------------------------------------------------------------------------------
class Bot:
    def __init__(self, robot):
        self.r = robot
        self.nshow = 0
        self.truth_ok = True

    def st(self):
        return self.r.state(ARM)

    def tcp(self):
        return np.array(self.st()["tcp_mm"], float)

    def cdir(self):
        c = np.array(self.st()["closing_dir"], float)
        c[2] = 0.0
        return unit(c)

    def move(self, p, c=None, contact=False, tag="", depth=0):
        if c is None:
            c = self.cdir()
        res = self.r.move(ARM, [float(v) for v in p], [0.0, 0.0, -1.0], [float(c[0]), float(c[1]), 0.0], bool(contact))
        if not res.get("moved") and "flip" in str(res.get("why")) and depth < 2:
            t = self.tcp()
            mid = 0.5 * (t + np.asarray(p, float))
            r1 = self.move(mid, c, contact, tag, depth + 1)
            if not r1.get("done"):
                return r1
            return self.move(p, c, contact, tag, depth + 1)
        if not res.get("done") and res.get("why") != "contact":
            print("  move%s %s -> moved=%s why=%s" % (tag, np.round(p, 1).tolist(), res.get("moved"), res.get("why")))
        return res

    def turn_to(self, c_target, pos=None):
        """make the ACTUAL closing dir equal c_target, turning in steps < 80 deg (at pos)."""
        c_target = unit([c_target[0], c_target[1], 0.0])
        a0 = angof(self.cdir())
        d = wrap(angof(c_target) - a0)
        if abs(d) < math.radians(2):
            return True
        p = self.tcp() if pos is None else np.asarray(pos, float)
        done_rot = 0.0
        for delta in ([d, d - 2 * math.pi] if d > 0 else [d, d + 2 * math.pi]):
            rem = delta - done_rot
            n = max(1, int(math.ceil(abs(rem) / math.radians(75))))
            step = rem / n
            ok = True
            for _ in range(n):
                res = self.move(p, hz(a0 + done_rot + step), tag=" turn")
                if res.get("done"):
                    done_rot += step
                else:
                    ok = False
                    break
            if ok:
                err = wrap(angof(self.cdir()) - angof(c_target))
                if abs(err) < math.radians(4):
                    return True
                print("  turn_to: wrong final yaw err %.1f deg" % math.degrees(err))
                return False
        return False

    def look(self):
        f = self.r.look(["left_wrist"])["left_wrist"]
        P = f.points().reshape(-1, 3)
        m = np.isfinite(P[:, 0])
        return P[m], f

    def show(self, label, img):
        if self.nshow < 12:
            try:
                self.r.show(label, img)
                self.nshow += 1
            except Exception as e:
                print("show failed", e)

    def truth(self):
        try:
            return self.r.truth()
        except Exception:
            return None


# ----------------------------------------------------------------------------------------------------
def table_z(P):
    z = P[:, 2]
    z = z[(z > -90) & (z < 130)]
    h, e = np.histogram(z, bins=np.arange(-90, 131, 2.0))
    k = int(np.argmax(h))
    zc = 0.5 * (e[k] + e[k + 1])
    return float(np.median(z[np.abs(z - zc) < 5]))


GX0, GY0, GRES, NX, NY = -660.0, -460.0, 4.0, 170, 285


def blobs(P, zt):
    ix = np.floor((P[:, 0] - GX0) / GRES).astype(int)
    iy = np.floor((P[:, 1] - GY0) / GRES).astype(int)
    ok = (ix >= 0) & (ix < NX) & (iy >= 0) & (iy < NY)
    Hm = np.full((NX, NY), -1e9)
    np.maximum.at(Hm, (ix[ok], iy[ok]), P[ok, 2])
    mask = (Hm > zt + 12).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for k in range(1, n):
        area = stats[k, cv2.CC_STAT_AREA]
        if area < 40:
            continue
        cells = lab == k
        hts = Hm[cells]
        hts = hts[hts > -1e8]
        if len(hts) < 20:
            continue
        top = float(np.percentile(hts, 97))
        lab_d = cv2.dilate(cells.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        sel = np.zeros(len(P), bool)
        sel[ok] = lab_d[ix[ok], iy[ok]]
        pts = P[sel]
        cx, cy = np.argwhere(cells).mean(axis=0)
        out.append({"area": float(area * GRES * GRES), "top": top, "pts": pts,
                    "c": np.array([GX0 + (cx + 0.5) * GRES, GY0 + (cy + 0.5) * GRES])})
    return out


def find_rims(P, zt, rng):
    """circles of rims (top layers) in a blob's points, tallest first; several objects may share a blob."""
    out = []
    Q = P[P[:, 2] > zt + 25]
    for it in range(6):
        if len(Q) < 60:
            break
        zr = float(np.percentile(Q[:, 2], 98.5))
        band = Q[np.abs(Q[:, 2] - zr) < 5]
        rc = ransac_circle(band[:, :2], 22, 56, 2.5, rng) if len(band) >= 15 else None
        if rc is None or rc[2] < 25:
            # not a rim: drop everything under this top layer's footprint
            g = set(map(tuple, np.floor(band[:, :2] / 15.0).astype(int)))
            keys = np.floor(Q[:, :2] / 15.0).astype(int)
            drop = np.array([tuple(k) in g for k in keys])
            Q = Q[~drop & (Q[:, 2] < zr - 6)] if drop.any() else Q[Q[:, 2] < zr - 6]
            continue
        C, r, n = rc
        out.append((C, r, zr))
        Q = Q[np.linalg.norm(Q[:, :2] - C, axis=1) > r + 60]
    return out


def fit_mug(P, zt, center_hint, rng, r_hint=None):
    """mug model from 3D points around a mug (table frame)."""
    d0 = np.linalg.norm(P[:, :2] - center_hint, axis=1)
    # the hint comes from another view (its calibration error can differ by ~15 mm)
    lim = 140 if r_hint is None else r_hint + 80
    P = P[d0 < lim]
    if len(P) < 50:
        return None
    hi = P[P[:, 2] > zt + 25]
    if r_hint is not None:
        hi = hi[np.linalg.norm(hi[:, :2] - center_hint, axis=1) < r_hint + 22]
    if len(hi) < 30:
        return None
    zr = float(np.percentile(hi[:, 2], 98.5))
    band = hi[np.abs(hi[:, 2] - zr) < 5]
    rc = ransac_circle(band[:, :2], 22, 56, 2.5, rng)
    if rc is None:
        return None
    C, r_fit, ninl = rc
    rr = np.linalg.norm(P[:, :2] - C, axis=1)
    az = np.arctan2(P[:, 1] - C[1], P[:, 0] - C[0])
    R_out = r_fit + 2.5
    # support surface right around the mug (table or a platform): the most common level
    ring = P[(rr > R_out + 8) & (rr < R_out + 28) & (P[:, 2] < zr - 30)]
    if len(ring) > 30:
        h, e = np.histogram(ring[:, 2], bins=np.arange(zt - 10, zr - 28, 2.0))
        k = int(np.argmax(h))
        z_sup = float(np.median(ring[np.abs(ring[:, 2] - 0.5 * (e[k] + e[k + 1])) < 4, 2]))
    else:
        z_sup = zt
    # interior
    inner = P[rr < r_fit - 10]
    hollow = (len(inner) < 15) or (float(np.median(inner[:, 2])) < zr - 15)
    # handle
    # seen from above, a handle's top surface lies in the upper ~half of the mug (drops platforms, low things)
    zmask = (P[:, 2] > z_sup + max(12.0, 0.42 * (zr - z_sup))) & (P[:, 2] < zr + 3)
    # outer part of a handle: beyond the wall's own depth fattening / noise band
    hm = (rr > R_out + 13) & (rr < R_out + 58) & zmask
    hp, ha, hr = P[hm], az[hm], rr[hm]
    res = {"C": C, "r_fit": r_fit, "R_out": R_out, "Rm": r_fit, "z_rim": zr, "z_sup": z_sup,
           "H": zr - z_sup, "hollow": hollow, "n_rim": ninl, "handle": False}
    if len(hp) < 10:
        return res
    nb = 36
    hist = np.bincount(((ha + math.pi) / (2 * math.pi) * nb).astype(int) % nb, minlength=nb).astype(float)
    sm = hist + 0.6 * (np.roll(hist, 1) + np.roll(hist, -1))
    attach_m = (rr > R_out + 4) & (rr < R_out + 15) & zmask
    best = None
    for _ in range(5):
        k = int(np.argmax(sm))
        if sm[k] < 8:
            break
        for j in range(-3, 4):
            sm[(k + j) % nb] = 0
        a_pk = -math.pi + (k + 0.5) * 2 * math.pi / nb
        near = np.abs(np.array([wrap(a - a_pk) for a in ha])) < math.radians(30)
        if near.sum() < 8:
            continue
        th = math.atan2(np.sin(ha[near]).mean(), np.cos(ha[near]).mean())
        # a handle grows out of the wall: keep the radially contiguous part (a neighbour leaves a gap)
        sec = zmask & (rr > R_out + 4) & (rr < R_out + 60) & \
            (np.abs(np.array([wrap(a - th) for a in az])) < math.radians(30))
        cnt = np.bincount(np.clip(((rr[sec] - R_out - 4) / 2.0).astype(int), 0, 27), minlength=28)
        last, gap = -1, 0
        for i in range(28):
            if cnt[i] > 0:
                last, gap = i, 0
            else:
                gap += 1
                if gap >= 3 and last >= 0:
                    break
        r_end = R_out + 4 + 2.0 * (last + 1) + 1.0
        near = (np.abs(np.array([wrap(a - th) for a in ha])) < math.radians(30)) & (hr <= r_end)
        if near.sum() < 8:
            continue
        hpn, hrn, han = hp[near], hr[near], ha[near]
        tang = hrn * np.sin(han - th)
        width = float(np.percentile(tang, 92) - np.percentile(tang, 8))
        rh = float(np.clip(width / 2 - 1.0, 3.5, 8.0))
        # the outer flank of the handle is seen at a grazing angle: its last ~half tube radius has no depth
        r_out = float(np.percentile(hrn, 99)) + 0.5 * rh
        z_hi = float(np.percentile(hpn[:, 2], 96))
        att = attach_m & (np.abs(np.array([wrap(a - th) for a in az])) < math.radians(12))
        n_att = int(att.sum())
        ok = (near.sum() >= 12 and r_out - R_out > 14 and width < 34 and z_hi - z_sup > 30
              and r_out - R_out < 60 and n_att >= 4)
        cand = {"handle": bool(ok), "th": th, "r_out": r_out, "z_hi": z_hi, "rh": rh,
                "hole_rc": 0.5 * (R_out + r_out - 2 * rh), "hole_top": z_hi - 2 * rh, "n_handle": int(near.sum()),
                "h_frac": float(near.sum()) / max(1, len(hp)), "h_width": width, "n_att": n_att}
        if best is None or (ok and not best["handle"]):
            best = cand
        if ok:
            break
    if best is not None:
        res.update(best)
    return res


class Branch:
    def __init__(self, A, q0, bh, z0, slope, s_tip, rb, s_root):
        self.A, self.q0, self.bh, self.z0, self.slope = A, q0, unit(bh), z0, slope
        self.s_tip, self.rb, self.s_root = s_tip, rb, s_root
        self.knob = rb + 4.0
        self.v = perp(self.bh)

    def xy(self, s):
        o = self.q0[:2] - float((self.q0[:2] - self.A) @ self.bh[:2]) * self.bh[:2]
        s = np.asarray(s, float)
        return o + s[..., None] * self.bh[:2]

    def ztop(self, s):
        return self.z0 + self.slope * s

    def axis_pt(self, s):
        return np.array([*self.xy(s), self.ztop(s) - self.rb])

    def az(self):
        return angof(self.bh)

    def desc(self):
        return "az %.0f elev %.0f s_tip %.0f rb %.1f ztop(root) %.0f" % (
            math.degrees(self.az()), math.degrees(math.atan(self.slope)), self.s_tip, self.rb, self.ztop(self.s_root))


def branch_from_points(A, Q, rb_prior=None):
    """Q: points of one branch. Returns Branch (xy line + top line)."""
    c0 = Q.mean(axis=0)
    xy = Q[:, :2] - c0[:2]
    u, s, vt = np.linalg.svd(xy, full_matrices=False)
    bh = np.array([vt[0][0], vt[0][1], 0.0])
    if (c0[:2] - A) @ bh[:2] < 0:
        bh = -bh
    v = perp(bh)
    off = (Q[:, :2] - c0[:2]) @ v[:2]
    med = float(np.median(off))
    q0 = c0.copy()
    q0[:2] = c0[:2] + med * v[:2]
    rb = float(np.clip(np.percentile(np.abs(off - med), 90) - 1.5, 3.0, 9.0)) if rb_prior is None else rb_prior
    s = (Q[:, :2] - A) @ bh[:2]
    z = Q[:, 2]
    bins = np.floor(s / 8.0).astype(int)
    ss, zz = [], []
    for b in np.unique(bins):
        m = bins == b
        if m.sum() >= 3:
            ss.append(float(np.mean(s[m])))
            zz.append(float(np.percentile(z[m], 85)))
    if len(ss) >= 3:
        slope, z0 = np.polyfit(np.array(ss), np.array(zz), 1)
    else:
        slope, z0 = 0.3, float(np.percentile(z, 85)) - 0.3 * float(np.mean(s))
    s_tip = float(np.percentile(s, 98.5))
    return Branch(A, q0, bh, float(z0), float(slope), s_tip, rb, 0.0)


def fit_rack(P, zt, rng):
    hi = P[P[:, 2] > zt + 150]
    if len(hi) < 50:
        return None
    A = np.median(hi[:, :2], axis=0)
    near = P[(np.linalg.norm(P[:, :2] - A, axis=1) < 60) & (P[:, 2] < zt + 120) & (P[:, 2] > zt + 4)]
    if len(near) > 20:
        h, e = np.histogram(near[:, 2], bins=np.arange(zt + 4, zt + 122, 2.0))
        k = int(np.argmax(h))
        base_top = float(0.5 * (e[k] + e[k + 1]))
    else:
        base_top = zt + 20
    pole_r = 12.0
    for it in range(2):
        pm = P[(P[:, 2] > base_top + 25) & (P[:, 2] < base_top + 128) &
               (np.linalg.norm(P[:, :2] - A, axis=1) < 45)]
        if len(pm) >= 15:
            rc = ransac_circle(pm[:, :2], 4, 26, 2.0, rng)
            if rc is not None and rc[2] >= 10:
                A, pole_r = rc[0], float(np.clip(rc[1], 5, 22))
    rr = np.linalg.norm(P[:, :2] - A, axis=1)
    top_pts = P[rr < pole_r + 4]
    pole_top = float(np.percentile(top_pts[:, 2], 99)) if len(top_pts) > 10 else float(np.percentile(P[:, 2], 99))
    bp = P[np.abs(P[:, 2] - base_top) < 4]
    base_r = float(np.clip(np.percentile(np.linalg.norm(bp[:, :2] - A, axis=1), 97), 50, 140)) if len(bp) > 30 else 110.0
    br1 = extract_branches(P, A, base_top, 40.0, rng)
    A1 = axis_from_lines(br1, A)
    if A1 is not None and np.linalg.norm(A1 - A) < 45:
        A = A1
        rr = np.linalg.norm(P[:, :2] - A, axis=1)
        pm = P[(P[:, 2] > base_top + 25) & (P[:, 2] < base_top + 128) & (rr < 30)]
        if len(pm) > 10:
            pole_r = float(np.clip(np.median(np.linalg.norm(pm[:, :2] - A, axis=1)), 5, 22))
    branches = extract_branches(P, A, base_top, pole_r + 8, rng)
    for br in branches:
        br.s_root = pole_r
    top_pts = P[np.linalg.norm(P[:, :2] - A, axis=1) < pole_r + 4]
    pole_top = float(np.percentile(top_pts[:, 2], 97)) if len(top_pts) > 10 else float(np.percentile(P[:, 2], 97))
    branches = [br for br in branches if br.slope < 1.2 and br.ztop(br.s_tip) < pole_top + 100]
    rack_top = max([pole_top] + [br.ztop(br.s_tip) + 3.0 for br in branches]) + 3.0
    return {"A": A, "base_top": base_top, "pole_r": pole_r, "pole_top": pole_top, "base_r": base_r,
            "branches": branches, "zt": zt, "top": rack_top}


def axis_from_lines(brs, A0):
    if len(brs) < 2:
        return None
    M = np.zeros((2, 2))
    bv = np.zeros(2)
    for br in brs:
        d = br.bh[:2]
        n = np.array([-d[1], d[0]])
        M += np.outer(n, n)
        bv += n * (n @ br.q0[:2])
    ev = np.linalg.eigvalsh(M)
    if ev[0] < 0.25:
        return None
    return np.linalg.solve(M, bv)


def same_branch(a, b):
    if abs(wrap(a.az() - b.az())) < math.radians(12) and abs(a.ztop(70) - b.ztop(70)) < 20:
        return True
    for s in (60.0, 100.0):
        if abs(float((a.xy(s) - b.xy(s)) @ b.v[:2])) > 14:
            return False
    return abs(a.ztop(80) - b.ztop(80)) < 22


def extract_branches(P, A, base_top, rmin, rng):
    rr = np.linalg.norm(P[:, :2] - A, axis=1)
    B = P[(P[:, 2] > base_top + 110) & (rr > rmin) & (rr < 230)]
    branches = []
    if len(B) > 30:
        az = np.arctan2(B[:, 1] - A[1], B[:, 0] - A[0])
        nb = 120
        idx = ((az + math.pi) / (2 * math.pi) * nb).astype(int) % nb
        hist = np.bincount(idx, minlength=nb).astype(float)
        ker = np.array([0.25, 0.5, 1.0, 0.5, 0.25])
        sm = sum(ker[i] * np.roll(hist, i - 2) for i in range(5))
        used = np.zeros(len(B), bool)
        for _ in range(10):
            k = int(np.argmax(sm))
            if sm[k] < 20:
                break
            a_pk = -math.pi + (k + 0.5) * 2 * math.pi / nb
            sm[[(k + j) % nb for j in range(-5, 6)]] = 0
            sector = (~used) & (np.abs(np.array([wrap(a - a_pk) for a in az])) < math.radians(18))
            for _rep in range(2):
                ids = np.where(sector & ~used)[0]
                if len(ids) < 20:
                    break
                rl = ransac_line(B[ids], 10.0, rng)
                if rl is None:
                    break
                c0, d, inl = rl
                if inl.sum() < 25:
                    break
                Q = B[ids[inl]]
                ext = (Q - c0) @ d
                used[ids[inl]] = True
                if ext.max() - ext.min() < 50:
                    continue
                br = branch_from_points(A, Q)
                br.npts = len(Q)
                if not (-0.1 < br.slope < 2.5 and br.s_tip > rmin + 50):
                    continue
                dup = None
                for o in branches:
                    if same_branch(o, br):
                        dup = o
                        break
                if dup is None:
                    branches.append(br)
                elif br.npts > dup.npts:
                    branches[branches.index(dup)] = br
    return branches


def refine_branch(br, P):
    s = (P[:, :2] - br.A) @ br.bh[:2]
    lo, hi = br.s_root + 8, br.s_tip - 2
    m = (s > lo) & (s < hi)
    Pm, sm = P[m], s[m]
    if len(Pm) < 20:
        return None
    off = (Pm[:, :2] - br.xy(sm)) @ br.v[:2]
    dz = Pm[:, 2] - br.ztop(sm)
    sel = (np.abs(off) < 16) & (np.abs(dz) < 18)
    if sel.sum() < 20:
        return None
    nb = branch_from_points(br.A, Pm[sel], rb_prior=br.rb)
    nb.s_root = br.s_root
    nb.s_tip = br.s_tip if abs(nb.s_tip - br.s_tip) > 15 else 0.5 * (nb.s_tip + br.s_tip)
    if abs(wrap(nb.az() - br.az())) > math.radians(8) or abs(nb.slope - br.slope) > 0.25:
        return None
    return nb


# ----------------------------------------------------------------------------------------------------
def gripper_local(opening):
    pts = []
    for sg in (-1, 1):
        for zz in (-16, 0, 25, 55, 85, 112):
            th = 10 + 30 * (zz + 16) / 128.0
            for cc in (opening / 2 + 2, opening / 2 + th):
                for ww in (-17, 0, 17):
                    pts.append((sg * cc, ww, zz))
    for cc in np.linspace(-100, 100, 9):
        for ww in (-32, 0, 32):
            for zz in (116, 150, 185):
                pts.append((cc, ww, zz))
    for cc in (-15, 15):
        for ww in (40, 64):
            for zz in (110, 150):
                pts.append((cc, ww, zz))
    for k in range(8):
        a = 2 * math.pi * k / 8
        pts.append((55 * math.cos(a), 55 * math.sin(a), 230))
    return np.array(pts, float)


def to_world(local, tcp, c):
    c = unit([c[0], c[1], 0.0])
    w = perp(c)
    return tcp + np.outer(local[:, 0], c) + np.outer(local[:, 1], w) + np.outer(local[:, 2], UP)


def mug_local(m):
    """mug sample points relative to the grasp TCP in a frame (g, w, z): g = from mug axis to grasp point,
    handle at angle hs*90 deg from g (hs = sign).  returns body pts, handle pts (for hs=+1 and -1)."""
    R, H = m["R_out"], m["H"]
    GD = gd(m)
    ax = np.array([-m["Rm"], 0.0])
    body = []
    for zz in (GD, GD - H / 3, GD - 2 * H / 3, GD - H + 2):
        for k in range(16):
            a = 2 * math.pi * k / 16
            body.append((ax[0] + R * math.cos(a), ax[1] + R * math.sin(a), zz))
    for k in range(4):
        a = 2 * math.pi * k / 4
        body.append((ax[0] + 0.5 * R * math.cos(a), ax[1] + 0.5 * R * math.sin(a), GD - H + 2))
    hdl = {}
    for hs in (1, -1):
        h = np.array([0.0, float(hs)])
        pts = []
        zt_rel = m["z_hi"] - m["z_rim"] + GD - m["rh"]
        for r in (m["R_out"] + 3, m["hole_rc"], m["r_out"] - m["rh"]):
            p = ax + r * h
            pts.append((p[0], p[1], zt_rel))
        p = ax + (m["r_out"] - m["rh"]) * h
        for dz in (12, 26, 38):
            pts.append((p[0], p[1], zt_rel - dz))
        hdl[hs] = np.array(pts)
    return np.array(body), hdl


def rack_parts(rack):
    A, bt = rack["A"], rack["base_top"]
    parts = [("pole", np.array([A[0], A[1], bt]), np.array([A[0], A[1], rack["pole_top"]]), rack["pole_r"], -1)]
    for j, br in enumerate(rack["branches"]):
        a = br.axis_pt(br.s_root)
        b = br.axis_pt(max(br.s_root + 5, br.s_tip - br.rb))
        parts.append(("br%d" % j, a, b, max(br.rb, br.knob), j))
    return parts


def clearance(Pw, rack, parts, skip_j=None, keepouts=()):
    """min clearance of points Pw to rack parts (skip branch skip_j), the base and keep-out capsules."""
    best = 1e9
    for (name, a, b, r, j) in parts:
        if skip_j is not None and j == skip_j:
            continue
        d = seg_dist(Pw, a, b) - r
        best = min(best, float(d.min()))
    A, bt = rack["A"], rack["base_top"]
    hd = np.linalg.norm(Pw[:, :2] - A, axis=1)
    inb = hd < rack["base_r"] + 10
    if inb.any():
        best = min(best, float((Pw[inb, 2] - bt).min()))
    for (a, b, r) in keepouts:
        best = min(best, float((seg_dist(Pw, a, b) - r).min()))
    return best


def forbidden(Pw):
    return bool(((Pw[:, 0] < -405) & (Pw[:, 1] < -265)).any())


# ----------------------------------------------------------------------------------------------------
class Plan:
    pass


def make_plan(m, br, j, sigma, s_d, rack, z_table):
    p = Plan()
    p.m, p.br, p.j, p.sigma, p.s_d = m, br, j, sigma, s_d
    bh = br.bh
    v = br.v
    p.g_tab = hz(m["th"] + sigma * math.pi / 2)        # grasp dir on the table (from mug axis)
    GD = gd(m)
    p.G = np.array([*(m["C"] + m["Rm"] * p.g_tab[:2]), m["z_rim"] - GD])
    p.off_h = sigma * m["hole_rc"] * v + m["Rm"] * bh
    p.dz = CLEAR - (m["hole_top"] - m["z_rim"] + GD)
    p.s_far = br.s_tip + m["rh"] + 13.0
    p.delta = wrap(angof(bh) - angof(p.g_tab))
    return p


def plan_tcp(p, s):
    br = p.br
    xy = br.xy(s) + p.off_h[:2]
    return np.array([xy[0], xy[1], br.ztop(s) + p.dz])


def check_plan(p, rack, keepouts, z_tr):
    """collision clearance of the hang path; returns (ok, min clearance info)."""
    parts = rack_parts(rack)
    m = p.m
    body_l, hdl_l = mug_local(m)
    hs = -p.sigma  # handle direction relative to g in the (g, w) frame at hang: h = -sigma v, w = z x g = v
    # at hang g = bh, w = perp(bh) = v, handle = -sigma*v -> local (0, -sigma)
    hdl = hdl_l[hs]
    grip = gripper_local(max(4.0, m.get("tw", 5.0)))
    grip_open = gripper_local(80.0)
    T_far = plan_tcp(p, p.s_far)
    T_hang = plan_tcp(p, p.s_d)
    T_low = T_hang - LOWER * UP
    T_above = np.array([T_far[0], T_far[1], z_tr])
    worst = {}
    segs = [("descend", T_above, T_far), ("thread", T_far, T_low)]
    for name, a, b in segs:
        n = int(math.ceil(np.linalg.norm(b - a) / 8.0)) + 1
        cg, cb, ch, cbt = 1e9, 1e9, 1e9, 1e9
        for t in np.linspace(0, 1, n):
            T = a + t * (b - a)
            g = to_world(grip, T, p.br.bh)
            mb = to_world(body_l, T, p.br.bh)
            mh = to_world(hdl, T, p.br.bh)
            cg = min(cg, clearance(g, rack, parts, keepouts=keepouts))
            cb = min(cb, clearance(mb, rack, parts, skip_j=p.j, keepouts=keepouts))
            cbt = min(cbt, float((seg_dist(mb, *parts[p.j + 1][1:3]) - p.br.rb).min()))
            ch = min(ch, clearance(mh, rack, parts, skip_j=p.j))
            if forbidden(g):
                cg = -99
        worst[name] = (cg, cb, cbt, ch)
    hov = to_world(grip_open, T_hang + hover_off(m, p.br.bh, p.br.bh) + (gd(m) + HOVER) * UP, p.br.bh)
    up = to_world(grip_open, T_low + 70 * UP, p.br.bh)
    c_h = clearance(hov, rack, parts, keepouts=keepouts)
    c_up = clearance(up, rack, parts, keepouts=keepouts)
    ok = (c_h > 10 and c_up > 6 and all(w[0] > 8 and w[1] > 6 and w[2] > 0.5 and w[3] > 3 for w in worst.values()))
    reach = []
    for T in (T_far, T_hang, T_above):
        dd = math.hypot(T[0] + 790, T[1] - 40)
        reach.append(-700 < T[0] < -90 and -400 < T[1] < 590 and dd < 760)
    ok = ok and all(reach)
    p.T_far, p.T_hang, p.T_low = T_far, T_hang, T_low
    p.clear = worst
    p.ok = ok
    return ok


def plan_cost(p):
    c = abs(p.delta) / math.pi * 0.8
    c += 1.2 * max(0.0, p.br.bh[0])
    dd = math.hypot(p.T_far[0] + 790, p.T_far[1] - 40)
    c += max(0.0, dd - 600) / 100.0
    return c


def keepout_for(p):
    br, m = p.br, p.m
    H, R = m["H"], m["R_out"]
    a = br.axis_pt(max(br.s_root + R, min(p.s_d, br.s_root + R + 5))) - np.array([0, 0, 0.4 * H])
    b = br.axis_pt(p.s_d) - np.array([0, 0, 0.4 * H])
    return (a, b, 0.45 * math.hypot(H, 2 * R) + 4.0)


def candidates(m, rack, keepouts, z_table, used_j=()):
    out = []
    for j, br in enumerate(rack["branches"]):
        if j in used_j:
            continue
        lo = br.s_root + 24
        hi = br.s_tip - 16
        if hi < lo:
            continue
        s_pref = float(np.clip(max(0.62 * br.s_tip, br.s_root + m["R_out"] + 12), lo, hi))
        for s_d in sorted(set([s_pref, float(np.clip(s_pref + 14, lo, hi)), float(np.clip(s_pref - 14, lo, hi))])):
            for sigma in (1, -1):
                p = make_plan(m, br, j, sigma, s_d, rack, z_table)
                z_tr = transport_z(m, rack, z_table, p)
                if check_plan(p, rack, keepouts, z_tr):
                    p.cost = plan_cost(p)
                    out.append(p)
    out.sort(key=lambda q: q.cost)
    return out


def transport_z(m, rack, zt, p=None):
    GD = gd(m)
    z = max(zt + 180 + m["H"] - GD, rack["top"] + (m["H"] - GD) + 30)
    if p is not None:
        z = max(z, plan_tcp(p, p.s_far)[2] + 40)
    return float(min(z, Z_SAFE))


# ----------------------------------------------------------------------------------------------------
class Skill:
    def __init__(self, robot):
        self.b = Bot(robot)
        self.rng = np.random.default_rng(0)
        self.t0 = robot.time_s()
        self.corr = np.zeros(3)
        self.ycorr = 0.0
        self.late = False
        self.last_sg = 1

    def log(self, *a):
        print("[%.1f]" % self.b.r.time_s(), *a)

    def overview(self):
        b = self.b
        allP = []
        for (x, y) in [(-390, -60), (-390, 290)]:
            ok = False
            for z in (600, 560, 520):
                r = b.move([x, y, z], [0, -1, 0])
                if r.get("done"):
                    ok = True
                    break
            if not ok:
                continue
            P, f = b.look()
            allP.append(P)
            if DEBUG:
                b.show("overview_%d" % y, f.rgb)
        P = np.concatenate(allP, axis=0)
        P = P[(P[:, 0] > -720) & (P[:, 0] < 50) & (P[:, 1] > -480) & (P[:, 1] < 680)]
        self.zt = table_z(P)
        self.log("table z %.1f, %d points" % (self.zt, len(P)))
        self.P_over = P
        obs = blobs(P, self.zt)
        obs.sort(key=lambda o: -o["top"])
        self.obstacles = [(o["c"], math.sqrt(o["area"] / math.pi) + 5.0, o["top"]) for o in obs]
        self.rack = None
        self.maybes = []
        mugs = []
        for o in obs:
            h = o["top"] - self.zt
            self.log("blob at %s top %.0f area %.0f" % (np.round(o["c"], 0).tolist(), h, o["area"]))
            if self.rack is None and h > 190 and o["area"] > 8000:
                self.rack = fit_rack(o["pts"], self.zt, self.rng)
                continue
            if 45 < h < 260 and o["area"] > 2000:
                for (C, r, zr) in find_rims(o["pts"], self.zt, self.rng):
                    m = fit_mug(o["pts"], self.zt, C, self.rng, r_hint=r)
                    if m is None:
                        continue
                    self.log("  cand: C %s r %.1f H %.0f sup %.0f hollow %s handle %s n_h %s frac %.2f w %.0f" % (
                        np.round(m["C"], 1).tolist(), m["r_fit"], m["H"], m["z_sup"] - self.zt, m["hollow"],
                        m["handle"], m.get("n_handle"), m.get("h_frac", 0), m.get("h_width", 0)))
                    if any(np.linalg.norm(m["C"] - q["C"]) < 45 for q in mugs + self.maybes):
                        continue
                    if m["hollow"] and 55 < m["H"] < 160 and 28 < m["r_fit"] < 52:
                        if m["handle"]:
                            mugs.append(m)
                        else:
                            self.maybes.append(m)
        self.mugs = mugs
        if self.rack is not None:
            rk = self.rack
            self.log("rack A %s base_top %.0f pole_r %.1f pole_top %.0f top %.0f base_r %.0f, %d branches" % (
                np.round(rk["A"], 1).tolist(), rk["base_top"] - self.zt, rk["pole_r"], rk["pole_top"] - self.zt,
                rk["top"] - self.zt, rk["base_r"], len(rk["branches"])))
            for br in rk["branches"]:
                self.log("   branch", br.desc())
        tr = self.b.truth()
        if tr:
            if "w_rack" in tr and self.rack is not None:
                self.log("TRUTH rack err", np.round(self.rack["A"] - np.array(tr["w_rack"][:2]), 1).tolist())
            for m in self.mugs:
                ds = [(np.linalg.norm(m["C"] - np.array(v[:2])), k) for k, v in tr.items() if "mug" in k]
                if ds:
                    d, k = min(ds)
                    self.log("TRUTH mug %s center err %.1f" % (k, d))

    def run(self):
        b = self.b
        b.r.set_gripper(ARM, 80)
        self.overview()
        if self.rack is None or not self.rack["branches"]:
            self.log("no rack found")
            return
        for mb in self.maybes:
            m2 = self.inspect(mb)
            if m2 is not None and m2["handle"] and m2["hollow"]:
                if all(np.linalg.norm(m2["C"] - q["C"]) > 40 for q in self.mugs):
                    self.mugs.append(m2)
        self.log("%d mugs found" % len(self.mugs))
        keepouts = []
        used = []
        todo = list(self.mugs)
        # choose an order: the mug with fewer candidates first
        cands = {}
        for i, m in enumerate(todo):
            cands[i] = candidates(m, self.rack, keepouts, self.zt)
            self.log("mug %d: %d candidates" % (i, len(cands[i])))
        order = sorted(range(len(todo)), key=lambda i: len(cands[i]))
        # rounds: every mug gets a turn before a mug that failed late (after grasping) is tried again
        pending = list(order)
        tried = {i: set() for i in order}
        for rnd in range(3):
            for i in list(pending):
                m = todo[i]
                cl = [p for p in candidates(m, self.rack, keepouts, self.zt, used_j=used)
                      if (p.j, p.sigma, int(round(p.s_d))) not in tried[i]]
                if not cl:
                    self.log("mug %d: no feasible candidate" % i)
                    pending.remove(i)
                    continue
                for p in cl[:8]:
                    if self.b.r.time_s() > 620:
                        self.log("out of time")
                        break
                    tried[i].add((p.j, p.sigma, int(round(p.s_d))))
                    self.log("mug %d -> branch %d sigma %d s_d %.0f delta %.0f cost %.2f clear %s" % (
                        i, p.j, p.sigma, p.s_d, math.degrees(p.delta), p.cost,
                        {k: tuple(round(x, 1) for x in v) for k, v in p.clear.items()}))
                    self.late = False
                    r = self.hang(p, keepouts)
                    if r == "flip":
                        self.log("grasp out of reach with this wrist turn; trying the other")
                        r = self.hang(p, keepouts, sg_opts=(-self.last_sg,))
                    if r == "done":
                        keepouts.append(keepout_for(p))
                        used.append(p.j)
                        pending.remove(i)
                        break
                    if r == "fatal" or self.late:
                        break
            if not pending or self.b.r.time_s() > 620:
                break
        for i in pending:
            self.log("mug %d not hung" % i)
        self.finish()

    def finish(self):
        b = self.b
        b.r.set_gripper(ARM, 80)
        try:
            b.r.wait(2.0)
        except Exception:
            pass
        t = b.tcp()
        if t[2] < 400:
            b.move([t[0], t[1], min(Z_SAFE, t[2] + 150)])
        r = b.r.home(ARM)
        if not r.get("done"):
            self.log("home failed", r)
            b.move([-450, 40, 450], [0, -1, 0])
            b.r.home(ARM)
        try:
            self.log("judge", b.r.judge())
        except Exception:
            pass

    def inspect(self, m):
        """closer look at a hollow round thing whose handle the overview did not see."""
        b = self.b
        t = b.tcp()
        if t[2] < Z_SAFE - 5:
            b.move([t[0], t[1], Z_SAFE])
        toward = unit(np.array([-790.0, 40.0]) - m["C"])
        ok = False
        for back in (0.0, 70.0, 130.0):
            xy = m["C"] + back * toward
            for z in (Z_SAFE, m["z_rim"] + HOVER + 60):
                if b.move([xy[0], xy[1], max(z, self.rack["top"] + 40 if self.rack else z)]).get("done"):
                    if b.move([xy[0], xy[1], m["z_rim"] + HOVER]).get("done"):
                        ok = True
                    break
            if ok:
                break
        if not ok:
            self.log("inspect %s: unreachable" % np.round(m["C"], 0).tolist())
            return None
        P, f = b.look()
        m2 = fit_mug(P, self.zt, m["C"], self.rng, r_hint=m["r_fit"])
        self.log("inspect %s -> %s" % (np.round(m["C"], 0).tolist(), None if m2 is None else
                                       (m2["handle"], m2["hollow"], m2.get("n_handle"), m2.get("h_width"),
                                        m2.get("n_att"))))
        return m2

    def go_high(self, xy, c=None, z=Z_SAFE):
        b = self.b
        t = b.tcp()
        if t[2] < z - 5:
            b.move([t[0], t[1], z], c)
        for zz in (z, z - 60, z - 120):
            r = b.move([xy[0], xy[1], zz], c)
            if r.get("done"):
                return True
        return False

    def goto_yaw(self, xy, c, z_min):
        """go above xy and turn to the exact closing dir c, trying several heights (>= z_min)."""
        b = self.b
        t = b.tcp()
        if t[2] < Z_SAFE - 5:
            b.move([t[0], t[1], Z_SAFE])
        tried = set()
        for z in (Z_SAFE, 490, 420, 360, 300, z_min + 20, z_min):
            z = max(z, z_min)
            if z in tried:
                continue
            tried.add(z)
            r = b.move([xy[0], xy[1], z])
            if not r.get("done"):
                continue
            if b.turn_to(c):
                return True
        # far out the wrist may not turn there: turn closer to the robot, then go out in a straight line
        toward = unit(np.array([-790.0, 40.0]) - np.asarray(xy[:2], float))
        for back in (150.0, 260.0):
            w = np.asarray(xy[:2], float) + back * toward
            for z in (490, 420, Z_SAFE):
                z = max(z, z_min)
                if not b.move([w[0], w[1], z]).get("done"):
                    continue
                if b.turn_to(c) and b.move([xy[0], xy[1], z], c).get("done"):
                    return True
                break
        return False

    def move_to(self, p, c, contact=False):
        """move while holding a mug: the arm sags under the load, so close the loop on the measured TCP / yaw."""
        b = self.b
        p = np.asarray(p, float)
        a_c = angof(c)
        r = b.move(p + self.corr, hz(a_c + self.ycorr), contact)
        if contact:
            return r
        for _ in range(2):
            if not r.get("done"):
                break
            err = p - b.tcp()
            ye = wrap(2.0 * (a_c - angof(b.cdir()))) / 2.0
            if np.linalg.norm(err) < 1.0 and abs(ye) < math.radians(0.5):
                break
            self.corr = self.corr + err
            self.ycorr = self.ycorr + ye
            r = b.move(p + self.corr, hz(a_c + self.ycorr), contact)
        return r

    def push_line(self, a, e, c, F0, step=4.0, thr=3.0):
        """move a -> e in small free steps, stopping when the external force differs from F0 (the held mug's
        weight) by more than thr: a contact test relative to the load (stop_on_contact counts the load too)."""
        b = self.b
        L = float(np.linalg.norm(e - a))
        n = max(1, int(math.ceil(L / step)))
        cc = hz(angof(c) + self.ycorr)
        Fp = F0
        for i in range(1, n + 1):
            q = a + (e - a) * (i / n)
            r = b.move(q + self.corr, cc, False)
            F = np.array(b.st()["force_n"], float)
            if not r.get("done"):
                return {"done": False, "why": r.get("why") or "refused"}
            # a contact is a jump between steps (the load's estimate drifts slowly with the arm's pose)
            if np.linalg.norm(F - Fp) > thr or np.linalg.norm(F - F0) > 2.2 * thr:
                return {"done": False, "why": "contact"}
            Fp = 0.5 * (Fp + F)
        return {"done": True, "why": ""}

    def lower_until_support(self, p, c, step=2.0, max_drop=28.0):
        """lower the held mug from p until the rack takes a good part of its weight (the arm's load drops);
        returns the TCP position there, or None."""
        b = self.b
        F0 = np.array(b.st()["force_n"], float)
        W = max(2.0, abs(F0[2]))
        need = max(1.6, 0.45 * W)
        cc = hz(angof(c) + self.ycorr)
        n = int(max_drop / step)
        for k in range(1, n + 1):
            q = p - step * k * UP
            r = b.move(q + self.corr, cc, False)
            F = np.array(b.st()["force_n"], float)
            if not r.get("done"):
                return None
            if F[2] - F0[2] > need:
                # confirm: one more step must keep (or raise) the support
                q2 = q - step * UP
                b.move(q2 + self.corr, cc, False)
                F2 = np.array(b.st()["force_n"], float)
                if F2[2] - F0[2] > need:
                    self.log("  supported after %.0f mm (dFz %.1f, %.1f of W %.1f)" % (
                        step * (k + 1), F[2] - F0[2], F2[2] - F0[2], W))
                    return q2
        return None

    def thread(self, T_far, T_hang, c_h, v, sigma, s_pass):
        """slide the handle onto the branch; on contact back off and shift; accept a stop once the handle is
        well past the branch tip (s_pass: travel after which it is)."""
        b = self.b
        F0 = np.array(b.st()["force_n"], float)
        L = float(np.linalg.norm(T_hang - T_far))
        # search pattern (absolute offsets: lateral in the hole first, the hole's measured centre tends to sit
        # too close to the wall, then higher / lower)
        sv = sigma * v
        pattern = [np.zeros(3), 4.0 * sv, -4.0 * sv, 8.0 * sv, -8.0 * sv,
                   4.0 * sv + 6.0 * UP, -4.0 * sv + 6.0 * UP, -6.0 * UP]
        for attempt in range(len(pattern)):
            if b.r.time_s() > 760:
                break
            off = pattern[attempt]
            a, e = T_far + off, T_hang + off
            if attempt:
                self.move_to(a, c_h)
                F0 = np.array(b.st()["force_n"], float)
            r = self.push_line(a, e, c_h, F0)
            t = b.tcp()
            prog = float((t - a) @ (e - a)) / max(L, 1e-6)
            F = np.array(b.st()["force_n"], float)
            dF = F - F0
            self.log("thread off(v %.0f z %.0f): done %s why %s progress %.0f/%.0f dF %s" % (
                off @ v, off[2], r.get("done"), r.get("why"), prog, L, np.round(dF, 1).tolist()))
            here = None
            if r.get("done"):
                here = e
            elif r.get("why") == "contact" and prog > s_pass + 12 and prog > 0.7 * L:
                # the handle may be well onto the branch (something else touched): try to hang it here
                self.log("  contact after the handle passed the tip: trying to hang here")
                back = (e - a) / max(L, 1e-6)
                here = a + (prog - 2.0) * back
                self.move_to(here, c_h)
            if here is not None:
                zs = self.lower_until_support(here, c_h)
                if zs is not None:
                    return zs
                self.log("  the branch does not carry the mug: backing out")
                self.move_to(here, c_h)
            if r.get("why") not in ("contact", "") and not r.get("done"):
                self.move_to(a, c_h)
                return None
            self.move_to(a, c_h)
        return None

    def path_clear(self, a, e, m, c, keepouts):
        """clearance of the gripper + held mug (mug body towards -c... at -Rm*g, g = c) moving a -> e."""
        parts = rack_parts(self.rack)
        body_l, _ = mug_local(m)
        grip = gripper_local(6.0)
        n = int(math.ceil(np.linalg.norm(e - a) / 10.0)) + 1
        best = 1e9
        for t in np.linspace(0, 1, n):
            T = a + t * (e - a)
            for g in (c, -c):
                pts = np.vstack([to_world(grip, T, g), to_world(body_l, T, g)])
                best = min(best, clearance(pts, self.rack, parts, keepouts=keepouts))
        return best

    def transport(self, xy, z_tr, m, c, keepouts, holding=False):
        """carry the held mug (yaw already c) to above xy, around / over the rack."""
        b = self.b
        t = b.tcp()
        z_hi = min(600.0, self.rack["top"] + (m["H"] - gd(m)) + 35)
        opts = []
        z_lo = self.zt + 120 + (m["H"] - gd(m))
        zs = [z_tr, max(z_tr, z_hi), z_tr + 40, max(z_lo, z_tr - 40)]
        for z in zs:
            opts.append([np.array([t[0], t[1], z]), np.array([xy[0], xy[1], z])])
        A = self.rack["A"]
        for z in (z_tr, z_tr + 40):
            for ang in np.radians([180, 150, 210, 120, 240, 90, 270]):
                for rad in (260.0, 320.0):
                    W = np.array([A[0] + rad * math.cos(ang), A[1] + rad * math.sin(ang), z])
                    opts.append([np.array([t[0], t[1], z]), W, np.array([xy[0], xy[1], z])])
        for path in opts:
            pts = [t] + path
            ok = all(self.path_clear(pts[i], pts[i + 1], m, c, keepouts) > 8 for i in range(1, len(pts) - 1))
            if not ok:
                continue
            good = True
            for q in path:
                r = self.move_to(q, c) if holding else b.move(q, c)
                if not r.get("done"):
                    good = False
                    break
            if good:
                return True
            self.log("transport path refused; trying another")
        self.log("no clear transport path")
        return False

    def put_back(self, G, c, z_tr, m=None):
        b = self.b
        t = b.tcp()
        self.move_to([t[0], t[1], max(z_tr, t[2])], c)
        if m is None or not self.transport(G[:2], z_tr, m, c, [], holding=True):
            self.move_to([G[0], G[1], z_tr], c)
        self.move_to(G + 3 * UP, c)
        b.r.set_gripper(ARM, 34)
        self.corr = np.zeros(3)
        self.ycorr = 0.0
        b.move(G + 80 * UP, c)

    def hang(self, p, keepouts, sg_opts=(1, -1)):
        b = self.b
        m, br = p.m, p.br
        z_min_rack = self.rack["top"] + 40
        # ---- 1. hang hover: observe the branch with the hang yaw
        sg_choice = None
        for s_g in sg_opts:
            self.last_sg = s_g
            c_h = s_g * br.bh
            hov0 = p.T_hang + hover_off(m, br.bh, c_h) + (gd(m) + HOVER) * UP
            toward = np.array([*unit(np.array([-790.0, 40.0]) - hov0[:2]), 0.0])
            if not (self.goto_yaw(hov0[:2], c_h, max(z_min_rack, hov0[2])) or
                    self.goto_yaw((hov0 + 90.0 * toward)[:2], c_h, max(z_min_rack, hov0[2]))):
                continue
            # the matched pose, else a nearby one (a few cm of shift change the camera's error by ~1 mm)
            for sh, dz in ((0, 0), (50, 0), (0, -30), (90, 0), (50, -30), (90, -30)):
                hov = hov0 + sh * toward + dz * UP
                if b.move(hov, c_h).get("done"):
                    sg_choice = s_g
                    break
            if sg_choice is not None:
                break
        if sg_choice is None:
            self.log("cannot reach the hang hover")
            return "fail"
        s_g = sg_choice
        c_h = s_g * br.bh
        P, f = b.look()
        nb = refine_branch(br, P)
        if nb is not None:
            self.log("branch refined: d_xy %.1f dz %.1f daz %.1f" % (
                float((nb.xy(p.s_d) - br.xy(p.s_d)) @ br.v[:2]), nb.ztop(p.s_d) - br.ztop(p.s_d),
                math.degrees(wrap(nb.az() - br.az()))))
            nb.knob = br.knob
            br_ref = nb
        else:
            self.log("branch refine failed; using overview")
            br_ref = br
        if DEBUG:
            b.show("hang_hover", f.rgb)
        # ---- 2. grasp hover: observe the mug with the grasp yaw
        c_g = s_g * p.g_tab
        ghov = p.G + hover_off(m, p.g_tab, c_g) + (gd(m) + HOVER) * UP
        if not self.goto_yaw(ghov[:2], c_g, ghov[2]):
            self.log("cannot reach grasp yaw")
            return "fail"
        toward = np.array([*unit(np.array([-790.0, 40.0]) - ghov[:2]), 0.0])
        r = {}
        for sh in (0.0, 50.0, 90.0):
            r = b.move(ghov + sh * toward, c_g)
            if r.get("done"):
                break
        if not r.get("done"):
            self.log("cannot reach the grasp hover")
            return "fail"
        P, f = b.look()
        m2 = fit_mug(P, self.zt, m["C"], self.rng, r_hint=m["r_fit"])
        if m2 is not None and m2["handle"] and (abs(wrap(m2["th"] - m["th"])) > math.radians(60) or
                                                np.linalg.norm(m2["C"] - m["C"]) > 35):
            self.log("mug refine implausible (dth %.0f, dC %.0f)" % (
                math.degrees(wrap(m2["th"] - m["th"])), np.linalg.norm(m2["C"] - m["C"])))
            m2 = None
        if m2 is not None and m2["handle"] and abs(wrap(m2["th"] - m["th"])) > math.radians(15):
            # the overview's handle direction was off: look again from the hover that matches the new grasp yaw
            q = make_plan(m2, br, p.j, p.sigma, p.s_d, self.rack, self.zt)
            cq = s_g * q.g_tab
            gh2 = q.G + hover_off(m2, q.g_tab, cq) + (gd(m2) + HOVER) * UP
            if b.move(gh2, cq).get("done"):
                P, f = b.look()
                m3 = fit_mug(P, self.zt, m2["C"], self.rng, r_hint=m2["r_fit"])
                ok3 = m3 is not None and m3["handle"] and abs(wrap(m3["th"] - m2["th"])) < math.radians(12)
                self.log("re-look after handle correction %.0f deg: %s" % (
                    math.degrees(wrap(m2["th"] - m["th"])), ok3))
                if ok3:
                    m2 = m3
        if m2 is None or not m2["handle"]:
            self.log("mug refine failed", None if m2 is None else (m2["handle"], m2.get("n_handle")))
            try:
                px, dd = f.project(np.array([[m["C"][0], m["C"][1], m["z_rim"]]]))
                self.log("  ghov %s tcp %s eye %s c %s C-> px %s" % (
                    np.round(ghov, 0).tolist(), np.round(b.tcp(), 0).tolist(), np.round(f.eye, 0).tolist(),
                    np.round(b.cdir(), 2).tolist(), np.round(px, 0).tolist()))
            except Exception as e:
                self.log("dbg fail", repr(e))
            if DEBUG:
                b.show("grasp_hover_fail", f.rgb)
            return "fail"
        else:
            self.log("mug refined: dC %s dth %.1f r %.1f H %.0f rim %.0f hole_rc %.1f r_out %.1f rh %.1f hole_top-rim %.1f" % (
                np.round(m2["C"] - m["C"], 1).tolist(), math.degrees(wrap(m2["th"] - m["th"])), m2["r_fit"], m2["H"],
                m2["z_rim"] - self.zt, m2["hole_rc"], m2["r_out"], m2["rh"], m2["hole_top"] - m2["z_rim"]))
        if DEBUG:
            b.show("grasp_hover", f.rgb)
        tr = b.truth()
        if tr:
            ds = [(np.linalg.norm(m2["C"] - np.array(v[:2])), k, v) for k, v in tr.items() if "mug" in k]
            if ds:
                d, k, v = min(ds)
                self.log("TRUTH %s: C err %s, rim-z_sup %.1f" % (k, np.round(m2["C"] - np.array(v[:2]), 1).tolist(),
                                                                 m2["z_rim"] - v[2]))
        p2 = make_plan(m2, br_ref, p.j, p.sigma, p.s_d, self.rack, self.zt)
        z_tr = transport_z(m2, self.rack, self.zt, p2)
        ok2 = check_plan(p2, self.rack, keepouts, z_tr)
        if not ok2:
            self.log("refined plan collides:", {k: tuple(round(x, 1) for x in v) for k, v in p2.clear.items()})
            lo = br_ref.s_root + m2["R_out"] + 8
            hi = br_ref.s_tip - 16
            alts = []
            for s_d in np.linspace(lo, hi, 9) if hi > lo else []:
                q = make_plan(m2, br_ref, p.j, p.sigma, float(s_d), self.rack, self.zt)
                if check_plan(q, self.rack, keepouts, transport_z(m2, self.rack, self.zt, q)):
                    alts.append((abs(s_d - p.s_d), q))
            if alts:
                alts.sort(key=lambda a: a[0])
                p2 = alts[0][1]
                z_tr = transport_z(m2, self.rack, self.zt, p2)
                self.log("  re-planned s_d %.0f" % p2.s_d)
            else:
                self.log("  no collision-free s_d; trying anyway")
        c_g2 = s_g * p2.g_tab
        GD = gd(m2)
        # ---- 3. grasp
        b.r.set_gripper(ARM, 34)
        r = b.move(p2.G + (GD + 35) * UP, c_g2)
        r = b.move(p2.G, c_g2, contact=True)
        t = b.tcp()
        if t[2] > p2.G[2] + 5:
            self.log("grasp descent stopped early at %.1f (target %.1f): %s" % (t[2], p2.G[2], r.get("why")))
            b.move(t + 25 * UP)
            if r.get("why") != "contact":
                return "flip"
            return "fail"
        w = b.r.set_gripper(ARM, 0)
        self.log("grasp width %.1f" % w)
        self.late = True
        if not (0.8 < w < 14):
            b.r.set_gripper(ARM, 80)
            b.move(b.tcp() + 60 * UP)
            return "fail"
        # ---- 4. lift, turn, transport
        t = b.tcp()
        self.corr = np.zeros(3)
        self.ycorr = 0.0
        z_lmin = m2["z_sup"] + (m2["H"] - GD) + 40.0
        lifted = None
        for zz in (z_tr, z_tr - 60, z_tr - 120, z_lmin):
            zz = max(zz, z_lmin)
            if self.move_to([t[0], t[1], zz], c_g2).get("done"):
                lifted = zz
                break
        if lifted is None:
            self.log("cannot lift the mug")
            b.r.set_gripper(ARM, 34)
            b.move(b.tcp() + 60 * UP)
            return "fail"
        if lifted < z_tr - 5:
            base_dir = angof(np.array([-790.0, 40.0]) - t[:2])
            ok = False
            mug_off = -m2["Rm"] * p2.g_tab[:2]
            bottom = lifted - (m2["H"] - GD)
            for da in (0, 35, -35, 70, -70):
                toward = hz(base_dir + math.radians(da))[:2]
                for dd in (110.0, 170.0, 240.0):
                    q = t[:2] + dd * toward
                    # keep the low sweep of the mug clear of everything else seen on the table
                    clear = True
                    for (oc, orad, otop) in self.obstacles:
                        if np.linalg.norm(oc - m2["C"]) < 45 or otop < bottom - 15:
                            continue
                        for s in np.linspace(0, 1, 8):
                            mc = t[:2] + s * (q - t[:2]) + mug_off
                            if np.linalg.norm(mc - oc) < orad + m2["R_out"] + 15:
                                clear = False
                    if not clear:
                        continue
                    if self.move_to([q[0], q[1], lifted], c_g2).get("done") and \
                            self.move_to([q[0], q[1], z_tr], c_g2).get("done"):
                        ok = True
                        break
                if ok:
                    break
            self.log("lifted at %.0f, moved in towards the robot: %s" % (lifted, ok))
            t = b.tcp()
        tr = b.truth()
        if tr:
            k = min([k for k in tr if "mug" in k], key=lambda k: np.linalg.norm(np.array(tr[k][:2]) - m2["C"]))
            rel = np.array(tr[k]) - b.tcp()
            exp = np.array([*(-m2["Rm"] * p2.g_tab[:2]), GD - m2["H"]])
            gg = p2.g_tab
            dd = rel - exp
            self.log("TRUTH held mug offset err: along g %.1f, along w %.1f, z %.1f" % (
                dd[:2] @ gg[:2], dd[:2] @ perp(gg)[:2], dd[2]))
        c_h = s_g * br_ref.bh
        turned = b.turn_to(c_h)
        if not turned:
            t = b.tcp()
            for zz in (z_tr + 60, z_tr - 50):
                if b.move([t[0], t[1], zz]).get("done") and b.turn_to(c_h):
                    turned = True
                    break
        if not turned:
            self.log("turn with mug failed")
            self.put_back(p2.G, c_g2, z_tr)
            return "fail"
        T_far = plan_tcp(p2, p2.s_far)
        T_hang = plan_tcp(p2, p2.s_d)
        self.move_to([t[0], t[1], z_tr], c_h)
        if not self.transport(T_far[:2], z_tr, m2, c_h, keepouts, holding=True):
            self.log("transport failed")
            self.put_back(p2.G, c_g2, z_tr)
            return "fail"
        r = self.move_to(T_far, c_h)
        st = b.st()
        self.log("at far point; force %s, tcp err %s, corr %s, ycorr %.1f" % (
            np.round(st["force_n"], 1).tolist(), np.round(T_far - b.tcp(), 1).tolist(),
            np.round(self.corr, 1).tolist(), math.degrees(self.ycorr)))
        # ---- 5. thread (stop on contact, retry higher / lower)
        cos_a = 1.0 / math.sqrt(1.0 + br_ref.slope ** 2)
        s_pass = (p2.s_far - br_ref.s_tip) / cos_a
        rel = self.thread(T_far, T_hang, c_h, br_ref.v, p2.sigma, s_pass)
        if rel is None:
            self.log("threading failed; putting the mug back")
            self.move_to(T_far, c_h)
            self.move_to(T_far + (z_tr - T_far[2]) * UP, c_h)
            if self.transport(p2.G[:2], z_tr, m2, c_h, keepouts, holding=True) and b.turn_to(c_g2):
                self.put_back(p2.G, c_g2, z_tr)
            else:
                self.log("cannot put the mug back; releasing it here")
                b.r.set_gripper(ARM, 80)
            self.corr = np.zeros(3)
            self.ycorr = 0.0
            return "fail"
        st = b.st()
        self.log("lowered: force %s" % np.round(st["force_n"], 1).tolist())
        b.r.set_gripper(ARM, 80)
        self.corr = np.zeros(3)
        self.ycorr = 0.0
        b.r.wait(0.7)
        t = b.tcp()
        b.move(t + 70 * UP)
        b.move([t[0], t[1], max(t[2] + 70, z_tr)])
        try:
            self.log("judge after hang:", b.r.judge())
        except Exception:
            pass
        return "done"


def test_grasp(sk):
    b = sk.b
    b.r.set_gripper(ARM, 80)
    sk.overview()
    tr = b.truth()
    for mi, m in enumerate(sk.mugs[:2]):
        for sigma in (1,):
            g = hz(m["th"] + sigma * math.pi / 2)
            GD = gd(m)
            G = np.array([*(m["C"] + m["Rm"] * g[:2]), m["z_rim"] - GD])
            sk.goto_yaw(G[:2], g, m["z_rim"] + 150)
            b.move(G + (GD + HOVER) * UP, g)
            P, f = b.look()
            m2 = fit_mug(P, sk.zt, m["C"], sk.rng)
            if m2 is not None and m2["handle"]:
                m = m2
            G = np.array([*(m["C"] + m["Rm"] * g[:2]), m["z_rim"] - GD])
            tr = b.truth()
            k = min([k for k in tr if "mug" in k], key=lambda k: np.linalg.norm(np.array(tr[k][:2]) - m["C"]))
            mc = np.array(tr[k])
            sk.log("mug %s true C %s, G radial dist from true axis %.1f (Rm %.1f)" % (
                k, np.round(mc, 1).tolist(), np.linalg.norm(G[:2] - mc[:2]), m["Rm"]))
            b.r.set_gripper(ARM, 34)
            b.move(G + (GD + 20) * UP, g)
            b.move(G, g, contact=True)
            w = b.r.set_gripper(ARM, 0)
            tr = b.truth()
            sk.log("closed w %.1f mug now %s" % (w, np.round(tr[k], 1).tolist()))
            P, f = b.look()
            b.show("closed_%d" % mi, f.rgb)
            for dzl in (3, 10, 40, 150):
                t = b.tcp()
                b.move(G + dzl * UP, g)
                b.r.wait(0.5)
                tr = b.truth()
                st = b.st()
                sk.log("lift %d: tcp %s mug %s rel %s opening %.1f force %s" % (
                    dzl, np.round(b.tcp(), 1).tolist(), np.round(tr[k], 1).tolist(),
                    np.round(np.array(tr[k]) - b.tcp(), 1).tolist(), st["opening_mm"],
                    np.round(st["force_n"], 1).tolist()))
            sc = b.r.look(["scene"])["scene"]
            b.show("held_%d" % mi, sc.rgb)
            b.move(G + 3 * UP, g)
            b.r.set_gripper(ARM, 80)
            b.move(G + (GD + 60) * UP, g)
    b.r.home(ARM)


TEST = None


def run(robot, task):
    sk = Skill(robot)
    if TEST == "grasp":
        test_grasp(sk)
        return
    if DEBUG:
        sk.run()
        return
    try:
        sk.run()
    except Exception as e:
        print("EXCEPTION", repr(e))
        try:
            sk.finish()
        except Exception as e2:
            print("finish failed", e2)
