import math
import numpy as np
import cv2

A = "left"
DOWN = (0.0, 0.0, -1.0)
CAM_BACK = 133.2         # wrist camera: mm behind the TCP along approach
CAM_SIDE = 50.8          # mm to the side (c x a direction)
CAM_ALONG = 2.8          # mm along closing_dir
FAST_Z = 305.0           # above z = 300 the arm moves at 15 cm/s


def plane_ransac(P, rng, iters=250, tol=4.0):
    n = len(P)
    if n < 30:
        return None
    best, bi = None, -1
    for _ in range(iters):
        s = P[rng.integers(0, n, 3)]
        nv = np.cross(s[1] - s[0], s[2] - s[0])
        nn = np.linalg.norm(nv)
        if nn < 1e-6:
            continue
        nv = nv / nn
        if abs(nv[2]) < 0.97:
            continue
        d = -nv @ s[0]
        cnt = int(np.sum(np.abs(P @ nv + d) < tol))
        if cnt > bi:
            bi, best = cnt, (nv, d)
    if best is None:
        return None
    nv, d = best
    inl = P[np.abs(P @ nv + d) < tol]
    M = np.c_[inl[:, 0], inl[:, 1], np.ones(len(inl))]
    return np.linalg.lstsq(M, inl[:, 2], rcond=None)[0]


def pz(pl, x, y):
    return pl[0] * x + pl[1] * y + pl[2]


def rect_info(xy):
    r = cv2.minAreaRect(np.asarray(xy, np.float32))
    p = cv2.boxPoints(r)
    e1, e2 = p[1] - p[0], p[2] - p[1]
    l1, l2 = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
    if l1 <= l2:
        return np.array(r[0], float), l1, l2, e1 / max(l1, 1e-6)
    return np.array(r[0], float), l2, l1, e2 / max(l2, 1e-6)


def comps(mask, min_px=40):
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [(lab == i, st[i]) for i in range(1, n) if st[i, 4] >= min_px]


def grid_comps(P, m, cell=6.0, min_px=40):
    """split the pixels of mask m into objects by connectivity of their points seen from above (XY grid)"""
    pts = P[m]
    if len(pts) == 0:
        return []
    n = int(1300 / cell)
    ij = np.clip(np.floor((pts[:, :2] + 650.0) / cell).astype(int), 0, n - 1)
    G = np.zeros((n, n), np.uint8)
    G[ij[:, 0], ij[:, 1]] = 1
    G = cv2.dilate(G, np.ones((3, 3), np.uint8))
    nl, lab = cv2.connectedComponents(G)
    L = np.zeros(m.shape, np.int32)
    L[m] = lab[ij[:, 0], ij[:, 1]]
    out = []
    for k in range(1, nl):
        mk = L == k
        if mk.sum() >= min_px:
            out.append(mk)
    return out


