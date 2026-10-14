"""Phase 4: automatic placement and routing of a PcbDoc whose components came in through the ECO.

  plan = make_plan(prj, spec)     placement (constructive + improvement), 2-layer grid routing with negotiated
                                  congestion (PathFinder over a numba A*), optional bottom pour for one net
  verify(plan, ...)               copper clearance + connectivity of the planned board, before touching Altium
  apply(prj, plan)                one DelphiScript run: move components, add tracks/vias/polygon, repour, save

The result is a working first layout, not a hand-optimised one: review it in Altium and fix what you don't like.
All units are mil.
"""
import heapq
import json
import math
import os

import numpy as np
from numba import njit
from scipy.ndimage import distance_transform_edt as edt
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from . import altium
from .pcbfile import BOT, MULTI, NO_NET, PCB, TOP

LAYERS = (TOP, BOT)          # grid layer 0 = top, 1 = bottom


# ------------------------------------------------------------------------------------------------ footprints
def footprints(pcb):
    """{des: dict(pads=[local pads], court=(x0,y0,x1,y1) local, x, y, rot, layer)} with pads in component space."""
    fps = {}
    for ci, c in enumerate(pcb.comps):
        des = pcb.names[ci]
        cx, cy, rot = float(c["X"][:-3]), float(c["Y"][:-3]), float(c.get("ROTATION", 0))
        a = math.radians(-rot)
        pads = []
        for q in pcb.pads:
            if q["comp"] != ci:
                continue
            dx, dy = q["x"] - cx, q["y"] - cy
            lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            pads.append(dict(name=q["name"], x=lx, y=ly, sx=q["sx"], sy=q["sy"], shape=q["shape"], rot=(q["rot"] - rot) % 360,
                             layer=q["layer"], hole=q["hole"], net=pcb.net(q["net"])))
        pts = []
        for p in pads:
            r = max(p["sx"], p["sy"]) / 2
            pts += [(p["x"] - r, p["y"] - r), (p["x"] + r, p["y"] + r)]
        for t in pcb.tracks:
            if t["comp"] == ci and t["layer"] in (33, 34):   # silkscreen outline of the part
                for x, y in ((t["x1"], t["y1"]), (t["x2"], t["y2"])):
                    dx, dy = x - cx, y - cy
                    pts.append((dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)))
        xs, ys = [p[0] for p in pts] or [0], [p[1] for p in pts] or [0]
        fps[des] = dict(pads=pads, court=(min(xs), min(ys), max(xs), max(ys)), x=cx, y=cy, rot=rot, layer=c.get("LAYER", "TOP"))
    return fps


def _rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def placed_pads(fp, x, y, rot):
    out = []
    for p in fp["pads"]:
        px, py = _rot(p["x"], p["y"], rot)
        out.append(dict(p, x=x + px, y=y + py, rot=(p["rot"] + rot) % 360))
    return out


