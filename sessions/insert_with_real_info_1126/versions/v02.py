import math
import numpy as np
import cv2

ARM = "left"
DN = (0.0, 0.0, -1.0)
LOOK_D = 330.0            # camera-to-top distance for the precise looks (mm)
SURVEY = (-330.0, 120.0, 560.0)
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
    def survey(self):
        self.travel(SURVEY)
        f = self.look()
        P = f.points()
        z = P[..., 2]
        ok = np.isfinite(z)
        zz = z[ok]
        hist, edges = np.histogram(zz, bins=np.arange(-100, 420, 2.0))
        k = int(np.argmax(hist))
        sel = zz[(zz >= edges[k] - 4) & (zz <= edges[k + 1] + 4)]
        zt = float(np.median(sel))
        self.z_table = zt
        print("survey: table z %.1f" % zt)
        zf = np.where(ok, z, zt).astype(np.float32)
        zf = cv2.medianBlur(zf, 3) if False else zf
        hz = zf - zt
        # ---- stick: narrow tall peak (top-hat with a 41 px disk)
        th = hz - cv2.morphologyEx(hz, cv2.MORPH_OPEN, _disk(20))
        cand = (th > 22).astype(np.uint8)
        n, lab, stats, cents = cv2.connectedComponentsWithStats(cand, connectivity=8)
        sticks = []
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] < 4:
                continue
            m = lab == i
            pts = P[m & ok]
            if len(pts) < 4:
                continue
            ztop = float(np.percentile(pts[:, 2], 90))
            top = pts[pts[:, 2] > ztop - 12]
            c = np.median(top[:, :2], axis=0)
            rr = np.hypot(top[:, 0] - c[0], top[:, 1] - c[1])
            rad = float(np.percentile(rr, 90)) / 0.95 if len(rr) > 3 else 3.0
            # surround: points 20..45 mm from c
            allp = P[ok]
            dd = np.hypot(allp[:, 0] - c[0], allp[:, 1] - c[1])
            sur = allp[(dd > max(rad + 8, 18)) & (dd < 50)]
            zsur = float(np.median(sur[:, 2])) if len(sur) > 10 else zt
            rise = ztop - zsur
            sticks.append(dict(c=c, top=ztop, rad=rad, rise=rise, zsur=zsur))
        sticks = [s for s in sticks if s["rise"] > 28 and s["rad"] < 22 and -640 < s["c"][0] < -40]
        sticks.sort(key=lambda s: s["rad"] - 0.05 * s["rise"])
        for s in sticks:
            print("  stick cand c=%s top=%.1f rad=%.1f rise=%.1f" % (np.round(s["c"], 1), s["top"], s["rad"], s["rise"]))
        # ---- ring candidates: round raised tops (top-hat with an 85 px disk)
        th2 = hz - cv2.morphologyEx(hz, cv2.MORPH_OPEN, _disk(42))
        cand2 = (th2 > 10).astype(np.uint8)
        cand2 = cv2.morphologyEx(cand2, cv2.MORPH_OPEN, _disk(2))
        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(cand2, connectivity=8)
        rings = []
        for i in range(1, n2):
            if stats2[i, cv2.CC_STAT_AREA] < 60:
                continue
            m = (lab2 == i) & ok
            pts = P[m]
            if len(pts) < 40:
                continue
            ztop = float(np.percentile(pts[:, 2], 95))
            tm = m & (z > ztop - 8)
            # largest connected part of the top
            nn, ll, ss, _ = cv2.connectedComponentsWithStats(tm.astype(np.uint8), connectivity=8)
            if nn < 2:
                continue
            j = 1 + int(np.argmax(ss[1:, cv2.CC_STAT_AREA]))
            tm = ll == j
            cnts, _ = cv2.findContours(tm.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if not cnts:
                continue
            cnt = max(cnts, key=cv2.contourArea)
            filled = np.zeros(tm.shape, np.uint8)
            cv2.drawContours(filled, [cnt], -1, 1, -1)
            fpts = P[(filled > 0) & ok & (z > ztop - 8)]
            if len(fpts) < 20:
                continue
            c = np.median(fpts[:, :2], axis=0)
            (cu, cv_), rpx = cv2.minEnclosingCircle(cnt)
            area = float(filled.sum())
            roundness = area / (math.pi * rpx * rpx + 1e-6)
            dcam = float(np.nanmedian(f.depth[filled > 0]))
            rad = math.sqrt(area / math.pi) * dcam / f.fx
            allp = P[ok]
            dd = np.hypot(allp[:, 0] - c[0], allp[:, 1] - c[1])
            sur = allp[(dd > rad + 8) & (dd < rad + 30)]
            zsur = float(np.median(sur[:, 2])) if len(sur) > 10 else zt
            rings.append(dict(c=c, top=ztop, rad=rad, round=roundness, rise=ztop - zsur, uv=(cu, cv_)))
        good = [r for r in rings if 9 <= r["rad"] <= 38 and r["rise"] > 10 and r["round"] > 0.5
                and -640 < r["c"][0] < -40]
        if sticks:
            s0 = sticks[0]
            good = [r for r in good if np.hypot(*(r["c"] - s0["c"])) > 25]
        good.sort(key=lambda r: -r["round"])
        for r in good:
            print("  ring cand c=%s top=%.1f rad=%.1f round=%.2f rise=%.1f" % (np.round(r["c"], 1), r["top"], r["rad"], r["round"], r["rise"]))
        # obstacles: everything above the table
        self.max_top = float(np.percentile(zf[hz > 8], 99.5)) if np.any(hz > 8) else zt + 50
        self.z_safe = max(self.z_safe, self.max_top + 60)
        print("  max top %.1f -> z_safe %.1f" % (self.max_top, self.z_safe))
        img = f.rgb.copy()
        for s in sticks[:3]:
            uv, _ = f.project(np.array([[s["c"][0], s["c"][1], s["top"]]]))
            cv2.circle(img, (int(uv[0][0]), int(uv[0][1])), 8, (255, 0, 0), 2)
        for r in good[:4]:
            cv2.circle(img, (int(r["uv"][0]), int(r["uv"][1])), 12, (0, 255, 0), 2)
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
        farm = inwin & np.isfinite(d) & (np.abs(d - d_far) < 6)
        zfar = float(np.nanmedian(P[..., 2][farm])) if farm.sum() > 10 else float("nan")
        inner = filled & (np.hypot(uu - u, vv - v) < 0.55 * rpx)
        hole_px = int(np.sum(inner & np.isfinite(d) & (d > d_top + 8)))
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

    def judge(self):
        try:
            return self.robot.judge()
        except Exception:
            return None


def run(robot, task):
    sk = Skill(robot)
    try:
        ok = sk.run(task)
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