class Skill:
    def __init__(self, robot):
        self.r = robot
        self.rng = np.random.default_rng(7)
        self.shown = 0

    def show(self, label, img):
        if self.shown < 12:
            self.shown += 1
            self.r.show(label, img)

    def t(self):
        return self.r.time_s()

    def move(self, xyz, cdir, contact=False, tag=""):
        res = self.r.move(A, [float(v) for v in xyz], DOWN, [float(v) for v in cdir], contact)
        if not res.get("done"):
            print("MOVE", tag, np.round(xyz, 1).tolist(), "->", res, "tcp", np.round(self.r.state(A)["tcp_mm"], 1).tolist())
        return res

    # ------------------------------------------------------------------ scene camera: rough places
    def cand(self, mask):
        P, H, objm = self.sP, self.sH, self.sobjm
        m = mask & objm
        if m.sum() < 15:
            return None
        h95 = float(np.percentile(H[m], 95))
        sel = m & (H > 0.5 * h95)
        xy = P[sel][:, :2]
        if len(xy) < 10:
            return None
        c, s, l, d = rect_info(xy)
        return dict(c=c, med=np.median(xy, axis=0), s=s, l=l, h=h95, n=int(m.sum()), mask=mask)

    def in_tray(self, xy, margin):
        T = self.tray
        if T is None:
            return False
        d = np.asarray(xy, float)[:2] - T["c"]
        return bool(abs(d @ T["dsh"]) < T["s"] / 2 + margin and abs(d @ T["dl"]) < T["l"] / 2 + margin)

    def scene_detect(self):
        S = self.r.look(["scene"])["scene"]
        P = S.points()
        X, Y, Z = P[..., 0], P[..., 1], P[..., 2]
        fin = np.isfinite(Z)
        with np.errstate(invalid="ignore"):
            area = fin & (X > -600) & (X < 560) & (np.abs(Y) < 560) & (np.abs(Z) < 110)
        pts = P[area]
        pl = plane_ransac(pts[:: max(1, len(pts) // 15000)], self.rng, tol=6.0)
        H = Z - pz(pl, X, Y)
        with np.errstate(invalid="ignore"):
            objm = fin & (H > 8) & (H < 140) & (X > -600) & (X < 250) & (np.abs(Y) < 590)
            objm &= ~((X < -420) & (Y < -270))
        self.sP, self.sH, self.sobjm = P, H, objm
        self.sk = cv2.morphologyEx(objm.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        tray = None
        for mk, sc in self.r.segment(S, "tray"):
            c = self.cand(mk)
            if c and 90 < c["l"] < 450 and c["h"] < 100:
                tray = c
                print("SCENE tray (SAM %.2f) c %s s %.0f l %.0f h %.0f" % (sc, np.round(c["c"]), c["s"], c["l"], c["h"]))
                break
        self.sam_blocks = []
        for mk, sc in self.r.segment(S, "block"):
            c = self.cand(mk)
            if not c or c["l"] > 170 or not (12 < c["h"] < 120):
                continue
            if tray is not None and (mk & tray["mask"]).sum() > 0.3 * mk.sum():
                continue
            c["src"] = "sam %.2f" % sc
            self.sam_blocks.append(c)
        if tray is None:
            cs = [c for c in (self.cand(m) for m in grid_comps(P, self.sk)) if c]
            big = [c for c in cs if 110 < c["l"] < 450]
            if big:
                tray = max(big, key=lambda c: c["n"])
                print("SCENE tray (depth) c %s l %.0f" % (np.round(tray["c"]), tray["l"]))
        vis = S.rgb.copy()
        for c, col in [(tray, (255, 0, 255))] + [(b, (0, 255, 0)) for b in self.sam_blocks[:1]]:
            if c is not None:
                cnt, _ = cv2.findContours(c["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cv2.drawContours(vis, cnt, -1, col, 1)
        self.show("scene_detect", vis)
        print("SCENE table plane z(-300,0) = %.1f" % float(pz(pl, -300, 0)))
        return pl, tray

    def block_cands(self):
        P = self.sP
        T = self.tray
        out = [c for c in self.sam_blocks if not self.in_tray(c["med"], 0)]
        m = self.sk.copy()
        if T is not None:
            X, Y = P[..., 0], P[..., 1]
            with np.errstate(invalid="ignore"):
                rx = (X - T["c"][0]) * T["dsh"][0] + (Y - T["c"][1]) * T["dsh"][1]
                ry = (X - T["c"][0]) * T["dl"][0] + (Y - T["c"][1]) * T["dl"][1]
                m &= ~((np.abs(rx) < T["s"] / 2 + 15) & (np.abs(ry) < T["l"] / 2 + 15))
        dep = []
        for mk in grid_comps(P, m):
            c = self.cand(mk)
            if c and c["l"] < 160 and 15 < c["h"] < 120 and -620 < c["med"][0] < -40:
                c["src"] = "depth"
                dep.append(c)
        dep.sort(key=lambda c: -c["n"])
        for c in dep:
            if all(np.linalg.norm(c["med"] - o["med"]) > 40 for o in out):
                out.append(c)
        for c in out[:5]:
            print("BLOCK cand", c["src"], np.round(c["med"]), "h %.0f l %.0f n %d" % (c["h"], c["l"], c["n"]))
        return out

    def scene_detect_old(self):
        S = self.r.look(["scene"])["scene"]
        P = S.points()
        X, Y, Z = P[..., 0], P[..., 1], P[..., 2]
        fin = np.isfinite(Z)
        with np.errstate(invalid="ignore"):
            area = fin & (X > -600) & (X < 560) & (np.abs(Y) < 560) & (np.abs(Z) < 110)
        pts = P[area]
        pts = pts[:: max(1, len(pts) // 15000)]
        pl = plane_ransac(pts, self.rng, tol=6.0)
        H = Z - pz(pl, X, Y)
        with np.errstate(invalid="ignore"):
            objm = fin & (H > 8) & (H < 140) & (X > -600) & (X < 250) & (np.abs(Y) < 590)
            objm &= ~((X < -420) & (Y < -270))

        def cand(mask):
            m = mask & objm
            if m.sum() < 15:
                return None
            h95 = float(np.percentile(H[m], 95))
            sel = m & (H > 0.5 * h95)
            xy = P[sel][:, :2]
            if len(xy) < 10:
                return None
            c, s, l, d = rect_info(xy)
            fill = float(np.mean(H[mask & fin] > 0.6 * h95)) if (mask & fin).sum() else 0.0
            return dict(c=c, med=np.median(xy, axis=0), s=s, l=l, h=h95, n=int(m.sum()), mask=mask, fill=fill)

        tray = None
        for mk, sc in self.r.segment(S, "tray"):
            c = cand(mk)
            if c and 90 < c["l"] < 450 and c["h"] < 100:
                tray = c
                print("SCENE tray (SAM %.2f) c %s s %.0f l %.0f h %.0f fill %.2f" % (sc, np.round(c["c"]), c["s"], c["l"], c["h"], c["fill"]))
                break
        block = None
        for mk, sc in self.r.segment(S, "block"):
            c = cand(mk)
            if not c or c["l"] > 170 or not (12 < c["h"] < 120):
                continue
            if tray is not None:
                inter = (mk & tray["mask"]).sum() / max(1, mk.sum())
                if inter > 0.3:
                    continue
            block = c
            print("SCENE block (SAM %.2f) med %s s %.0f l %.0f h %.0f fill %.2f" % (sc, np.round(c["med"]), c["s"], c["l"], c["h"], c["fill"]))
            break
        if tray is None or block is None:
            # fall back on depth components
            k = cv2.morphologyEx(objm.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
            cs = []
            for m in grid_comps(P, k):
                c = cand(m)
                if c:
                    cs.append(c)
            cs.sort(key=lambda c: -c["l"])
            if tray is None:
                big = [c for c in cs if c["l"] > 110]
                if big:
                    tray = big[0]
                    print("SCENE tray (depth) c", np.round(tray["c"]), "l", round(tray["l"]))
            if block is None:
                small = [c for c in cs if c["l"] < 150 and c["h"] > 15 and (tray is None or np.linalg.norm(c["c"] - tray["c"]) > 60)]
                small.sort(key=lambda c: -c["n"])
                if small:
                    block = small[0]
                    print("SCENE block (depth) med", np.round(block["med"]), "h", round(block["h"]))
        vis = S.rgb.copy()
        for c, col in ((tray, (255, 0, 255)), (block, (0, 255, 0))):
            if c is not None:
                cnt, _ = cv2.findContours(c["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cv2.drawContours(vis, cnt, -1, col, 1)
        self.show("scene_detect", vis)
        table_c = float(pz(pl, -300, 0))
        print("SCENE table plane z(-300,0) = %.1f" % table_c)
        return pl, tray, block

    # ------------------------------------------------------------------ wrist camera looks
    def cam_offset(self, cdir):
        c = np.array(cdir, float)
        a = np.array(DOWN)
        return CAM_SIDE * np.cross(c, a) + CAM_ALONG * c

    def look_at(self, x, y, z_tcp, z_target):
        """move so the wrist camera is above (x, y); look; return frame"""
        for cdir in ((0, -1, 0), (0, 1, 0), (1, 0, 0), (-1, 0, 0)):
            off = self.cam_offset(cdir)
            tcp = np.array([x - off[0], y - off[1], z_tcp])
            tcp[0] = min(max(tcp[0], -700), 90)
            res = self.move(tcp, cdir, tag="look")
            if res.get("moved"):
                break
        W = self.r.look(["left_wrist"])["left_wrist"]
        uv, _ = W.project(np.array([[x, y, z_target]], float))
        u, v = uv[0]
        if not (170 < u < 600 and 50 < v < 430):
            st = self.r.state(A)
            e = np.array(W.eye, float)
            shift = np.array([x - e[0], y - e[1]])
            tcp = np.array(st["tcp_mm"], float) + np.r_[shift, 0.0]
            print("LOOK recentre by", np.round(shift, 1))
            self.r.move(A, tcp.tolist(), DOWN, st["closing_dir"], False)
            W = self.r.look(["left_wrist"])["left_wrist"]
        return W

    def wrist_tray(self, W, rough_c, rough_l):
        P = W.points()
        X, Y, Z = P[..., 0], P[..., 1], P[..., 2]
        fin = np.isfinite(Z)
        with np.errstate(invalid="ignore"):
            d = np.hypot(X - rough_c[0], Y - rough_c[1])
            Rout = 0.5 * rough_l + 50
            tab = fin & (d > Rout)
        pts = P[tab]
        pl = None
        if len(pts) > 400:
            lowz = np.percentile(pts[:, 2], 60)
            pl = plane_ransac(pts[pts[:, 2] < lowz + 3][:: max(1, len(pts) // 8000)], self.rng, tol=3.0)
        if pl is None:
            return None, None
        H = Z - pz(pl, X, Y)
        with np.errstate(invalid="ignore"):
            wall = fin & (H > 8) & (H < 120) & (d < Rout + 60)
        k = cv2.morphologyEx(wall.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        best = None
        for m, stt in comps(k, 100):
            m = m & wall
            if m.sum() < 50:
                continue
            c, s, l, dsh = rect_info(P[m][:, :2])
            if l < 90:
                continue
            if best is None or l > best[2]:
                best = (m, c, l, s, dsh, stt)
        if best is None:
            return None, pl
        m, c, l, s, dsh, stt = best
        dl = np.array([-dsh[1], dsh[0]])
        rx = (X - c[0]) * dsh[0] + (Y - c[1]) * dsh[1]
        ry = (X - c[0]) * dl[0] + (Y - c[1]) * dl[1]
        with np.errstate(invalid="ignore"):
            inner = fin & (np.abs(rx) < s / 2 - 20) & (np.abs(ry) < l / 2 - 20) & (H < 8) & (H > -5)
        floor_h = float(np.median(H[inner])) if inner.sum() > 50 else 3.0
        # the flat floor's own extent (smaller than the rim's when the walls are flared)
        with np.errstate(invalid="ignore"):
            flm = fin & (np.abs(rx) < s / 2) & (np.abs(ry) < l / 2) & (np.abs(H - floor_h) < 2.5)
        if flm.sum() > 300:
            in_s = min(float(np.percentile(np.abs(rx[flm]), 97)), s / 2 - 4)
            in_l = min(float(np.percentile(np.abs(ry[flm]), 97)), l / 2 - 4)
        else:
            in_s, in_l = s / 2 - 12, l / 2 - 12
        rim_h = float(np.percentile(H[m], 90))
        x0, y0, w0, h0 = stt[0], stt[1], stt[2], stt[3]
        border = bool(x0 < 115 or y0 < 3 or x0 + w0 > 636 or y0 + h0 > 476)
        tz = float(pz(pl, c[0], c[1]))
        out = dict(c=c, s=s, l=l, dsh=dsh, dl=dl, table_z=tz, floor_z=tz + floor_h, rim_z=tz + rim_h,
                   floor_h=floor_h, rim_h=rim_h, border=border, in_s=in_s, in_l=in_l)
        print("TRAY floor half-extents %.0f x %.0f" % (in_s, in_l))
        print("TRAY c %s s %.0f l %.0f short-dir %s floor %.1f (h %.1f) rim %.1f (h %.1f) table %.1f border %s"
              % (np.round(c, 1), s, l, np.round(dsh, 2), out["floor_z"], floor_h, out["rim_z"], rim_h, tz, border))
        vis = W.rgb.copy()
        vis[m] = (0.5 * vis[m] + [0, 127, 0]).astype(np.uint8)
        self.show("wrist_tray", vis)
        return out, pl

    def wrist_block(self, W, rough_xy, avoid=None):
        P = W.points()
        X, Y, Z = P[..., 0], P[..., 1], P[..., 2]
        fin = np.isfinite(Z)
        with np.errstate(invalid="ignore"):
            d = np.hypot(X - rough_xy[0], Y - rough_xy[1])
            ring = fin & (d > 70) & (d < 230)
        if ring.sum() < 300:
            return None
        zr = Z[ring]
        sel = ring & (Z < np.percentile(zr, 50) + 2)
        pts = P[sel]
        pl = plane_ransac(pts[:: max(1, len(pts) // 6000)], self.rng, tol=3.0)
        if pl is None:
            return None
        H = Z - pz(pl, X, Y)
        with np.errstate(invalid="ignore"):
            m = fin & (H > 7) & (H < 140) & (d < 170)
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        zc = float(np.nanmedian(Z[ring]))
        pix_mm = max(float(W.eye[2]) - zc, 100.0) / W.fx
        cands = []
        for cm, stt in comps(m, 40):
            top = float(np.percentile(H[cm], 92))
            topm = cm & (H > top - 6)
            if topm.sum() < 25:
                continue
            xy = P[topm][:, :2]
            c, s, l, dsh = rect_info(xy)
            fill = topm.sum() * pix_mm ** 2 / max(s * l, 1.0)
            ok = 10 <= s <= 85 and l <= 125 and top >= 12 and fill > 0.45 and not self.in_tray(c, 5)
            dist = float(np.linalg.norm(c - rough_xy))
            print("  blob c %s s %.0f l %.0f top %.1f fill %.2f dist %.0f %s" % (np.round(c), s, l, top, fill, dist, "ok" if ok else "-"))
            if ok:
                cands.append((dist, c, s, l, dsh, top, topm, cm))
        if not cands:
            return None
        cands.sort(key=lambda q: q[0])
        dist, c, s, l, dsh, top, topm, cm = cands[0]
        tz = float(pz(pl, c[0], c[1]))
        top_z = float(np.median(Z[topm]))
        s_c, l_c = max(s - 3.0, 8.0), max(l - 3.0, 8.0)
        out = dict(c=c, s=s_c, l=l_c, dsh=dsh, dl=np.array([-dsh[1], dsh[0]]), top_z=top_z, table_z=tz, h=top_z - tz)
        print("BLOCK c %s size %.1f x %.1f h %.1f top %.1f table %.1f short-dir %s"
              % (np.round(c, 1), s_c, l_c, top_z - tz, top_z, tz, np.round(dsh, 2)))
        vis = W.rgb.copy()
        vis[cm] = (0.5 * vis[cm] + [127, 0, 0]).astype(np.uint8)
        vis[topm] = (0.5 * vis[topm] + [0, 0, 127]).astype(np.uint8)
        self.show("wrist_block", vis)
        return out

    # ------------------------------------------------------------------ main
    def run(self):
        r = self.r
        self.tray = None
        pl_s, tray_s = self.scene_detect()
        if tray_s is None:
            print("FAIL: no tray in the scene view")
            return
        # 1. tray from the wrist camera
        tray = None
        tc, tl = tray_s["c"], tray_s["l"]
        tz_s = float(pz(pl_s, tc[0], tc[1]))
        for k in range(3):
            W = self.look_at(tc[0], tc[1], max(FAST_Z, tz_s + 420), tz_s)
            tray, pl_t = self.wrist_tray(W, tc, tl)
            if tray is None:
                print("tray not found in wrist view", k)
                continue
            if not tray["border"] and np.linalg.norm(tray["c"] - tc) < 40:
                break
            tc, tl = tray["c"], tray["l"]
        if tray is None:
            print("FAIL: no tray")
            return
        self.tray = tray
        # 2. block: candidates from the scene camera, each confirmed by the wrist camera
        blk = None
        for bc in self.block_cands()[:4]:
            bxy = bc["med"]
            top_s = float(pz(pl_s, bxy[0], bxy[1])) + bc["h"]
            for k in range(3):
                W = self.look_at(bxy[0], bxy[1], max(FAST_Z, top_s + 200), top_s)
                b = self.wrist_block(W, bxy)
                if b is None:
                    print("block not found in wrist view", k)
                    break
                blk = b
                if np.linalg.norm(b["c"] - bxy) < 30:
                    break
                bxy = b["c"]
            if blk is not None:
                break
        if blk is None:
            print("FAIL: no block")
            return
        # 3. grasp
        held = None
        for attempt in range(3):
            held = self.grasp(blk)
            if held:
                break
            if r.time_s() > 200:
                break
            W = self.look_at(blk["c"][0], blk["c"][1], max(FAST_Z, blk["top_z"] + 200), blk["top_z"])
            nb = self.wrist_block(W, blk["c"])
            if nb is not None:
                blk = nb
        if not held:
            print("FAIL: grasp")
            r.set_gripper(A, 80)
            return
        # 4. place in the tray
        if not self.place(held, tray):
            print("FAIL: no reachable place in the tray")
            self.put_back(held)

    def grasp(self, blk):
        r = self.r
        c, s, l = blk["c"], blk["s"], blk["l"]
        options = [(blk["dsh"], s, l)]
        if l <= 72:
            options.append((blk["dl"], l, s))
        h = blk["h"]
        gz = max(blk["top_z"] - min(0.5 * h, 22.0), blk["table_z"] + 19.0, 13.0)
        for dvec, w, other in options:
            cdir = (dvec[0], dvec[1], 0.0)
            r.set_gripper(A, min(80.0, w + 26.0))
            pre = (c[0], c[1], blk["top_z"] + 45.0)
            res = self.move(pre, cdir, tag="pregrasp")
            if not res.get("moved") or not res.get("done"):
                continue
            res = self.move((c[0], c[1], gz), cdir, contact=True, tag="descend")
            st = r.state(A)
            if res.get("why") == "contact" and st["tcp_mm"][2] > gz + 8:
                print("GRASP: touched something %.1f mm above the grasp height" % (st["tcp_mm"][2] - gz))
                self.move(pre, cdir, tag="up")
                return None
            op = r.set_gripper(A, 0)
            print("GRASP closed: opening %.1f (expected %.1f) tcp z %.1f target %.1f" % (op, w, st["tcp_mm"][2], gz))
            if op < max(4.0, 0.5 * w):
                r.set_gripper(A, min(80.0, w + 26.0))
                self.move(pre, cdir, tag="up")
                return None
            st = r.state(A)
            hold_below = st["tcp_mm"][2] - blk["table_z"]
            lift = (c[0], c[1], max(FAST_Z, blk["top_z"] + 120))
            self.move(lift, cdir, tag="lift")
            op2 = r.state(A)["opening_mm"]
            print("LIFT: opening %.1f force %s" % (op2, np.round(r.state(A)["force_n"], 1).tolist()))
            if op2 < 3:
                r.set_gripper(A, 80)
                return None
            return dict(w=op, cdir=cdir, hold_below=hold_below, h=h, other=other, pick=((c[0], c[1]), gz, cdir))
        return None

    def place(self, held, tray):
        """lower the block onto the tray floor at a reachable point inside the tray; False if none is reachable"""
        r = self.r
        hb = held["hold_below"]
        c = tray["c"]
        hs, hl = tray["in_s"] - 2, tray["in_l"] - 2           # the floor's half sizes
        tow = np.array([-280.0, 20.0]) - c                   # towards where the arm reaches easily
        tow = tow / max(np.linalg.norm(tow), 1e-6)
        z_touch = tray["floor_z"] + hb
        heights = [FAST_Z, max(tray["rim_z"] + hb + 25, z_touch + 30)]
        for cd, along, across in ((tray["dl"], hl, hs), (tray["dsh"], hs, hl)):
            mu = along - held["w"] / 2 - 35                   # room along the closing dir (fingers)
            mv = across - held["other"] / 2 - 15
            if mu < -5 or mv < -5:
                continue
            mu, mv = max(mu, 0.0), max(mv, 0.0)
            pv = np.array([-cd[1], cd[0]])
            cands = []
            for fu in (0.0, 0.5, 1.0):
                for fv in (0.0, 0.5, 1.0):
                    for su in (-1, 1):
                        for sv in (-1, 1):
                            o = su * fu * mu * np.asarray(cd) + sv * fv * mv * pv
                            sc = float(np.hypot(fu * mu, fv * mv) - 0.7 * (o @ tow))
                            if all(np.linalg.norm(o - q[1]) > 1 for q in cands):
                                cands.append((sc, o))
            cands.sort(key=lambda q: q[0])
            cdir = (float(cd[0]), float(cd[1]), 0.0)
            for sc, o in cands[:9]:
                p = c + o
                zt = None
                for z in heights:
                    if self.move((p[0], p[1], z), cdir, tag="over tray").get("done"):
                        zt = z
                        break
                if zt is None:
                    continue
                if not self.move((p[0], p[1], max(z_touch + 20, 14.0)), cdir, tag="lower").get("done"):
                    self.move((p[0], p[1], zt), cdir, tag="back up")
                    continue
                print("PLACE at %s (offset %s from the tray centre)" % (np.round(p, 1), np.round(o, 1)))
                for k in range(3):
                    res = self.move((p[0], p[1], max(z_touch - 10, 13.5)), cdir, contact=True, tag="to floor")
                    st = r.state(A)
                    print("PLACE: stopped at %.1f, expected contact at %.1f, why %s force %s"
                          % (st["tcp_mm"][2], z_touch, res.get("why"), np.round(st["force_n"], 1).tolist()))
                    if res.get("why") != "contact" or st["tcp_mm"][2] < z_touch + 12:
                        break
                r.set_gripper(A, min(80.0, held["w"] + 22.0))
                st = r.state(A)
                bottom = st["tcp_mm"][2] - hb
                z1 = max(bottom + held["h"] + 45, tray["rim_z"] + 45)
                x, y = st["tcp_mm"][0], st["tcp_mm"][1]
                if self.move((x, y, z1), cdir, tag="retreat").get("done"):
                    self.move((x, y, max(FAST_Z, z1)), cdir, tag="retreat2")
                return True
        return False

    def put_back(self, held):
        """the tray cannot be reached: set the block down where it was picked up"""
        r = self.r
        (x, y), gz, cdir = held["pick"]
        print("PUT BACK at", round(x), round(y))
        self.move((x, y, gz + held["h"] + 40), cdir, tag="back over pick")
        self.move((x, y, gz - 3), cdir, contact=True, tag="back down")
        r.set_gripper(A, min(80.0, held["w"] + 22.0))
        st = r.state(A)
        self.move((st["tcp_mm"][0], st["tcp_mm"][1], gz + held["h"] + 40), cdir, tag="back up")

    def place_old(self, held, tray):
        r = self.r
        c = tray["c"]
        # closing dir along the tray's long axis (more room for the fingers)
        cdir = (tray["dl"][0], tray["dl"][1], 0.0)
        hb = held["hold_below"]
        above = max(FAST_Z, tray["rim_z"] + hb + 40)
        res = self.move((c[0], c[1], above), cdir, tag="over tray")
        if not res.get("done"):
            cdir = (tray["dsh"][0], tray["dsh"][1], 0.0)
            res = self.move((c[0], c[1], above), cdir, tag="over tray 2")
        z_touch = tray["floor_z"] + hb
        self.move((c[0], c[1], max(z_touch + 20, 14.0)), cdir, tag="lower")
        for k in range(3):
            res = self.move((c[0], c[1], max(z_touch - 10, 13.5)), cdir, contact=True, tag="to floor")
            st = r.state(A)
            print("PLACE: stopped at %.1f, expected contact at %.1f, why %s force %s"
                  % (st["tcp_mm"][2], z_touch, res.get("why"), np.round(st["force_n"], 1).tolist()))
            if res.get("why") != "contact" or st["tcp_mm"][2] < z_touch + 12:
                break
        r.set_gripper(A, min(80.0, held["w"] + 22.0))
        st = r.state(A)
        bottom = st["tcp_mm"][2] - hb
        z1 = max(bottom + held["h"] + 45, tray["rim_z"] + 45)
        x, y = st["tcp_mm"][0], st["tcp_mm"][1]
        res = self.move((x, y, z1), cdir, tag="retreat")
        if res.get("done"):
            self.move((x, y, above), cdir, tag="retreat2")


def run(robot, task):
    print("TASK:", task[:80])
    sk = Skill(robot)
    try:
        sk.run()
    finally:
        try:
            robot.set_gripper(A, 80)
            res = robot.home(A)
            if not res.get("done"):
                st = robot.state(A)
                robot.move(A, [st["tcp_mm"][0], st["tcp_mm"][1], 350], DOWN, st["closing_dir"], False)
                res = robot.home(A)
            print("HOME", res, "t = %.1f s" % robot.time_s())
        except Exception as e:
            print("end error", e)
