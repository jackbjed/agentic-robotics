import math
import numpy as np
import cv2

ARM = "left"
DN = (0.0, 0.0, -1.0)
LOOK_D = 330.0            # camera-to-top distance for the precise looks (mm)
SURVEY = (-400.0, 120.0, 600.0)
FINGER = 16.0             # fingertips below the TCP
TCP_MIN = 13.0            # lowest TCP z we command (robot frame)
TEST_FALLBACK = True      # testing only: skip the first survey's stick candidates


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
        self.stick_c = None
        self.cam_ref = None
        self.T_stick = None

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
        elif r.get("curved", False):
            print("  (curved joint move to %s)" % np.round(p, 1))
        return r

    def move_precise(self, p, cd=None, tol=0.3, iters=3):
        """Plain move, then re-command with the measured xy error until the TCP is within tol (the real arm can end
        a few mm off). Returns (result, the xy actually commanded)."""
        tgt = np.asarray(p, float)
        cmd = tgt.copy()
        r = self.move(cmd, cd=cd)
        for _ in range(iters):
            if not r.get("moved", False):
                break
            m = self.tcp()
            err = tgt[:2] - m[:2]
            if math.hypot(err[0], err[1]) < tol:
                break
            cmd[:2] += err
            r = self.move(cmd, cd=cd)
        return r, cmd

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
            if self.cam_ref is None:
                self.cam_ref = self.cam_off.copy()
        return f

    def ensure_orientation(self, ref=None):
        """The wrist may come out of a move turned (even by 180 deg: camera on the other side): turn it back to the
        reference orientation (given by the camera's offset from the TCP)."""
        ref = self.cam_ref if ref is None else ref
        if ref is None:
            return
        for _ in range(5):
            f = self.robot.look(["left_wrist"])["left_wrist"]
            off = np.array(f.eye, float) - self.tcp()
            a = math.degrees(math.atan2(ref[0] * off[1] - ref[1] * off[0],
                                        ref[0] * off[0] + ref[1] * off[1]))
            if abs(a) < 8.0:
                self.cam_off = off
                return True
            print("  hand yaw off by %.0f deg: rotating back" % a)
            if not self.rotate_by(-max(-60.0, min(60.0, a))):
                print("  cannot restore the hand's orientation")
                return False
        return False

    def rotate_by(self, deg):
        """Rotate the hand about the vertical in place by a free move (unambiguous for |deg| < 90)."""
        cdv = np.array(self.robot.state(ARM)["closing_dir"], float)
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        new = np.array([c * cdv[0] - s * cdv[1], s * cdv[0] + c * cdv[1], 0.0])
        r = self.move(self.tcp(), cd=new)
        return bool(r.get("done", False))

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

    def survey(self, poses=(SURVEY,), min_depth=380.0):
        allpts = []
        f = None
        for pose in poses:
            self.travel(pose)
            self.ensure_orientation()
            f = self.look()
            P = f.points()
            z = P[..., 2]
            ok = np.isfinite(z) & np.isfinite(f.depth) & (f.depth > min_depth)
            H_, W_ = z.shape
            vv_, uu_ = np.mgrid[0:H_, 0:W_]
            ok &= ~((vv_ > 360) & ((uu_ < 185) | (uu_ > 455)))     # the fingers and their flying pixels
            allpts.append(P[ok])
        pts = np.concatenate(allpts, axis=0)
        zz = pts[:, 2]
        hist, edges = np.histogram(zz, bins=np.arange(-100, 420, 2.0))
        k = int(np.argmax(hist))
        sel = zz[(zz >= edges[k] - 4) & (zz <= edges[k + 1] + 4)]
        zt = float(np.median(sel))
        self.z_table = zt
        print("survey (%d views): table z %.1f" % (len(poses), zt))
        # ---- top-down heightmap (max z per 2 mm cell)
        nx = int((self.HX1 - self.HX0) / self.CS)
        ny = int((self.HY1 - self.HY0) / self.CS)
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
        th = hz - cv2.morphologyEx(hz, cv2.MORPH_OPEN, _disk(11))
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
        sticks = [s for s in sticks if s["rise"] > 35 and 3.5 < s["eqd"] < 38]
        sticks.sort(key=lambda s: -s["rise"] + 2.0 * max(0.0, s["eqd"] - 24.0))
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
        self.ensure_orientation()
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
            if math.hypot(du, dv) < 8:
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
                delta = max((w - d_est) / 2.0 + abs(shift), 1.0)
                s = 1.0 if shift >= 0 else -1.0
                plan = [s * delta, -s * delta, s * delta * 1.5, -s * delta * 1.5]
            if not plan:
                break
            off = plan.pop(0)
            tgt = origin + axis * off
            self.move([tgt[0], tgt[1], z], cd=np.array([axis[0], axis[1], 0.0]))
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
        for s0 in sticks[:4]:
            sg = np.array([s0["c"][0], s0["c"][1], s0["top"]])
            st, fst = self.centred_look(sg, 70, 15, "stick", max_iter=1)
            if st is not None and st["r_mm"] < 15.5 and st["z_top"] - st["z_far"] > 30 and st["hole_px"] < 6:
                self.stick_c = s0["c"]
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
            self.stick_v = np.array([T[0], T[1], s_top])
            self.stick_top = s_top
            self.stick_base = base
            return True
        self.show("calib", self.annot(fc, cal))
        self.stick_v = cal["p"].copy()
        self.e = cal["p"] - np.array([T[0], T[1], 0.0])
        self.e[2] = 0.0
        print("  camera error e = %s" % np.round(self.e[:2], 2))
        self.stick_top = cal["z_top"]
        self.stick_base = cal["z_far"] if np.isfinite(cal["z_far"]) else base
        return True

    def find_ring(self, rings):
        best, bscore = None, -1
        sc = self.stick_c
        if sc is not None:
            rings = [r for r in rings if np.hypot(*(r["c"] - sc)) > 35]
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
            res["c_survey"] = np.array(rc["c"], float)
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
        if r.get("why") == "contact" and c[2] - FINGER > z_top - 6:
            print("  landed on the ring?")
            return False
        self.move([c[0], c[1], c[2] + 3.0])
        zc = float(self.tcp()[2])
        # ---- centre the ring between the fingers along both axes (small pushes), then calibrate on it
        axA = self.closing_xy()
        wA = self.grip(0)
        TA = self.tcp()
        self.grip(80)
        T_r = None
        ang = 0.0
        for a_try in (90.0, -90.0):
            if self.turn_rel(a_try):
                ang = a_try
                break
        if ang != 0.0:
            axB = self.closing_xy()
            wB = self.grip(0)
            TB = self.tcp()
            self.grip(80)
            print("  ring closes: wA %.1f wB %.1f" % (wA, wB))
            if wA > 3 and wB > 3 and abs(wA - wB) < 3.0:
                M = np.array([axA, axB])
                T_r = np.linalg.solve(M, np.array([np.dot(TA[:2], axA), np.dot(TB[:2], axB)]))
                t = self.truth()
                if t:
                    print("   [dbg] ring top - T_r:", np.round(np.array(t["w_ring_top"][:2]) - T_r, 2))
            c = self.tcp()
            t = self.truth()
            if t:
                self.show("before_lift", self.robot.look(["scene"])["scene"].rgb)
                print("   [dbg] state", self.robot.state(ARM))
            self.move([c[0], c[1], z_top + 25], cd=np.array(self.robot.state(ARM)["closing_dir"], float))
            if t:
                self.show("after_lift", self.robot.look(["scene"])["scene"].rgb)
                print("   [dbg] ring after lift", self.truth()["w_ring"], "was", t["w_ring"])
            self.turn_rel(-ang)
        if T_r is not None:
            cal, fc = self.centred_look(np.array([T_r[0], T_r[1], z_top]), 110, 10, "ringcal")
            if cal is not None:
                e2 = cal["p"][:2] - T_r
                print("  ring calibration e = %s (stick-based %s)" % (np.round(e2, 2), np.round(self.e[:2], 2)))
                if np.linalg.norm(e2 - self.e[:2]) < 4.0:
                    self.e = np.array([e2[0], e2[1], 0.0])
                    self.stick_xy = self.stick_v[:2] - e2
                    t = self.truth()
                    if t:
                        print("   [dbg] stick xy err now:", np.round(self.stick_xy - np.array(t["w_stick_top"][:2]), 2))
            x, y = float(T_r[0]), float(T_r[1])
        if self.tcp()[2] > zc + 2.0:
            self.travel([x, y, z_top + 25])
            self.move([x, y, zc + 8])
            self.move([x, y, zc], contact=True)
        w = self.grip(0)
        c_true = np.array([x, y])
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
        self.ensure_orientation(self.cam_grasp)   # the held ring's small offset from the TCP must not be mirrored
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
            _, cmd = self.move_precise([tx + ox, ty + oy, z1 if i == 0 else zc + 2.5])
            r = self.move([cmd[0], cmd[1], z2], contact=True)
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

    # ================================================================ v10 flow: one closing axis only
    def run3(self, task):
        sticks, rings = self.survey()
        st = None if TEST_FALLBACK else self.confirm_stick(sticks)
        if st is None or not rings:
            print("survey again: closer, two views")
            sticks2, rings2 = self.survey(poses=((-380.0, -40.0, 430.0), (-380.0, 220.0, 430.0)), min_depth=300.0)
            if st is None:
                st = self.confirm_stick(sticks2)
            if rings2:
                rings = rings2
        if st is None:
            print("stick not confirmed")
            return False
        self.dbg("stick (raw)", st["p"], "w_stick_top")
        self.A = self.closing_xy()
        self.B = np.array([-self.A[1], self.A[0]])
        if not self.stick_estimate(st):
            return False
        ring = self.find_ring(rings)
        if ring is None:
            return False
        if not self.grasp_ring3(ring):
            return False
        return self.insert2()

    def confirm_stick(self, sticks):
        st = None
        for s0 in sticks[:4]:
            sg = np.array([s0["c"][0], s0["c"][1], s0["top"]])
            st, fst = self.centred_look(sg, 70, 15, "stick", max_iter=1)
            if st is not None and st["r_mm"] < 15.5 and st["z_top"] - st["z_far"] > 30 and st["hole_px"] < 6:
                self.stick_c = s0["c"]
                if math.hypot(st["uv"][0] - fst.cx, st["uv"][1] - fst.cy) > 6:
                    st2, _ = self.centred_look(st["p"], 70, 15, "stick", max_iter=1)
                    if st2 is not None:
                        st = st2
                break
            st = None
        return st

    def stick_estimate(self, st):
        """Close on the stick along A (stall-aware) for a first estimate of the camera error along A, then measure
        the stick at rest with a centred look."""
        x, y = float(st["p"][0]), float(st["p"][1])
        s_top, base = float(st["z_top"]), float(st["z_far"])
        self.e = np.zeros(3)
        zg = max(base + FINGER - 3.0, TCP_MIN, s_top - 100.0)
        self.grip(80)
        self.travel([x, y, s_top + 25])
        self.move([x, y, zg + 15])
        r = self.move([x, y, zg], contact=True)
        c = self.tcp()
        print("  stick grip descent: %s z=%.1f (zg %.1f base %.1f top %.1f)" % (r.get("why"), c[2], zg, base, s_top))
        T = None
        TB = None
        axB = None
        if c[2] < zg + 25:
            self.move([c[0], c[1], c[2] + 3.0])
            zc = float(self.tcp()[2])
            self.d_ref = max(3.0, 2.0 * float(st["r_mm"]) - 5.0)
            self.d_min = None
            T = self.close_axis(self.A, zc)
            if T is not None:
                # close along B too (estimate of the camera error along B): lift above the stick, reorient by a
                # free (collision-checked) move, descend, close, lift, reorient back
                c = self.tcp()
                zt = s_top + 25
                self.move([c[0], c[1], zt])
                ok1 = self.rotate_by(45.0)
                if not ok1:
                    self.ensure_orientation()
                    ok1 = self.rotate_by(-45.0)
                if ok1:
                    axB = self.closing_xy()
                    cdB = np.array([axB[0], axB[1], 0.0])
                    r2 = self.move([c[0], c[1], zc], cd=cdB)
                    if r2.get("done", False):
                        TB = self.close_axis(axB, zc)
                    c = self.tcp()
                    self.move([c[0], c[1], zt], cd=cdB)
                else:
                    print("  cannot reorient for the B close")
        c = self.tcp()
        self.move([c[0], c[1], s_top + 25], cd=np.array(self.robot.state(ARM)["closing_dir"], float))
        self.ensure_orientation()
        cal, fc = self.centred_look(np.array([x, y, s_top]), 70, 15, "stick_rest")
        if cal is None:
            print("  stick rest look failed")
            return False
        self.show("stick", self.annot(fc, cal))
        self.stick_v = cal["p"].copy()
        self.stick_top = cal["z_top"]
        self.stick_base = cal["z_far"] if np.isfinite(cal["z_far"]) else base
        if T is not None:
            eA = float(np.dot(cal["p"][:2] - T[:2], self.A))
            eB = 0.0
            S = None
            dl = cal["p"][:2] - st["p"][:2]          # true displacement of the stick by the closes (camera)
            print("  stick displaced by the closes: %s" % np.round(dl, 2))
            if TB is not None and axB is not None and abs(float(np.dot(axB, self.A))) < 0.95:
                # each close moved the stick along its own axis only: dl = a*A + b*u
                u = axB
                a_, b_ = np.linalg.solve(np.column_stack([self.A, u]), dl)
                # start position P0: (P0 + a A).A = T.A ; (P0 + dl).u = TB.u
                M = np.array([self.A, u])
                P0 = np.linalg.solve(M, np.array([np.dot(T[:2], self.A) - a_, np.dot(TB[:2], u) - np.dot(dl, u)]))
                S = P0 + dl
                eA = float(np.dot(cal["p"][:2] - S, self.A))
                eB = float(np.dot(cal["p"][:2] - S, self.B))
                if abs(eB) > 25 or abs(eA) > 25:
                    eB, S = 0.0, None
                    eA = float(np.dot(cal["p"][:2] - T[:2], self.A))
            ev = self.A * eA + self.B * eB
            self.e = np.array([ev[0], ev[1], 0.0])
            print("  stick-based camera error: A %.2f, B %.2f" % (eA, eB))
            # the stick's position from contact alone
            if S is None:
                S = self.A * float(np.dot(T[:2], self.A)) + self.B * float(np.dot(cal["p"][:2], self.B))
            self.T_stick = S
            t = self.truth()
            if t:
                print("   [dbg] contact stick position err:", np.round(S - np.array(t["w_stick_top"][:2]), 2))
        self.dbg("stick rest (raw)", cal["p"], "w_stick_top")
        return True

    def choose_yaw(self, c, z_sup):
        """Grasp yaw (deg, relative to A) whose finger zones are free of obstacles in the survey heightmap."""
        hz, seen = self.hm_hz, self.hm_seen
        nx, ny = hz.shape
        thr = (z_sup - self.z_table) + 8.0
        best = (1e9, 0.0)
        for th in (0.0, 30.0, -30.0, 60.0, -60.0, 90.0):
            ca, sa = math.cos(math.radians(th)), math.sin(math.radians(th))
            u = np.array([ca * self.A[0] - sa * self.A[1], sa * self.A[0] + ca * self.A[1]])
            v = np.array([-u[1], u[0]])
            m = -1e9
            for sg in (1.0, -1.0):
                for s in np.arange(38.0, 84.0, 3.0):
                    for t in np.arange(-24.0, 25.0, 3.0):
                        p = c + u * s * sg + v * t
                        i = int((p[0] - self.HX0) / self.CS)
                        j = int((p[1] - self.HY0) / self.CS)
                        if 0 <= i < nx and 0 <= j < ny and seen[i, j]:
                            m = max(m, float(hz[i, j]))
            score = m - thr
            if score <= 0:
                return th
            if score < best[0]:
                best = (score, th)
        print("  no free grasp yaw; least obstructed %.0f deg (%.0f mm)" % (best[1], best[0]))
        return best[1]

    def grasp_ring3(self, ring):
        g = ring["p"][:2] - self.e[:2]
        x, y = float(g[0]), float(g[1])
        z_top, z_sup = float(ring["z_top"]), float(ring["z_far"])
        if not np.isfinite(z_sup):
            z_sup = z_top - 20
        th = self.choose_yaw(np.array(ring.get("c_survey", g)), z_sup)
        ca, sa = math.cos(math.radians(th)), math.sin(math.radians(th))
        A = np.array([ca * self.A[0] - sa * self.A[1], sa * self.A[0] + ca * self.A[1]])
        B = np.array([-A[1], A[0]])
        cdA = np.array([A[0], A[1], 0.0])
        if th != 0.0:
            print("  grasp yaw %.0f deg (obstacles near the ring)" % th)
        self.ring_r = ring["r_mm"]
        zg = min(max(z_sup + FINGER - 3.0, TCP_MIN), z_top + 2.0)
        self.grip(80)
        self.travel([x, y, z_top + 30])
        if th != 0.0:
            done = True
            for k in range(int(math.ceil(abs(th) / 45.0))):
                step = th / math.ceil(abs(th) / 45.0)
                done = done and self.rotate_by(step)
            if not done:
                print("  cannot turn to the grasp yaw")
                return False
        self.move([x, y, z_top + 8], cd=cdA)
        r = self.move([x, y, zg], contact=True, cd=cdA)
        c = self.tcp()
        print("  ring grasp descent: %s z=%.1f (zg %.1f, top %.1f, sup %.1f)" % (r.get("why"), c[2], zg, z_top, z_sup))
        if c[2] - FINGER > z_top - 6:
            print("  landed on the ring?")
            return False
        self.move([c[0], c[1], c[2] + 3.0], cd=cdA)
        zc = float(self.tcp()[2])
        wA = self.grip(0)
        TA = self.tcp()
        self.grip(80)
        print("  ring close A: %.1f" % wA)
        if wA < 3:
            return False
        t = self.truth()
        self.robot.wait(1.0)
        if t:
            print("   [dbg] ring top - TCP after close:", np.round(np.array(t["w_ring_top"][:2]) - TA[:2], 2))
        # ---- look at the ring where it now stands (centred along A at TA)
        self.move([TA[0], TA[1], z_top + 25], cd=cdA)
        cal, fc = self.centred_look(np.array([x, y, z_top]), 110, 10, "ringcal")
        if cal is None:
            print("  ring calibration look failed")
            return False
        self.show("ringcal", self.annot(fc, cal))
        if abs(cal["z_top"] - z_top) > 6 or abs(cal["r_mm"] - ring["r_mm"]) > 3:
            print("  the ring changed after the close (top %.1f -> %.1f, r %.1f -> %.1f)" % (
                z_top, cal["z_top"], ring["r_mm"], cal["r_mm"]))
        eA = float(np.dot(cal["p"][:2] - TA[:2], A))
        print("  camera error along A: ring %.2f, stick-based %.2f" % (eA, float(np.dot(self.e[:2], A))))
        if t:
            print("   [dbg] ring drift since close:", np.round(np.array(self.truth()["w_ring_top"]) - np.array(t["w_ring_top"]), 2))
        self.ring_v = cal["p"].copy()
        self.ring_top_v, self.ring_sup_v = cal["z_top"], (cal["z_far"] if np.isfinite(cal["z_far"]) else z_sup)
        self.ring_h = self.ring_top_v - self.ring_sup_v
        # ---- grasp it there
        self.travel([TA[0], TA[1], z_top + 25])
        if th != 0.0:
            for k in range(int(math.ceil(abs(th) / 45.0))):
                self.rotate_by(th / math.ceil(abs(th) / 45.0))
        self.move([TA[0], TA[1], zc + 8], cd=cdA)
        self.move([TA[0], TA[1], zc], contact=True, cd=cdA)
        w = self.grip(0)
        Tg = self.tcp()
        self.zg = float(Tg[2])
        self.cd = cdA                      # keep this hand orientation for the transport and insertion
        fg = self.robot.look(["left_wrist"])["left_wrist"]
        self.cam_grasp = np.array(fg.eye, float) - self.tcp()
        print("  ring grasp width %.1f" % w)
        if w < 3:
            return False
        eA_st = float(np.dot(self.e[:2], A))
        if self.T_stick is not None and abs(eA - eA_st) > 1.0:
            # the first close may not have centred the ring: the grasp close just did -> calibrate once more
            print("  ring and stick calibrations disagree by %.1f mm: one more calibration cycle" % (eA - eA_st))
            self.grip(80)
            self.robot.wait(1.0)
            TA2 = Tg.copy()
            self.move([TA2[0], TA2[1], z_top + 25], cd=cdA)
            cal2, fc2 = self.centred_look(np.array([x, y, z_top]), 110, 10, "ringcal2")
            if cal2 is not None:
                eA = float(np.dot(cal2["p"][:2] - TA2[:2], A))
                self.ring_v = cal2["p"].copy()
                print("  camera error along A (2nd): ring %.2f" % eA)
            self.travel([TA2[0], TA2[1], z_top + 25])
            if th != 0.0:
                for k in range(int(math.ceil(abs(th) / 45.0))):
                    self.rotate_by(th / math.ceil(abs(th) / 45.0))
            self.move([TA2[0], TA2[1], zc + 8], cd=cdA)
            self.move([TA2[0], TA2[1], zc], contact=True, cd=cdA)
            w = self.grip(0)
            Tg = self.tcp()
            self.zg = float(Tg[2])
            fg = self.robot.look(["left_wrist"])["left_wrist"]
            self.cam_grasp = np.array(fg.eye, float) - self.tcp()
            print("  ring grasp width %.1f" % w)
            if w < 3:
                return False
        # ---- the stick in the TCP frame: along A from the calibrated camera, along B relative to the ring
        sA = float(np.dot(self.stick_v[:2], A)) - eA
        sB = float(np.dot(Tg[:2], B)) + float(np.dot(self.stick_v[:2] - self.ring_v[:2], B))
        self.stick_xy = A * sA + B * sB
        if self.T_stick is not None:
            d = self.stick_xy - self.T_stick
            print("  vision target vs contact stick position: %s" % np.round(d, 2))
            if np.linalg.norm(d) > 6.0:
                print("  (vision and contact disagree; keeping the vision target)")
        t = self.truth()
        if t:
            ring_off = np.array(t["w_ring_top"][:2]) - Tg[:2]
            print("   [dbg] ring top - TCP (held):", np.round(ring_off, 2))
            # where the TCP must go so the ring centre is over the stick
            need = np.array(t["w_stick_top"][:2]) - ring_off
            print("   [dbg] TCP target err:", np.round(self.stick_xy - need, 2))
        return True

    def judge(self):
        try:
            return self.robot.judge()
        except Exception:
            return None


def run(robot, task):
    sk = Skill(robot)
    try:
        ok = sk.run3(task)
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