def court_box(fp, x, y, rot, margin):
    x0, y0, x1, y1 = fp["court"]
    pts = [_rot(px, py, rot) for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = [x + p[0] for p in pts], [y + p[1] for p in pts]
    return box(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


# ------------------------------------------------------------------------------------------------ placement
def place(fps, board_poly, hints, nets_of, step=25, margin=15, edge=40, log=print):
    """Constructive placement: fixed hints first, then parts in order of connectivity at the position/rotation that
    adds the least half-perimeter wirelength; then a few passes of single-part moves to improve it."""
    pos = {}
    inner = board_poly.buffer(-edge)
    for des, h in hints.items():
        if des in fps:
            pos[des] = (float(h["x"]), float(h["y"]), float(h.get("rot", 0)))
    deg = {d: sum(len(nets_of.get(n, [])) for n in {p["net"] for p in f["pads"] if p["net"]}) for d, f in fps.items()}
    todo = sorted([d for d in fps if d not in pos], key=lambda d: (-deg[d], d))
    xmin, ymin, xmax, ymax = inner.bounds

    def pins_of(d, x, y, r):
        return [(p["net"], p["x"], p["y"]) for p in placed_pads(fps[d], x, y, r) if p["net"]]

    def hpwl(netpins):
        tot = 0.0
        for n, pts in netpins.items():
            if len(pts) > 1:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                tot += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return tot

    def netpins_from(pos):
        npins = {}
        for d, (x, y, r) in pos.items():
            for n, px, py in pins_of(d, x, y, r):
                npins.setdefault(n, []).append((px, py))
        return npins

    boxes = {d: court_box(fps[d], *pos[d], margin) for d in pos}
    cx, cy = inner.centroid.x, inner.centroid.y
    xs = np.arange(math.ceil(xmin / step) * step, xmax + 1, step)
    ys = np.arange(math.ceil(ymin / step) * step, ymax + 1, step)
    for d in todo:
        base = netpins_from(pos)
        best = None
        coarse = 2 if len(xs) * len(ys) > 2500 else 1
        for r in (0, 90, 180, 270):
            for x in xs[::coarse]:
                for y in ys[::coarse]:
                    b = court_box(fps[d], x, y, r, margin)
                    if not inner.buffer(edge - 10).contains(b):
                        continue
                    if any(b.intersects(o) for o in boxes.values()):
                        continue
                    npins = {k: list(v) for k, v in base.items()}
                    for n, px, py in pins_of(d, x, y, r):
                        npins.setdefault(n, []).append((px, py))
                    cost = hpwl(npins) + 0.05 * (abs(x - cx) + abs(y - cy))
                    if best is None or cost < best[0]:
                        best = (cost, x, y, r)
        if best is None:
            raise RuntimeError("no room on the board for %s: make the board larger" % d)
        pos[d] = (best[1], best[2], best[3])
        boxes[d] = court_box(fps[d], *pos[d], margin)
    # improvement: move single parts to nearby better spots
    for it in range(3):
        moved = 0
        for d in todo:
            cur = hpwl(netpins_from(pos))
            x0, y0, r0 = pos[d]
            others = {k: v for k, v in boxes.items() if k != d}
            best = (cur, x0, y0, r0)
            for r in (0, 90, 180, 270):
                for dx in range(-8, 9):
                    for dy in range(-8, 9):
                        x, y = x0 + dx * step, y0 + dy * step
                        b = court_box(fps[d], x, y, r, margin)
                        if not inner.buffer(edge - 10).contains(b) or any(b.intersects(o) for o in others.values()):
                            continue
                        pos[d] = (x, y, r)
                        c = hpwl(netpins_from(pos))
                        if c < best[0] - 1:
                            best = (c, x, y, r)
                        pos[d] = (x0, y0, r0)
            if best[1:] != (x0, y0, r0):
                pos[d] = best[1:]
                boxes[d] = court_box(fps[d], *pos[d], margin)
                moved += 1
        log("  placement pass %d: moved %d parts, wirelength %.0f mil" % (it + 1, moved, hpwl(netpins_from(pos))))
        if not moved:
            break
    return pos


# ------------------------------------------------------------------------------------------------ A* (numba)
DX = np.array([1, 1, 0, -1, -1, -1, 0, 1], np.int64)
DY = np.array([0, 1, 1, 1, 0, -1, -1, -1], np.int64)


@njit(cache=True)
def astar(allow, viaok, src, tgt, tx, ty, G, viacost, b45, b90, botf, maxexp, extra, viaextra):
    NL, NY, NX = allow.shape
    ND = 9
    N = NL * NY * NX * ND
    g = np.full(N, 1e18)
    par = np.full(N, -1, np.int64)
    h = [(0.0, np.int64(0))]
    h.pop()
    sq2 = 1.4142135623730951
    for L in range(NL):
        for j in range(NY):
            for i in range(NX):
                if src[L, j, i] and allow[L, j, i]:
                    s = ((L * NY + j) * NX + i) * ND + 8
                    g[s] = 0.0
                    dx = abs(i - tx)
                    dy = abs(j - ty)
                    heapq.heappush(h, (G * (max(dx, dy) + (sq2 - 1) * min(dx, dy)), s))
    exp = 0
    goal = -1
    while len(h) > 0:
        f, s = heapq.heappop(h)
        d = s % ND
        c = s // ND
        i = c % NX
        c //= NX
        j = c % NY
        L = c // NY
        gs = g[s]
        dx = abs(i - tx)
        dy = abs(j - ty)
        hh = G * (max(dx, dy) + (sq2 - 1) * min(dx, dy))
        if f > gs + hh + 1e-6:
            continue
        if tgt[L, j, i]:
            goal = s
            break
        exp += 1
        if exp > maxexp:
            break
        lf = botf if L == 1 else 1.0
        for nd in range(8):
            if d < 8:
                t = abs(nd - d)
                t = min(t, 8 - t)
                if t > 2:
                    continue
                pen = 0.0 if t == 0 else (b45 if t == 1 else b90)
            else:
                pen = 0.0
            ni = i + DX[nd]
            nj = j + DY[nd]
            if ni < 0 or nj < 0 or ni >= NX or nj >= NY:
                continue
            if not allow[L, nj, ni]:
                continue
            if nd % 2 == 1:
                if not (allow[L, j, ni] and allow[L, nj, i]):
                    continue
                step = G * sq2
            else:
                step = G
            ng = gs + step * lf + pen + extra[L, nj, ni]
            ns = ((L * NY + nj) * NX + ni) * ND + nd
            if ng < g[ns] - 1e-9:
                g[ns] = ng
                par[ns] = s
                dx = abs(ni - tx)
                dy = abs(nj - ty)
                heapq.heappush(h, (ng + G * (max(dx, dy) + (sq2 - 1) * min(dx, dy)), ns))
        if NL == 2 and viaok[j, i]:
            ns = (((1 - L) * NY + j) * NX + i) * ND + 8
            ng = gs + viacost + viaextra[j, i]
            if ng < g[ns] - 1e-9:
                g[ns] = ng
                par[ns] = s
                heapq.heappush(h, (ng + hh, ns))
    if goal < 0:
        return np.zeros((0, 3), np.int64), exp
    path = []
    s = goal
    while s >= 0:
        c = s // ND
        i = c % NX
        c //= NX
        j = c % NY
        L = c // NY
        path.append((L, j, i))
        s = par[s]
    out = np.zeros((len(path), 3), np.int64)
    for k in range(len(path)):
        out[k, 0] = path[len(path) - 1 - k][0]
        out[k, 1] = path[len(path) - 1 - k][1]
        out[k, 2] = path[len(path) - 1 - k][2]
    return out, exp


def wastar(allow, viaok, src, tgt, ti, tj, G, viacost, extra, viaextra, margin=80, maxexp=3_000_000, b45=2.0, b90=8.0, botf=1.3):
    """A* restricted to the bounding box of source+target cells plus a margin (falls back to a wider box)."""
    NY, NX = allow.shape[1:]
    m2 = (src.any(0) | tgt.any(0))
    jj, ii = np.nonzero(m2)
    if len(jj) == 0:
        return np.zeros((0, 3), np.int64)
    for mg in (margin, margin * 3, max(NY, NX)):
        j0, j1 = max(jj.min() - mg, 0), min(jj.max() + mg, NY - 1)
        i0, i1 = max(ii.min() - mg, 0), min(ii.max() + mg, NX - 1)
        sl = (slice(None), slice(j0, j1 + 1), slice(i0, i1 + 1))
        path, _ = astar(np.ascontiguousarray(allow[sl]), np.ascontiguousarray(viaok[j0:j1 + 1, i0:i1 + 1]),
                        np.ascontiguousarray(src[sl]), np.ascontiguousarray(tgt[sl]), ti - i0, tj - j0, float(G), viacost,
                        b45, b90, botf, maxexp, np.ascontiguousarray(extra[sl]), np.ascontiguousarray(viaextra[j0:j1 + 1, i0:i1 + 1]))
        if len(path):
            path[:, 1] += j0
            path[:, 2] += i0
            return path
        if j0 == 0 and i0 == 0 and j1 == NY - 1 and i1 == NX - 1:
            break
    return np.zeros((0, 3), np.int64)


# ------------------------------------------------------------------------------------------------ grid helpers
class Grid:
    def __init__(self, poly, G):
        self.G = G
        x0, y0, x1, y1 = poly.bounds
        self.x0, self.y0 = math.floor(x0 / G) * G, math.floor(y0 / G) * G
        self.NX = int(math.ceil((x1 - self.x0) / G)) + 1
        self.NY = int(math.ceil((y1 - self.y0) / G)) + 1
        self.X = self.x0 + np.arange(self.NX) * G
        self.Y = self.y0 + np.arange(self.NY) * G

    def ij(self, x, y):
        return int(round((y - self.y0) / self.G)), int(round((x - self.x0) / self.G))

    def xy(self, j, i):
        return self.x0 + i * self.G, self.y0 + j * self.G

    def mask_geom(self, g, grow=0.0):
        """Boolean mask of cells whose centre is within `grow` of geometry g."""
        m = np.zeros((self.NY, self.NX), bool)
        if g.is_empty:
            return m
        x0, y0, x1, y1 = g.bounds
        j0, i0 = self.ij(x0 - grow, y0 - grow)
        j1, i1 = self.ij(x1 + grow, y1 + grow)
        j0, i0, j1, i1 = max(j0 - 1, 0), max(i0 - 1, 0), min(j1 + 1, self.NY - 1), min(i1 + 1, self.NX - 1)
        if j1 < j0 or i1 < i0:
            return m
        from shapely import contains_xy, distance
        xx, yy = np.meshgrid(self.X[i0:i1 + 1], self.Y[j0:j1 + 1])
        if grow <= 0:
            m[j0:j1 + 1, i0:i1 + 1] = contains_xy(g, xx, yy)
        else:
            from shapely import points
            m[j0:j1 + 1, i0:i1 + 1] = distance(g, points(xx, yy)) <= grow
        return m


def pad_geom(p):
    w, h = p["sx"], p["sy"]
    if p["shape"] == 1:
        r = min(w, h) / 2
        g = Point(0, 0).buffer(r, 16) if abs(w - h) < 0.01 else (
            LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)]) if w > h else LineString([(0, -(h / 2 - r)), (0, h / 2 - r)])).buffer(r, 16)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    return affinity.translate(affinity.rotate(g, p["rot"], origin=(0, 0)), p["x"], p["y"])


