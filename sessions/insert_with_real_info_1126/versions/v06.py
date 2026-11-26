import math
import numpy as np
import cv2

ARM = "left"
DN = (0.0, 0.0, -1.0)
LOOK_D = 330.0            # camera-to-top distance for the precise looks (mm)
SURVEY = (-400.0, 120.0, 600.0)
FINGER = 16.0             # fingertips below the TCP
TCP_MIN = 13.0            # lowest TCP z we command (robot frame)


def _disk(r):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


class Skill:
    def __init__(self, robot):
        self.robot = robot
        self.cd = np.array([0.0, -1.0, 0.0])
        self.cam_off = np.array([51.0, -2.9, 133.1])
        self.z_safe = 320.0
        self.nshow = 0
        self.t0 = robot.time_s()

    # ------------------------------------------------------------ utilities
    def truth(self):
        try:
            return self.robot.truth()
        except Exception:
            return None

    def dbg(self, label, p, key):
        t = self.truth()
        if t and key in t:
            print("   [dbg] %s err vs %s: %s" % (label, key, np.round(np.asarray(p, float) - np.asarray(t[key]), 2)))

    def show(self, label, img):
        if self.nshow < 12:
            self.nshow += 1
            try:
                self.robot.show(label, img)
            except Exception:
                pass

    def tcp(self):
        return np.array(self.robot.state(ARM)["tcp_mm"], float)

    def move(self, p, contact=False, cd=None):
        cd = self.cd if cd is None else np.asarray(cd, float)
        r = self.robot.move(ARM, [float(v) for v in p], list(DN), [float(v) for v in cd], bool(contact))
        if (not r.get("moved", False)) or (not r.get("done", False) and r.get("why") != "contact"):
            print("  move to %s -> %s" % (np.round(p, 1), r))
        return r

    def travel(self, p, zs=None):
        p = np.asarray(p, float)
        c = self.tcp()
        zs = self.z_safe if zs is None else zs
        if np.hypot(p[0] - c[0], p[1] - c[1]) > 15:
            zs = max(zs, p[2])
            if c[2] < zs - 3:
                self.move([c[0], c[1], zs])
            if p[2] < zs - 3:
                self.move([p[0], p[1], zs])
        return self.move(p)

    def look(self, name="left_wrist"):
        f = self.robot.look([name])[name]
        if name == "left_wrist":
            self.cam_off = np.array(f.eye, float) - self.tcp()
        return f

    def look_pose(self, target):
        """TCP position that puts the wrist camera LOOK_D above target (same orientation)."""
        t = np.asarray(target, float)
        return np.array([t[0] - self.cam_off[0], t[1] - self.cam_off[1], t[2] + LOOK_D - self.cam_off[2]])

    @staticmethod
    def backproject(f, u, v, d):
        x = (u - f.cx) / f.fx * d
        y = (v - f.cy) / f.fy * d
        return np.array(f.eye, float) + np.array(f.R, float) @ np.array([x, y, d])

    # ------------------------------------------------------------ survey
    HX0, HX1, HY0, HY1, CS = -660.0, -20.0, -340.0, 500.0, 2.0

    def survey(self):
        self.travel(SURVEY)
        f = self.look()
        P = f.points()
        z = P[..., 2]
        ok = np.isfinite(z) & np.isfinite(f.depth) & (f.depth > 380)
        H_, W_ = z.shape
        vv_, uu_ = np.mgrid[0:H_, 0:W_]
        ok &= ~((vv_ > 360) & ((uu_ < 185) | (uu_ > 455)))     # the fingers and their flying pixels
        zz = z[ok]
        hist, edges = np.histogram(zz, bins=np.arange(-100, 420, 2.0))
        k = int(np.argmax(hist))
        sel = zz[(zz >= edges[k] - 4) & (zz <= edges[k + 1] + 4)]
        zt = float(np.median(sel))
        self.z_table = zt
        print("survey: table z %.1f" % zt)
        # ---- top-down heightmap (max z per 2 mm cell)
        nx = int((self.HX1 - self.HX0) / self.CS)
        ny = int((self.HY1 - self.HY0) / self.CS)
        pts = P[ok]
        ix = np.floor((pts[:, 0] - self.HX0) / self.CS).astype(int)
        iy = np.floor((pts[:, 1] - self.HY0) / self.CS).astype(int)
        s_ = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        hm = np.full((nx, ny), -1e9)
        np.maximum.at(hm, (ix[s_], iy[s_]), pts[s_, 2])
        seen = hm > -1e8
        hz = np.where(seen, hm - zt, 0.0).astype(np.float32)
        hz = np.clip(hz, -20, 400)
        self.hm_hz, self.hm_seen = hz, seen

        def cell_xy(i, j):
            return np.array([self.HX0 + (i + 0.5) * self.CS, self.HY0 + (j + 0.5) * self.CS])

        def ring_median(c, r0, r1, q=50):
            i0 = (c[0] - self.HX0) / self.CS
            j0 = (c[1] - self.HY0) / self.CS
            ii, jj = np.mgrid[0:nx, 0:ny]
            d = np.hypot(ii - i0, jj - j0) * self.CS
            m = (d >= r0) & (d < r1) & seen
            return float(np.percentile(hz[m], q)) if m.sum() > 5 else 0.0

        # ---- stick: narrow tall peak (top-hat with a ~34 mm disk)
        th = hz - cv2.morphologyEx(hz, cv2.MORPH_OPEN, _disk(8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats((th > 25).astype(np.uint8), connectivity=8)
        sticks = []
        for i in range(1, n):
            m = lab == i
            peak = float(np.percentile(hz[m], 98))
            tm = m & (hz > peak - 10)
            ii, jj = np.nonzero(tm)
            w = 1.0
            c = cell_xy(ii.mean(), jj.mean())
            eqd = 2 * math.sqrt(tm.sum() * self.CS ** 2 / math.pi)
            if tm.sum() < 3:
                continue
            rise = peak - ring_median(c, eqd / 2 + 8, eqd / 2 + 26, 80)
            sticks.append(dict(c=c, top=peak + zt, eqd=eqd, rise=rise))
        sticks = [s for s in sticks if s["rise"] > 35 and 3.5 < s["eqd"] < 30]
        sticks.sort(key=lambda s: -s["rise"] + 1.0 * s["eqd"])
        for s in sticks:
            print("  stick cand c=%s top=%.1f eqd=%.1f rise=%.1f" % (np.round(s["c"], 1), s["top"], s["eqd"], s["rise"]))
        # ---- ring candidates: round raised tops (top-hat with an ~84 mm disk)
        th2 = hz - cv2.morphologyEx(hz, cv2.MORPH_OPEN, _disk(21))
        cand2 = cv2.morphologyEx((th2 > 12).astype(np.uint8), cv2.MORPH_OPEN, _disk(1))
        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(cand2, connectivity=8)
        rings = []
        for i in range(1, n2):
            m = lab2 == i
            if m.sum() < 30:
                continue
            top = float(np.percentile(hz[m], 95))
            tm = (m & (hz > top - 7)).astype(np.uint8)
            cnts, _ = cv2.findContours(tm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if not cnts:
                continue
            cnt = max(cnts, key=cv2.contourArea)
            filled = np.zeros(tm.shape, np.uint8)
            cv2.drawContours(filled, [cnt], -1, 1, -1)
            area = float(filled.sum())
            (cy_, cx_), rpx = cv2.minEnclosingCircle(cnt)
            roundness = area / (math.pi * rpx * rpx + 1e-6)
            ii, jj = np.nonzero(filled)
            c = cell_xy(ii.mean(), jj.mean())
            rad = math.sqrt(area / math.pi) * self.CS
            rise = top - ring_median(c, rad + 6, rad + 22)
            rings.append(dict(c=c, top=top + zt, rad=rad, round=roundness, rise=rise))
        for r in rings:
            print("   (all ring cands) c=%s top=%.1f rad=%.1f round=%.2f rise=%.1f" % (np.round(r["c"], 1), r["top"], r["rad"], r["round"], r["rise"]))
        good = [r for r in rings if 8 <= r["rad"] <= 40 and r["rise"] > 12 and r["round"] > 0.45]
        if sticks:
            s0 = sticks[0]
            good = [r for r in good if np.hypot(*(r["c"] - s0["c"])) > 30]
        good.sort(key=lambda r: -r["round"])
        for r in good:
            print("  ring cand c=%s top=%.1f rad=%.1f round=%.2f rise=%.1f" % (np.round(r["c"], 1), r["top"], r["rad"], r["round"], r["rise"]))
        # obstacles: everything above the table
        self.max_top = float(np.percentile(hz[hz > 8], 99.7)) + zt if np.any(hz > 8) else zt + 50
        self.z_safe = max(self.z_safe, self.max_top + 60)
        print("  max top %.1f -> z_safe %.1f" % (self.max_top, self.z_safe))
        img = f.rgb.copy()
        for s in sticks[:3]:
            uv, _ = f.project(np.array([[s["c"][0], s["c"][1], s["top"]]]))
            cv2.circle(img, (int(uv[0][0]), int(uv[0][1])), 8, (255, 0, 0), 2)
        for r in good[:4]:
            uv, _ = f.project(np.array([[r["c"][0], r["c"][1], r["top"]]]))
            cv2.circle(img, (int(uv[0][0]), int(uv[0][1])), 14, (0, 255, 0), 2)
        self.show("survey", img)
        return sticks, good

    # ------------------------------------------------------------ precise top-face measurement
    def top_face(self, f, guess, win, thr, min_px=8):
        d = f.depth.astype(np.float64)
        H, W = d.shape
        uv, _ = f.project(np.array([guess], float))
        u0, v0 = float(uv[0][0]), float(uv[0][1])
        vv, uu = np.mgrid[0:H, 0:W]
        rr = np.hypot(uu - u0, vv - v0)
        inwin = rr < win
        dw = np.where(inwin, d, np.nan)
        if np.sum(np.isfinite(dw)) < 50:
            return None
        d_far = float(np.nanpercentile(dw, 80))
        near = np.isfinite(dw) & (dw < d_far - thr)
        n, lab, stats, cents = cv2.connectedComponentsWithStats(near.astype(np.uint8), connectivity=8)
        best, bd = None, 1e9
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] < min_px:
                continue
            dist = math.hypot(cents[i][0] - u0, cents[i][1] - v0)
            if dist < bd:
                best, bd = i, dist
        if best is None:
            return None
        comp = lab == best
        d_top = float(np.percentile(d[comp], 3))
        region = cv2.dilate(comp.astype(np.uint8), _disk(3)) > 0
        top = region & np.isfinite(d) & (d < d_top + 5.0)
        cnts, _ = cv2.findContours(top.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            return None
        cnt = max(cnts, key=cv2.contourArea)
        filled = np.zeros((H, W), np.uint8)
        cv2.drawContours(filled, [cnt], -1, 1, -1)
        filled = filled > 0
        ys, xs = np.nonzero(filled)
        u, v = float(xs.mean()), float(ys.mean())
        area = float(filled.sum())
        rpx = math.sqrt(area / math.pi)
        dm = float(np.median(d[top & filled]))
        p = self.backproject(f, u, v, dm)
        P = f.points()
        ztop = float(np.nanmedian(P[..., 2][top & filled]))
        p[2] = ztop
        ra = np.hypot(uu - u, vv - v)
        farm = (ra > rpx + 8) & (ra < rpx + 30) & np.isfinite(d) & (d > d_top + 8)
        zfar = float(np.nanmedian(P[..., 2][farm])) if farm.sum() > 10 else float("nan")
        inner = filled & (np.hypot(uu - u, vv - v) < 0.6 * rpx) & ~top
        hole_px = 0
        nh, lh, sh, ch = cv2.connectedComponentsWithStats(inner.astype(np.uint8), connectivity=8)
        for i in range(1, nh):
            if math.hypot(ch[i][0] - u, ch[i][1] - v) < 0.3 * rpx:
                hole_px = max(hole_px, int(sh[i, cv2.CC_STAT_AREA]))
        return dict(p=p, uv=(u, v), r_mm=rpx * dm / f.fx, rpx=rpx, z_top=ztop, z_far=zfar, hole_px=hole_px,
                    d_top=d_top, mask=filled, n_top=int(np.sum(top & filled)))

    def centred_look(self, guess, win, thr, label, max_iter=2):
        """Put the wrist camera above guess, measure, recentre once if far from the image centre."""
        g = np.asarray(guess, float)
        res, f = None, None
        for it in range(max_iter):
            self.travel(self.look_pose(g))
            f = self.look()
            res = self.top_face(f, g, win, thr)
            if res is None:
                print("  %s: nothing found (iter %d)" % (label, it))
                return None, f
            du, dv = res["uv"][0] - f.cx, res["uv"][1] - f.cy
            print("  %s it%d: p=%s uv_off=(%.1f, %.1f) r=%.1f hole_px=%d zfar=%.1f" % (
                label, it, np.round(res["p"], 2), du, dv, res["r_mm"], res["hole_px"], res["z_far"]))
            g = res["p"].copy()
            if math.hypot(du, dv) < 5:
                break
        return res, f

    # ------------------------------------------------------------ gripper helpers
    def grip(self, w):
        return float(self.robot.set_gripper(ARM, float(w)))

    def turn_z(self, ang):
        c = self.tcp()
        r = self.robot.turn(ARM, [float(v) for v in c], [0.0, 0.0, 1.0], float(ang))
        return r

    def closing_xy(self):
        cdv = np.array(self.robot.state(ARM)["closing_dir"], float)[:2]
        return cdv / (np.linalg.norm(cdv) + 1e-9)

    def close_axis(self, axis, z):
        """Close on the stick; if the opening shows it off-centre (one finger stalled on it), shift by half the
        excess toward the side the arm yielded to (or try both sides) and close again. Returns the TCP while closed."""
        origin = self.tcp()[:2].copy()
        plan = []
        best = None
        for attempt in range(5):
            before = self.tcp()
            w = self.grip(0)
            after = self.tcp()
            self.grip(80)
            shift = float(np.dot(after[:2] - before[:2], axis))
            ref = self.d_ref + 2.5 if self.d_min is None else max(self.d_ref + 2.5, self.d_min + 0.8)
            ok = 1.0 < w <= ref
            print("   close along (%.2f, %.2f): w=%.1f shift=%.2f ok=%s" % (axis[0], axis[1], w, shift, ok))
            if w > 1.0 and (best is None or w < best[0]):
                best = (w, after.copy())
            if ok:
                self.d_min = w if self.d_min is None else min(self.d_min, w)
                t = self.truth()
                if t:
                    print("   [dbg] stick top - TCP:", np.round(np.array(t["w_stick_top"][:2]) - after[:2], 2))
                return after
            if w < 1.0:
                return None
            if attempt == 0:
                d_est = self.d_min if self.d_min is not None else self.d_ref
                delta = max((w - d_est) / 2.0, 1.0)
                s = 1.0 if shift >= 0 else -1.0
                plan = [s * delta, -s * delta, s * delta * 1.5, -s * delta * 1.5]
            if not plan:
                break
            off = plan.pop(0)
            tgt = origin + axis * off
            self.move([tgt[0], tgt[1], z])
        if best is not None and best[0] <= self.d_ref + 5.0:
            print("   using the narrowest close (w=%.1f)" % best[0])
            return best[1]
        return None

    def yaw(self):
        cdv = np.array(self.robot.state(ARM)["closing_dir"], float)
        return math.degrees(math.atan2(cdv[1], cdv[0]))

    def turn_rel(self, ang):
        """Turn about the TCP's vertical by ang; if stopped on the way, lift 4 mm and finish once."""
        y0 = self.yaw()
        r = self.turn_z(ang)
        if r.get("done", False):
            return True
        if not r.get("moved", False):
            print("  turn %.0f refused: %s" % (ang, r.get("why", "")[:80]))
            return False
        phi = ((self.yaw() - y0 + 90.0) % 180.0) - 90.0
        print("  turn %.0f stopped after %.1f: %s" % (ang, phi, r.get("why", "")[:60]))
        c = self.tcp()
        self.move([c[0], c[1], c[2] + 4.0], cd=np.array(self.robot.state(ARM)["closing_dir"], float))
        r = self.turn_z(ang - phi)
        return bool(r.get("done", False))

    # ------------------------------------------------------------ main
    def run(self, task):
        sticks, rings = self.survey()
        if not sticks:
            print("no stick found")
            return False
        s0 = sticks[0]
        # ---- precise stick
        sg = np.array([s0["c"][0], s0["c"][1], s0["top"]])
        st, fst = self.centred_look(sg, 70, 15, "stick")
        if st is None:
            return False
        self.show("stick", self.annot(fst, st))
        self.dbg("stick (raw)", st["p"], "w_stick_top")
        self.stick = st
        # ---- find the ring among candidates
        ring = None
        for rc in rings[:4]:
            g = np.array([rc["c"][0], rc["c"][1], rc["top"]])
            res, fr = self.centred_look(g, 110, 10, "ringcand", max_iter=1)
            if res is None:
                continue
            print("   cand r=%.1f hole_px=%d h=%.1f" % (res["r_mm"], res["hole_px"], res["z_top"] - res["z_far"]))
            if res["hole_px"] >= 3 and 8 <= res["r_mm"] <= 36:
                ring = res
                self.show("ring", self.annot(fr, res))
                break
        if ring is None:
            print("no ring confirmed; using best candidate")
            if not rings:
                return False
            rc = rings[0]
            ring, fr = self.centred_look(np.array([rc["c"][0], rc["c"][1], rc["top"]]), 110, 10, "ringcand", 1)
            if ring is None:
                return False
        self.dbg("ring (raw)", ring["p"], "w_ring_top")
        # ---- centring grasps
        ok = self.centre_ring(ring)
        if not ok:
            return False
        return self.insert()

    def annot(self, f, res):
        img = f.rgb.copy()
        m = res["mask"].astype(np.uint8)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(img, cnts, -1, (255, 0, 0), 1)
        cv2.circle(img, (int(round(res["uv"][0])), int(round(res["uv"][1]))), 2, (0, 255, 0), -1)
        return img

    def centre_ring(self, ring):
        x, y = float(ring["p"][0]), float(ring["p"][1])
        z_top, z_sup = float(ring["z_top"]), float(ring["z_far"])
        if not np.isfinite(z_sup):
            z_sup = z_top - 20
        h = z_top - z_sup
        self.ring_h = h
        zg = max(z_sup + FINGER + 1.0, TCP_MIN)
        zg = min(zg, z_top + 4.0)
        self.grip(80)
        self.travel([x, y, z_top + 40])
        self.move([x, y, z_top + 10])
        r = self.move([x, y, zg], contact=True)
        c = self.tcp()
        print("  grasp descent: %s tcp=%s (zg %.1f, top %.1f, sup %.1f)" % (r.get("why"), np.round(c, 1), zg, z_top, z_sup))
        if r.get("why") == "contact" and c[2] > z_top - 3:
            print("  landed on the ring?")
            return False
        w1 = self.grip(0)
        print("  close A: width %.1f" % w1)
        if w1 < 3:
            return False
        self.grip(80)
        ang = 90.0
        r = self.turn_z(ang)
        if not r.get("moved", False):
            ang = -90.0
            r = self.turn_z(ang)
        if not r.get("moved", False):
            print("  turn refused", r)
            return False
        w2 = self.grip(0)
        T1 = self.tcp()
        print("  close B: width %.1f, T1 %s" % (w2, np.round(T1, 2)))
        t = self.truth()
        if t:
            print("   [dbg] ring top - T1:", np.round(np.array(t["w_ring_top"]) - T1, 2))
        self.grip(80)
        zg_real = float(T1[2])
        self.move([T1[0], T1[1], z_top + 30])
        r = self.turn_z(-ang)
        if not r.get("moved", False):
            print("  turn back refused", r)
        # ---- calibration look
        cal, fc = self.centred_look(np.array([T1[0], T1[1], z_top]), 110, 10, "calib")
        if cal is None:
            return False
        self.show("calib", self.annot(fc, cal))
        e = cal["p"] - np.array([T1[0], T1[1], 0.0])
        print("  camera error estimate e = %s" % np.round(e[:2], 2))
        self.e = e
        self.ring_top_v = cal["z_top"]
        self.ring_sup_v = cal["z_far"] if np.isfinite(cal["z_far"]) else z_sup
        self.ring_h = self.ring_top_v - self.ring_sup_v
        self.ring_r = cal["r_mm"]
        # ---- final grasp at T1
        self.travel([T1[0], T1[1], z_top + 30])
        self.move([T1[0], T1[1], zg_real + 8])
        r = self.move([T1[0], T1[1], zg_real], contact=True)
        w3 = self.grip(0)
        self.zg = float(self.tcp()[2])
        print("  final grasp width %.1f at z %.1f" % (w3, self.zg))
        if w3 < 3:
            return False
        t = self.truth()
        if t:
            print("   [dbg] ring top - TCP:", np.round(np.array(t["w_ring_top"]) - self.tcp(), 2))
        return True

    def insert(self):
        st = self.stick
        s = st["p"][:2] - self.e[:2]
        s_top = st["z_top"]
        base_v = st["z_far"] if np.isfinite(st["z_far"]) else s_top - 60
        t = self.truth()
        if t:
            print("   [dbg] corrected stick xy err:", np.round(s - np.array(t["w_stick_top"][:2]), 2))
        a = self.zg - self.ring_sup_v          # TCP above ring bottom (vision-relative)
        h = self.ring_h
        z1 = self.zg + (s_top - self.ring_sup_v) + 12.0
        hang = a  # ring bottom below TCP
        zs = max(self.z_safe + hang, z1 + 20)
        c = self.tcp()
        self.move([c[0], c[1], zs])
        self.move([s[0], s[1], zs])
        self.move([s[0], s[1], z1])
        kin = min(h - 2.0, 25.0)
        z2 = z1 - 12.0 - kin
        offs = [(0.0, 0.0)]
        for k, rad in enumerate([0.6, 1.2, 1.8, 2.5, 3.2]):
            nk = 6 * (k + 1)
            for j in range(nk):
                a_ = 2 * math.pi * j / nk
                offs.append((rad * math.cos(a_), rad * math.sin(a_)))
        threaded = False
        zc = z1
        for i, (ox, oy) in enumerate(offs):
            if self.robot.time_s() - self.t0 > 480:
                break
            if i > 0:
                self.move([s[0] + ox, s[1] + oy, zc + 2.5])
            r = self.move([s[0] + ox, s[1] + oy, z2], contact=True)
            c = self.tcp()
            if r.get("why") != "contact" or c[2] < z2 + 2.0:
                threaded = True
                print("  threaded at offset (%.1f, %.1f), try %d" % (ox, oy, i))
                break
            zc = float(c[2])
            if i == 0:
                print("  contact at z %.1f (expected top contact ~%.1f)" % (zc, z1 - 12))
        if not threaded:
            print("  could not thread")
        # push further down toward the base
        z3 = max(self.zg + (base_v - self.ring_sup_v) + 3.0, TCP_MIN, s_top - 95.0)
        c = self.tcp()
        if threaded and z3 < c[2] - 1:
            r = self.move([c[0], c[1], z3], contact=True)
            print("  push down: %s tcp z %.1f (z3 %.1f)" % (r.get("why"), self.tcp()[2], z3))
        self.grip(80)
        self.robot.wait(0.5)
        c = self.tcp()
        self.move([c[0], c[1], max(c[2] + 40, s_top + 40)])
        print("  judge:", self.judge())
        return threaded

    # ================================================================ v3 flow
    def run2(self, task):
        sticks, rings = self.survey()
        if not sticks:
            print("no stick found")
            return False
        # ---- stick: first look
        st = None
        for s0 in sticks[:3]:
            sg = np.array([s0["c"][0], s0["c"][1], s0["top"]])
            st, fst = self.centred_look(sg, 70, 15, "stick", max_iter=1)
            if st is not None and st["r_mm"] < 16 and st["z_top"] - st["z_far"] > 30:
                break
            st = None
        if st is None:
            print("stick not confirmed")
            return False
        self.dbg("stick (raw)", st["p"], "w_stick_top")
        # ---- centre the stick between the fingers (moves the stand), calibrate the camera on it
        if not self.centre_stick(st):
            return False
        # ---- ring
        ring = self.find_ring(rings)
        if ring is None:
            return False
        if not self.grasp_ring(ring):
            return False
        return self.insert2()

    def centre_stick(self, st):
        x, y = float(st["p"][0]), float(st["p"][1])
        s_top, base = float(st["z_top"]), float(st["z_far"])
        zg = max(base + FINGER - 3.0, TCP_MIN, s_top - 100.0)
        self.grip(80)
        self.travel([x, y, s_top + 25])
        self.move([x, y, zg + 15])
        r = self.move([x, y, zg], contact=True)
        c = self.tcp()
        print("  stick grip descent: %s z=%.1f (zg %.1f base %.1f top %.1f)" % (r.get("why"), c[2], zg, base, s_top))
        if c[2] > zg + 25:
            print("  blocked high: abort")
            return False
        self.move([c[0], c[1], c[2] + 3.0])     # fingertips off the base
        zc = float(self.tcp()[2])
        self.d_ref = max(3.0, 2.0 * float(st["r_mm"]) - 5.0)   # vision diameter (depth edges fatten ~5 mm)
        self.d_min = None
        axA = self.closing_xy()
        TA = self.close_axis(axA, zc)
        if TA is None:
            return False
        ang = 0.0
        for a_try in (90.0, -90.0):
            if self.turn_rel(a_try):
                ang = a_try
                break
        T = TA.copy()
        if ang != 0.0:
            axB = self.closing_xy()
            TB = self.close_axis(axB, float(self.tcp()[2]))
            if TB is not None:
                # combine: each close fixes the stick along its own axis
                pa = float(np.dot(TA[:2], axA))
                pb = float(np.dot(TB[:2], axB))
                M = np.array([axA, axB])
                xy = np.linalg.solve(M, np.array([pa, pb]))
                T = np.array([xy[0], xy[1], TB[2]])
        else:
            print("  turn impossible: only one close")
        t = self.truth()
        if t:
            print("   [dbg] stick top - T:", np.round(np.array(t["w_stick_top"]) - T, 2))
        self.stick_xy = T[:2].copy()
        self.stick_w = self.d_min
        c = self.tcp()
        self.move([c[0], c[1], s_top + 25], cd=np.array(self.robot.state(ARM)["closing_dir"], float))
        if ang != 0.0:
            self.turn_rel(-ang)
        cal, fc = self.centred_look(np.array([T[0], T[1], s_top]), 70, 15, "calib")
        if cal is None:
            print("  calibration look failed: e = 0")
            self.e = np.zeros(3)
            self.stick_top = s_top
            self.stick_base = base
            return True
        self.show("calib", self.annot(fc, cal))
        self.e = cal["p"] - np.array([T[0], T[1], 0.0])
        self.e[2] = 0.0
        print("  camera error e = %s" % np.round(self.e[:2], 2))
        self.stick_top = cal["z_top"]
        self.stick_base = cal["z_far"] if np.isfinite(cal["z_far"]) else base
        return True

    def find_ring(self, rings):
        best, bscore = None, -1
        for rc in rings[:4]:
            g = np.array([rc["c"][0] - self.e[0], rc["c"][1] - self.e[1], rc["top"]])
            res, fr = self.centred_look(g + np.r_[self.e[:2], 0.0], 110, 10, "ringcand", max_iter=2)
            if res is None:
                continue
            h = res["z_top"] - res["z_far"]
            print("   cand r=%.1f hole_px=%d h=%.1f" % (res["r_mm"], res["hole_px"], h))
            if not (7 <= res["r_mm"] <= 38 and h > 10):
                continue
            score = res["hole_px"]
            if score > bscore:
                best, bscore, bf = res, score, fr
            if score >= 10:
                break
        if best is None:
            print("no ring found")
            return None
        self.show("ring", self.annot(bf, best))
        self.dbg("ring (raw)", best["p"], "w_ring_top")
        return best

    def grasp_ring(self, ring):
        c_true = ring["p"][:2] - self.e[:2]
        t = self.truth()
        if t:
            print("   [dbg] ring corrected err:", np.round(c_true - np.array(t["w_ring_top"][:2]), 2))
        x, y = float(c_true[0]), float(c_true[1])
        z_top, z_sup = float(ring["z_top"]), float(ring["z_far"])
        if not np.isfinite(z_sup):
            z_sup = z_top - 20
        self.ring_top_v, self.ring_sup_v = z_top, z_sup
        self.ring_h = z_top - z_sup
        self.ring_r = ring["r_mm"]
        zg = max(z_sup + FINGER - 3.0, TCP_MIN)
        zg = min(zg, z_top + 2.0)
        self.grip(80)
        self.travel([x, y, z_top + 30])
        self.move([x, y, z_top + 8])
        r = self.move([x, y, zg], contact=True)
        c = self.tcp()
        print("  ring grasp descent: %s z=%.1f (zg %.1f, top %.1f, sup %.1f)" % (r.get("why"), c[2], zg, z_top, z_sup))
        if r.get("why") == "contact" and c[2] > z_top - 3:
            print("  landed on the ring?")
            return False
        self.move([c[0], c[1], c[2] + 3.0])
        w = self.grip(0)
        self.zg = float(self.tcp()[2])
        self.ring_xy_off = c_true - self.tcp()[:2]      # ring centre relative to TCP (along closing ~0)
        print("  ring grasp width %.1f" % w)
        t = self.truth()
        if t:
            print("   [dbg] ring top - TCP:", np.round(np.array(t["w_ring_top"]) - self.tcp(), 2))
        if w < 3:
            return False
        return True

    def insert2(self):
        s = self.stick_xy.copy()
        s_top = self.stick_top
        base_v = self.stick_base
        h = self.ring_h
        z1 = self.zg + (s_top - self.ring_sup_v) + 12.0
        hang = self.zg - self.ring_sup_v
        zs = max(self.z_safe + hang, z1 + 20)
        c = self.tcp()
        self.move([c[0], c[1], zs])
        tx, ty = s[0], s[1]
        r = self.move([tx, ty, zs])
        r = self.move([tx, ty, z1])
        if not r.get("done", False):
            print("  cannot reach above the stick")
            return False
        kin = min(h - 2.0, 25.0)
        z2 = z1 - 12.0 - kin
        offs = [(0.0, 0.0)]
        for k, rad in enumerate([0.6, 1.2, 1.8, 2.5]):
            nk = 6 * (k + 1)
            for j in range(nk):
                a_ = 2 * math.pi * j / nk
                offs.append((rad * math.cos(a_), rad * math.sin(a_)))
        threaded = False
        zc = z1
        for i, (ox, oy) in enumerate(offs):
            if self.robot.time_s() - self.t0 > 470:
                break
            if i > 0:
                self.move([tx + ox, ty + oy, zc + 2.5])
            r = self.move([tx + ox, ty + oy, z2], contact=True)
            c = self.tcp()
            if r.get("moved", False) and (r.get("why") != "contact" or c[2] < z2 + 2.0) and c[2] < z2 + 3.0:
                threaded = True
                print("  threaded at offset (%.1f, %.1f), try %d" % (ox, oy, i))
                break
            zc = float(c[2])
            if i == 0:
                print("  contact at z %.1f (top contact expected ~%.1f)" % (zc, z1 - 12))
        if not threaded:
            print("  could not thread")
            return False
        z3 = max(self.zg + (base_v - self.ring_sup_v) + 3.0, TCP_MIN, s_top - 95.0)
        c = self.tcp()
        if z3 < c[2] - 1:
            r = self.move([c[0], c[1], z3], contact=True)
            print("  push down: %s tcp z %.1f (z3 %.1f)" % (r.get("why"), self.tcp()[2], z3))
        self.grip(80)
        self.robot.wait(0.5)
        c = self.tcp()
        self.move([c[0], c[1], max(c[2] + 40, s_top + 40)])
        print("  judge:", self.judge())
        return True

    def judge(self):
        try:
            return self.robot.judge()
        except Exception:
            return None


def run(robot, task):
    sk = Skill(robot)
    try:
        ok = sk.run2(task)
    except Exception as ex:
        print("EXCEPTION", repr(ex))
        ok = False
    # open the gripper and go home
    try:
        robot.set_gripper(ARM, 80)
        c = sk.tcp()
        if c[2] < sk.z_safe:
            sk.move([c[0], c[1], sk.z_safe])
        r = robot.home(ARM)
        if not r.get("moved", False):
            print("home refused", r)
    except Exception as ex:
        print("EXCEPTION at end", repr(ex))
    print("time %.1f s, result %s" % (robot.time_s(), ok))
    print("final judge:", sk.judge())
