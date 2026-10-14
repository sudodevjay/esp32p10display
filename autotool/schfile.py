"""Read-only parser for Altium SchDoc files: component list with parameters, and a multi-sheet netlist that follows
Altium's connection semantics (wire end on wire connects, X-crossings only with a junction, pin hot end / power port /
net label on a wire connects, ports and labels are global by name)."""
import struct
from collections import defaultdict

import olefile


def recs(fn):
    b = olefile.OleFileIO(fn).openstream("FileHeader").read()
    out, i = [], 0
    while i < len(b):
        n = struct.unpack("<I", b[i:i + 4])[0]
        ln, t = n & 0xFFFFFF, n >> 24
        d = b[i + 4:i + 4 + ln]
        i += 4 + ln
        out.append({k.upper(): v for k, v in (x.split("=", 1) for x in d.decode("latin1").rstrip("\x00").split("|") if "=" in x)}
                   if t == 0 else {})
    return out[1:]


def _c(r, k):
    return int(r.get(k, 0) or 0) + int(r.get(k + "_FRAC", 0) or 0) / 1e5


def parts(schdocs):
    """[{des, lib, descr, params{...}, sheet}] -- 'Comment' is resolved when it is '=ParamName'."""
    out = []
    for fn in schdocs:
        R = recs(fn)
        comps = {}
        for n, r in enumerate(R):
            if r.get("RECORD") == "1":
                comps[n] = dict(lib=r.get("LIBREFERENCE", ""), descr=r.get("COMPONENTDESCRIPTION", ""), params={}, sheet=fn)
        for r in R:
            o = r.get("OWNERINDEX", "")
            if not o.lstrip("-").isdigit() or int(o) not in comps:
                continue
            c = comps[int(o)]
            if r.get("RECORD") == "34":
                c["des"] = r.get("TEXT")
            elif r.get("RECORD") == "41" and r.get("NAME"):
                c["params"][r["NAME"]] = r.get("TEXT", "")
        for c in comps.values():
            com = c["params"].get("Comment", "")
            if com.startswith("="):
                c["params"]["Comment"] = c["params"].get(com[1:], com)
            out.append(c)
    return out


def netlist(schdocs):
    """{designator: {pin: net}}; unnamed nets get Altium-style names Net<Des>_<Pin>."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    def onseg(p, P):
        for a, b in zip(P, P[1:]):
            if (min(a[0], b[0]) - 1e-6 <= p[0] <= max(a[0], b[0]) + 1e-6 and min(a[1], b[1]) - 1e-6 <= p[1] <= max(a[1], b[1]) + 1e-6
                    and abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) < 1e-6):
                return True
        return False

    pins = []
    for si, fn in enumerate(schdocs):
        R = recs(fn)
        des = {int(r["OWNERINDEX"]): r["TEXT"] for r in R if r.get("RECORD") == "34"}
        wires, pts, junc = [], [], []
        for n, r in enumerate(R):
            t = r.get("RECORD")
            if t == "27":
                k = int(r["LOCATIONCOUNT"])
                wires.append(("S%dW%d" % (si, n), [(_c(r, "X%d" % j), _c(r, "Y%d" % j)) for j in range(1, k + 1)]))
            elif t == "2" and int(r.get("OWNERINDEX", -1)) in des:
                x, y = _c(r, "LOCATION.X"), _c(r, "LOCATION.Y")
                L = int(r.get("PINLENGTH", 0))
                dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][int(r.get("PINCONGLOMERATE", 0)) & 3]
                node = "S%dP%d" % (si, n)
                pins.append((des[int(r["OWNERINDEX"])], r.get("DESIGNATOR"), node))
                pts.append(((x + dx * L, y + dy * L), node))
            elif t in ("17", "25"):  # power port, net label
                node = "S%dL%d" % (si, n)
                pts.append(((_c(r, "LOCATION.X"), _c(r, "LOCATION.Y")), node))
                union(node, "NET:" + r.get("TEXT", ""))
            elif t == "18":  # sheet port: global by name in a flat design
                node = "S%dO%d" % (si, n)
                pts.append(((_c(r, "LOCATION.X"), _c(r, "LOCATION.Y")), node))
                union(node, "NET:" + r.get("NAME", ""))
            elif t == "29":
                junc.append((_c(r, "LOCATION.X"), _c(r, "LOCATION.Y")))
        for wn, _ in wires:
            find(wn)
        for i, (wn, P) in enumerate(wires):
            for wn2, P2 in wires[i + 1:]:
                if onseg(P[0], P2) or onseg(P[-1], P2) or onseg(P2[0], P) or onseg(P2[-1], P):
                    union(wn, wn2)
        for j in junc:
            on = [wn for wn, P in wires if onseg(j, P)]
            for a, b in zip(on, on[1:]):
                union(a, b)
        for xy, node in pts:
            find(node)
            for wn, P in wires:
                if onseg(xy, P):
                    union(node, wn)
            for xy2, node2 in pts:
                if xy2 == xy and node2 != node:
                    union(node, node2)

    names = defaultdict(set)
    for k in list(parent):
        if k.startswith("NET:"):
            names[find(k)].add(k[4:])
    out = defaultdict(dict)
    for d, p, node in pins:
        r = find(node)
        if r in names:
            net = "+".join(sorted(names[r]))
        else:
            members = sorted((dd, pp) for dd, pp, nn in pins if find(nn) == r)
            net = "Net%s_%s" % members[0] if len(members) > 1 else "-"
        out[d][p] = net
    return dict(out)