def pad_layers(p):
    return (0, 1) if p["layer"] == MULTI or p["hole"] > 0 else ((0,) if p["layer"] == TOP else (1,))


# ------------------------------------------------------------------------------------------------ routing
def route(board_poly, pads, widths, clearance=10, G=5, via_d=24, via_h=12, edge=20, pour=None, iters=40, log=print):
    """pads: [dict(net, x, y, sx, sy, shape, rot, layer, hole, des, name)]. Returns dict(tracks, vias, failed, overlaps)."""
    grid = Grid(board_poly, G)
    NY, NX = grid.NY, grid.NX
    nets = sorted({p["net"] for p in pads if p["net"]})
    pour_net = pour["net"] if pour else None
    pour_layer = (1 if pour.get("layer", "bottom") == "bottom" else 0) if pour else None
    w_of = lambda n: float(widths.get(n, 10))
    geoms = [pad_geom(p) for p in pads]
    # rasterise once: which pad covers each cell, per layer; distances come from distance transforms (fast)
    padnet = np.full((2, NY, NX), -1, np.int32)
    net_id = {n: i for i, n in enumerate(nets)}
    for k, p in enumerate(pads):
        m = grid.mask_geom(geoms[k])
        if not m.any():
            j, i = grid.ij(p["x"], p["y"])
            m[min(max(j, 0), NY - 1), min(max(i, 0), NX - 1)] = True
        for L in pad_layers(p):
            padnet[L][m] = net_id.get(p["net"], -2)
    inside_board = grid.mask_geom(board_poly)
    d_edge = edt(inside_board) * G                       # distance of a cell centre to the board edge
    slack = 1.0 * G                                       # rasterisation error margin

    def hard_allow(n, w):
        allow = np.zeros((2, NY, NX), bool)
        r = clearance + w / 2 + slack
        inner = d_edge >= edge + w / 2
        nid = net_id[n]
        for L in (0, 1):
            other = (padnet[L] != -1) & (padnet[L] != nid)
            allow[L] = inner & (edt(~other) * G >= r) if other.any() else inner
        return allow

    def via_allow(n):
        r = clearance + via_d / 2 + slack
        ok = d_edge >= edge + via_d / 2
        nid = net_id[n]
        for L in (0, 1):
            other = (padnet[L] != -1) & (padnet[L] != nid)
            if other.any():
                ok &= edt(~other) * G >= r
        own = np.zeros((NY, NX), bool)
        for k, p in enumerate(pads):              # no via inside an SMD pad of the same net
            if p["net"] == n and p["hole"] <= 0:
                own |= grid.mask_geom(geoms[k], via_d / 2)
        return ok & ~own

    def targets(k):
        p = pads[k]
        t = np.zeros((2, NY, NX), bool)
        m = grid.mask_geom(geoms[k].buffer(-min(p["sx"], p["sy"]) * 0.2))
        if not m.any():
            j, i = grid.ij(p["x"], p["y"])
            m[min(max(j, 0), NY - 1), min(max(i, 0), NX - 1)] = True
        for L in pad_layers(p):
            t[L] |= m
        return t

    routed_nets = [n for n in nets if n != pour_net]
    hard = {}
    for n in nets:
        w = w_of(n)
        A = hard_allow(n, w)
        for k, p in enumerate(pads):        # own pads are always enterable
            if p["net"] == n:
                A |= targets(k)
        hard[n] = (A, via_allow(n))
    tgt_of = {k: targets(k) for k in range(len(pads))}

    geom = {n: ([], []) for n in nets}       # net -> (segments [(L,x1,y1,x2,y2,w)], vias [(x,y)])
    occ = {}                                 # net -> bool (2,NY,NX) copper footprint grown by clearance/2
    hist = np.zeros((2, NY, NX))
    count = np.zeros((2, NY, NX), np.int16)

    def occupancy(n):
        segs, vias = geom[n]
        o = np.zeros((2, NY, NX), bool)
        for (L, x1, y1, x2, y2, w) in segs:
            g = LineString([(x1, y1), (x2, y2)]) if (x1, y1) != (x2, y2) else Point(x1, y1)
            o[L] |= grid.mask_geom(g, w / 2 + clearance / 2 - 0.6)
        for (x, y) in vias:
            m = grid.mask_geom(Point(x, y), via_d / 2 + clearance / 2 - 0.6)
            o[0] |= m
            o[1] |= m
        return o

    def path_to_geom(path, n, w, kstart, kend):
        segs, vias = [], []
        pts = [(int(L), grid.xy(j, i)) for L, j, i in path]
        # compress into straight runs per layer
        run = [pts[0]]
        for a in pts[1:]:
            if a[0] != run[-1][0]:
                vias.append(run[-1][1])
                if len(run) > 1:
                    segs += _runs(run, w)
                run = [a]
            else:
                run.append(a)
        if len(run) > 1:
            segs += _runs(run, w)
        # stubs into the pad centres so the copper really lands on the pads
        for k, end in ((kstart, pts[0]), (kend, pts[-1])):
            if k is None:
                continue
            p = pads[k]
            if (p["x"], p["y"]) != end[1] and end[0] in pad_layers(p):
                segs.append((end[0], end[1][0], end[1][1], p["x"], p["y"], min(w, p["sx"], p["sy"])))
        return segs, vias

    def route_net(n, pres):
        w = w_of(n)
        A, V = hard[n]
        if n in occ:
            count[:] = count - occ[n]
        # a centre-line cell conflicts when this net's copper band (w/2 + c/2) would touch another net's band
        foreign = count > 0
        unit = (G * 4.0) * pres
        extra = hist * G * 0.5
        vex = np.zeros((NY, NX))
        for L in (0, 1):
            if foreign[L].any():
                d = edt(~foreign[L]) * G
                extra[L] = extra[L] + np.where(d < w / 2 + clearance / 2 + 1.0 * G, unit * (1 + hist[L]), 0.0)
                vex = vex + np.where(d < via_d / 2 + clearance / 2 + 1.0 * G, unit * 3 * (1 + hist[L]), 0.0)
        ks = [k for k, p in enumerate(pads) if p["net"] == n]
        geom[n] = ([], [])
        if len(ks) < 2:
            occ[n] = np.zeros((2, NY, NX), bool)
            return True
        tree = tgt_of[ks[0]].copy()
        left = ks[1:]
        ok = True
        tree_pad = {ks[0]}
        while left:
            tg = np.zeros((2, NY, NX), bool)
            for k in left:
                tg |= tgt_of[k]
            cx = np.mean([pads[k]["x"] for k in tree_pad])
            cy = np.mean([pads[k]["y"] for k in tree_pad])
            kn = min(left, key=lambda k: abs(pads[k]["x"] - cx) + abs(pads[k]["y"] - cy))
            tj, ti = grid.ij(pads[kn]["x"], pads[kn]["y"])
            path = wastar(A, V, tree, tg, ti, tj, G, 30.0 * G / 5, extra, vex)
            if len(path) == 0:
                ok = False
                break
            L, j, i = path[-1]
            hit = next((k for k in left if tgt_of[k][L, j, i]), left[0])
            start = next((k for k in tree_pad if tgt_of[k][path[0][0], path[0][1], path[0][2]]), None)
            s, v = path_to_geom(path, n, w, start, hit)
            geom[n][0].extend(s)
            geom[n][1].extend(v)
            for L2, j2, i2 in path:
                tree[L2, j2, i2] = True
            tree |= tgt_of[hit]
            tree_pad.add(hit)
            left.remove(hit)
        occ[n] = occupancy(n)
        count[:] = count + occ[n]
        return ok

    # pour net: every SMD pad on the other side gets a short stub + via into the pour; TH pads join the pour directly
    stitch = []
    if pour_net:
        V = hard[pour_net][1]
        A = hard[pour_net][0]
        for k, p in enumerate(pads):
            if p["net"] != pour_net or pour_layer in pad_layers(p):
                continue
            L = pad_layers(p)[0]
            near = grid.mask_geom(Point(p["x"], p["y"]).buffer(120)) & V & A[L] & A[1 - L]
            if not near.any():
                log("  ! no room for a pour via next to %s-%s" % (p["des"], p["name"]))
                continue
            src = np.zeros((2, NY, NX), bool)
            src[L] = tgt_of[k][L]
            tg = np.zeros((2, NY, NX), bool)
            tg[L] = near
            j0, i0 = grid.ij(p["x"], p["y"])
            only = np.zeros((2, NY, NX), bool)
            only[L] = A[L]
            path = wastar(only, np.zeros((NY, NX), bool), src, tg, i0, j0, G, 1e9, np.zeros((2, NY, NX)), np.zeros((NY, NX)),
                          margin=40, maxexp=400_000, botf=1.0)
            if len(path) == 0:
                log("  ! cannot reach a pour via from %s-%s" % (p["des"], p["name"]))
                continue
            s, _ = path_to_geom(path, pour_net, w_of(pour_net), k, None)
            geom[pour_net][0].extend(s)
            geom[pour_net][1].append(grid.xy(path[-1][1], path[-1][2]))
            stitch.append((p["des"], p["name"]))
        occ[pour_net] = occupancy(pour_net)
        count[:] = count + occ[pour_net]

    order = sorted(routed_nets, key=lambda n: (-w_of(n), sum(1 for p in pads if p["net"] == n)))
    failed = []
    pres = 0.5
    todo = list(order)
    for it in range(iters):
        failed = [n for n in todo if not route_net(n, pres)]
        over = (count > 1)
        n_over = int(over.sum())
        log("  routing iteration %d: rerouted %d nets, %d overlap cells, %d nets unroutable" % (it + 1, len(todo), n_over, len(failed)))
        if n_over == 0 and not failed:
            break
        hist += over * 1.0
        pres *= 1.6
        # next round: only nets that touch an overlap, plus the ones that failed
        todo = [n for n in order if n in failed or (n in occ and (occ[n] & over).any())]
        if pour_net and (occ[pour_net] & over).any():
            log("  (overlaps with the fixed pour stitching: %d cells)" % int((occ[pour_net] & over).sum()))
    # exact geometric check of the result; push the router away from every real violation and re-route those nets
    from shapely.strtree import STRtree

    def exact_violations():
        items = []
        for n, (segs, vs) in geom.items():
            for (L, x1, y1, x2, y2, w) in segs:
                g = LineString([(x1, y1), (x2, y2)]) if (x1, y1) != (x2, y2) else Point(x1, y1)
                items.append((L, n, g.buffer(w / 2, 8)))
            for (x, y) in vs:
                for L in (0, 1):
                    items.append((L, n, Point(x, y).buffer(via_d / 2, 16)))
        for k, p in enumerate(pads):
            for L in pad_layers(p):
                items.append((L, p["net"], geoms[k]))
        tree = STRtree([it[2] for it in items])
        bad = []
        for i, a in enumerate(items):
            for j in tree.query(a[2].buffer(clearance)):
                j = int(j)
                b = items[j]
                if j <= i or a[0] != b[0] or a[1] == b[1]:
                    continue
                if a[2].distance(b[2]) < clearance - 0.05:
                    from shapely.ops import nearest_points
                    pa, pb = nearest_points(a[2], b[2])
                    bad.append((a[0], a[1], b[1], (pa.x + pb.x) / 2, (pa.y + pb.y) / 2))
        return bad

    for rep in range(6):
        bad = exact_violations()
        if not bad:
            break
        log("  repair %d: %d exact clearance violations" % (rep + 1, len(bad)))
        redo = set()
        for L, na, nb, x, y in bad:
            m = grid.mask_geom(Point(x, y), clearance * 2)
            hist[L][m] += 20
            for n in (na, nb):
                if n in routed_nets:
                    redo.add(n)
        for n in order:
            if n in redo:
                route_net(n, pres)
    tracks = [dict(net=n, layer=LAYERS[L], x1=x1, y1=y1, x2=x2, y2=y2, w=w) for n, (segs, _) in geom.items() for (L, x1, y1, x2, y2, w) in segs]
    vias = [dict(net=n, x=x, y=y, d=via_d, h=via_h) for n, (_, vs) in geom.items() for (x, y) in vs]
    return dict(tracks=tracks, vias=vias, failed=failed, overlap_cells=int((count > 1).sum()), stitched=stitch, grid=G)


def _runs(run, w):
    """Collapse a list of grid points on one layer into straight segments."""
    L = run[0][0]
    pts = [p[1] for p in run]
    segs = []
    a = pts[0]
    prev = pts[0]
    d0 = None
    for b in pts[1:]:
        d = (np.sign(round(b[0] - prev[0], 6)), np.sign(round(b[1] - prev[1], 6)))
        if d0 is not None and d != d0:
            segs.append((L, a[0], a[1], prev[0], prev[1], w))
            a = prev
        d0 = d
        prev = b
    segs.append((L, a[0], a[1], prev[0], prev[1], w))
    return segs


# ------------------------------------------------------------------------------------------------ planning
def make_plan(prj, spec, log=print):
    pcb = PCB(prj.pcbdoc)
    fps = footprints(pcb)
    outline = pcb.outline()
    b = spec.get("board", {})
    if b.get("width"):
        ox, oy = pcb.origin()
        board = box(ox, oy, ox + b["width"], oy + b["height"])
    elif len(outline) >= 3:
        board = Polygon(outline)
    else:
        raise SystemExit("no board size: give board.width/height in the spec")
    hints = {}
    ox, oy = board.bounds[0], board.bounds[1]
    for p in spec.get("parts", []):
        if p.get("pcb"):
            hints[p["des"]] = dict(x=ox + p["pcb"]["x"], y=oy + p["pcb"]["y"], rot=p["pcb"].get("rot", 0))
    nets_of = {}
    for d, f in fps.items():
        for p in f["pads"]:
            if p["net"]:
                nets_of.setdefault(p["net"], []).append(d)
    log("placing %d parts on a %.0f x %.0f mil board ..." % (len(fps), board.bounds[2] - board.bounds[0], board.bounds[3] - board.bounds[1]))
    pos = place(fps, board, hints, nets_of, log=log)
    pads = []
    for d, (x, y, r) in pos.items():
        for p in placed_pads(fps[d], x, y, r):
            pads.append(dict(p, des=d))
    widths = spec.get("net_widths", {})
    clr = spec.get("board", {}).get("clearance", 10)
    log("routing %d nets ..." % len({p["net"] for p in pads if p["net"]}))
    r = route(board, pads, widths, clearance=clr, pour=spec.get("pour"), log=log)
    plan = dict(board=list(board.exterior.coords), placement={d: dict(x=x, y=y, rot=rr) for d, (x, y, rr) in pos.items()},
                pads=pads, tracks=r["tracks"], vias=r["vias"], failed=r["failed"], overlap_cells=r["overlap_cells"],
                pour=spec.get("pour"), clearance=clr, stitched=r["stitched"])
    return plan


def verify(plan, log=print):
    """Copper clearance and connectivity of the planned board (pour approximated as board area minus clearances)."""
    clr = plan["clearance"]
    items = []
    for p in plan["pads"]:
        for L in pad_layers(p):
            items.append((L, p["net"], pad_geom(p), "pad %s-%s" % (p["des"], p["name"])))
    for t in plan["tracks"]:
        L = 0 if t["layer"] == TOP else 1
        g = LineString([(t["x1"], t["y1"]), (t["x2"], t["y2"])]) if (t["x1"], t["y1"]) != (t["x2"], t["y2"]) else Point(t["x1"], t["y1"])
        items.append((L, t["net"], g.buffer(t["w"] / 2, 8), "track %s" % t["net"]))
    for v in plan["vias"]:
        for L in (0, 1):
            items.append((L, v["net"], Point(v["x"], v["y"]).buffer(v["d"] / 2, 16), "via %s" % v["net"]))
    board = Polygon(plan["board"])
    pour = plan.get("pour")
    if pour:
        PL = 1 if pour.get("layer", "bottom") == "bottom" else 0
        others = unary_union([g.buffer(clr + 2) for L, n, g, _ in items if L == PL and n != pour["net"]])
        area = board.buffer(-25).difference(others)
        for part in getattr(area, "geoms", [area]):
            if part.area > 2000:
                items.append((PL, pour["net"], part, "pour %s" % pour["net"]))
    viol = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a[0] != b[0] or a[1] == b[1] or (a[1] is None and b[1] is None):
                continue
            if a[3].startswith("pour") or b[3].startswith("pour"):
                continue
            d = a[2].distance(b[2])
            if d < clr - 0.3:
                viol.append("%.1f < %s mil: %s <-> %s" % (d, clr, a[3], b[3]))
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if a[1] and a[1] == b[1] and a[0] == b[0] and a[2].intersects(b[2]):
                parent[find(i)] = find(j)
    # a through-hole pad / via is one object on both layers
    by = {}
    for i, it in enumerate(items):
        by.setdefault((it[1], it[3], round(it[2].centroid.x, 2), round(it[2].centroid.y, 2)), []).append(i)
    for ks in by.values():
        for k in ks[1:]:
            parent[find(k)] = find(ks[0])
    opens = {}
    for i, it in enumerate(items):
        if it[1] and it[3].startswith("pad"):
            opens.setdefault(it[1], {}).setdefault(find(i), set()).add(it[3][4:])
    opens = {n: [sorted(s) for s in g.values()] for n, g in opens.items() if len(g) > 1}
    log("plan check: %d clearance violations, %d open nets" % (len(viol), len(opens)))
    for v in viol[:10]:
        log("  " + v)
    for n, g in list(opens.items())[:10]:
        log("  open %s: %s" % (n, g))
    return viol, opens


# ------------------------------------------------------------------------------------------------ apply in Altium
APPLY = r"""
Var Board : IPCB_Board; Nets : TStringList;

Function FindNet(Nm : String) : IPCB_Net;
Var It : IPCB_BoardIterator; N : IPCB_Net;
Begin
  Result := Nil;
  It := Board.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eNetObject)); It.AddFilter_LayerSet(AllLayers); It.AddFilter_Method(eProcessAll);
  N := It.FirstPCBObject;
  While N <> Nil Do Begin
    If N.Name = Nm Then Result := N;
    N := It.NextPCBObject;
  End;
  Board.BoardIterator_Destroy(It);
End;

Procedure Place(Des : String; X, Y : Double; Rot : Integer);
Var C : IPCB_Component;
Begin
  C := Board.GetPcbComponentByRefDes(Des);
  If C = Nil Then Begin L('!! missing ' + Des); Exit; End;
  PCBServer.SendMessageToRobots(C.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
  C.BeginModify;
  C.Rotation := Rot;
  C.MoveByXY(MilsToCoord(X) - C.X, MilsToCoord(Y) - C.Y);
  C.EndModify;
  PCBServer.SendMessageToRobots(C.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);
End;

Procedure T(Lay : Integer; NetName : String; X1, Y1, X2, Y2, W : Double);
Var Tr : IPCB_Track; N : IPCB_Net;
Begin
  N := FindNet(NetName);
  Tr := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
  Tr.X1 := MilsToCoord(X1); Tr.Y1 := MilsToCoord(Y1); Tr.X2 := MilsToCoord(X2); Tr.Y2 := MilsToCoord(Y2);
  Tr.Width := MilsToCoord(W); Tr.Layer := Lay;
  Board.AddPCBObject(Tr);
  If N <> Nil Then Begin N.AddPCBObject(Tr); Tr.Net := N; End;
  PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Tr.I_ObjectAddress);
End;

Procedure V(NetName : String; X, Y, D, H : Double);
Var Vi : IPCB_Via; N : IPCB_Net;
Begin
  N := FindNet(NetName);
  Vi := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
  Vi.X := MilsToCoord(X); Vi.Y := MilsToCoord(Y); Vi.Size := MilsToCoord(D); Vi.HoleSize := MilsToCoord(H);
  Vi.LowLayer := eBottomLayer; Vi.HighLayer := eTopLayer;
  Board.AddPCBObject(Vi);
  If N <> Nil Then Begin N.AddPCBObject(Vi); Vi.Net := N; End;
  PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Vi.I_ObjectAddress);
End;

Procedure Outline;
Var Tr : IPCB_Track;
Begin
%(outline)s
  ResetParameters; AddStringParameter('Mode', 'BOARDOUTLINE_FROM_SEL_PRIMS'); RunProcess('PCB:PlaceBoardOutline');
End;

Procedure Pour;
Var Pg : IPCB_Polygon; Sg : TPolySegment;
Begin
%(pour)s
End;

Procedure RunAll;
Begin
  L('apply start');
  OpenDoc('%(prj)s', 'Project'); OpenDoc('%(pcb)s', 'Document');
  Board := PCBServer.GetCurrentPCBBoard;
  If Board = Nil Then Begin L('!! no board'); Exit; End;
  PCBServer.PreProcess;
%(place)s
  L('placed');
%(tracks)s
  L('routed');
  PCBServer.PostProcess;
  Outline;
  L('outline');
  Pour;
  L('poured');
  Board.ViewManager_FullUpdate;
  ResetParameters; AddStringParameter('ObjectKind', 'Document'); AddStringParameter('FileName', '%(pcb)s'); RunProcess('WorkspaceManager:SaveObject');
  L('@@DONE');
End;
"""


def apply(prj, plan, log=print):
    pl = "\n".join("  Place('%s', %.4f, %.4f, %d);" % (d, p["x"], p["y"], int(round(p["rot"])) % 360) for d, p in plan["placement"].items())
    tr = "\n".join("  T(%s, '%s', %.4f, %.4f, %.4f, %.4f, %.2f);" % ("eTopLayer" if t["layer"] == TOP else "eBottomLayer", t["net"], t["x1"], t["y1"], t["x2"], t["y2"], t["w"])
                   for t in plan["tracks"])
    tr += "\n" + "\n".join("  V('%s', %.4f, %.4f, %.2f, %.2f);" % (v["net"], v["x"], v["y"], v["d"], v["h"]) for v in plan["vias"])
    pts = plan["board"]
    ol = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        ol.append("  Tr := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default); Tr.X1 := MilsToCoord(%.3f); Tr.Y1 := MilsToCoord(%.3f); "
                  "Tr.X2 := MilsToCoord(%.3f); Tr.Y2 := MilsToCoord(%.3f); Tr.Width := MilsToCoord(5); Tr.Layer := eMechanical1; Board.AddPCBObject(Tr); Tr.Selected := True;"
                  % (x1, y1, x2, y2))
    pour = ""
    if plan.get("pour"):
        pn = plan["pour"]
        inner = Polygon(pts).buffer(-25, join_style=2)
        cs = list(inner.exterior.coords)[:-1]
        pour = ["  Pg := PCBServer.PCBObjectFactory(ePolyObject, eNoDimension, eCreate_Default);",
                "  Pg.Layer := %s; Pg.PolyHatchStyle := ePolySolid; Pg.RemoveDead := True; Pg.Name := '%s_POUR';"
                % ("eBottomLayer" if pn.get("layer", "bottom") == "bottom" else "eTopLayer", pn["net"]),
                "  Pg.PointCount := %d;" % len(cs)]
        for k, (x, y) in enumerate(cs + [cs[0]]):
            pour.append("  Sg := TPolySegment; Sg.Kind := ePolySegmentLine; Sg.vx := MilsToCoord(%.3f); Sg.vy := MilsToCoord(%.3f); Pg.Segments[%d] := Sg;" % (x, y, k))
        pour += ["  Board.AddPCBObject(Pg);", "  Pg.Net := FindNet('%s');" % pn["net"], "  Pg.Rebuild;"]
        pour = "\n".join(pour)
    body = APPLY % dict(prj=prj.prjpcb, pcb=prj.pcbdoc, place=pl, tracks=tr, outline="\n".join(ol), pour=pour)
    return altium.run_script(body, timeout=900)


def preview(plan, path):
    """PNG of the planned layout (top red, bottom blue, pads grey, vias black) for a human look before applying."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly
    b = Polygon(plan["board"])
    x0, y0, x1, y1 = b.bounds
    fig, ax = plt.subplots(figsize=(12, 12 * (y1 - y0) / max(x1 - x0, 1)))
    ax.add_patch(MPoly(list(b.exterior.coords), closed=True, fill=False, ec="k", lw=1.5))
    scale = 12 * 72 / max(x1 - x0, 1)
    for t in plan["tracks"]:
        ax.plot([t["x1"], t["x2"]], [t["y1"], t["y2"]], color="red" if t["layer"] == TOP else "blue",
                lw=max(t["w"] * scale, 0.6), alpha=0.6, solid_capstyle="round")
    for p in plan["pads"]:
        g = pad_geom(p)
        ax.add_patch(MPoly(list(g.exterior.coords), closed=True, color="#999" if p["hole"] <= 0 else "#d4a017", alpha=0.9))
    for v in plan["vias"]:
        ax.add_patch(plt.Circle((v["x"], v["y"]), v["d"] / 2, color="k"))
    for d, q in plan.get("placement", {}).items():
        ax.text(q["x"], q["y"], d, fontsize=8, color="green", ha="center", va="center", weight="bold")
    ax.set_xlim(x0 - 20, x1 + 20)
    ax.set_ylim(y0 - 20, y1 + 20)
    ax.set_aspect("equal")
    ax.set_title("planned layout: %d tracks, %d vias, failed nets: %s" % (len(plan["tracks"]), len(plan["vias"]), plan.get("failed") or "none"))
    plt.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)
    return path
