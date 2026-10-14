"""Read-only parser for Altium PcbDoc (OLE) files: components, nets, pads, tracks, arcs, vias, fills, poured regions.
Coordinates are in mil, Altium absolute (not relative to the board origin)."""
import re
import struct

import olefile

TOP, BOT, MULTI = 1, 32, 74
NO_NET = 65535


def mil(s):
    return float(str(s).replace("mil", ""))


def txtrecs(b):
    out, i = [], 0
    while i < len(b):
        n = struct.unpack("<I", b[i:i + 4])[0]
        t = b[i + 4:i + 4 + n].decode("latin1").strip("\x00")
        out.append(dict(s.split("=", 1) for s in t.split("|") if "=" in s))
        i += 4 + n
    return out


def binrecs(b, nsub=1):
    out, i = [], 0
    while i < len(b):
        typ, j, subs = b[i], i + 1, []
        for _ in range(nsub):
            n = struct.unpack("<I", b[j:j + 4])[0]
            subs.append(b[j + 4:j + 4 + n])
            j += 4 + n
        out.append((typ, subs))
        i = j
    return out


class PCB:
    def __init__(self, fn):
        o = olefile.OleFileIO(fn)
        rd = lambda p: o.openstream(p).read() if o.exists(p) else b""
        self.raw = rd
        self.comps = txtrecs(rd("Components6/Data"))
        self.names = [c.get("SOURCEDESIGNATOR", "?") for c in self.comps]
        self.nets = [n.get("NAME") for n in txtrecs(rd("Nets6/Data"))]
        self.board = txtrecs(rd("Board6/Data"))[0]
        self.pads, self.tracks, self.vias, self.arcs, self.fills, self.pours = [], [], [], [], [], []
        for _, subs in binrecs(rd("Pads6/Data"), 6):
            g = subs[4]
            name = subs[0][1:1 + subs[0][0]].decode("latin1")
            x, y = struct.unpack("<ii", g[13:21])
            sx, sy = struct.unpack("<ii", g[21:29])
            self.pads.append(dict(name=name, layer=g[0], net=struct.unpack("<H", g[3:5])[0], comp=struct.unpack("<H", g[7:9])[0],
                                  x=x / 1e4, y=y / 1e4, sx=sx / 1e4, sy=sy / 1e4, hole=struct.unpack("<i", g[45:49])[0] / 1e4,
                                  shape=g[49], rot=struct.unpack("<d", g[52:60])[0]))
        for _, subs in binrecs(rd("Tracks6/Data")):
            g = subs[0]
            x1, y1, x2, y2, w = struct.unpack("<iiiii", g[13:33])
            self.tracks.append(dict(layer=g[0], net=struct.unpack("<H", g[3:5])[0], comp=struct.unpack("<H", g[7:9])[0],
                                    x1=x1 / 1e4, y1=y1 / 1e4, x2=x2 / 1e4, y2=y2 / 1e4, w=w / 1e4))
        for _, subs in binrecs(rd("Vias6/Data")):
            g = subs[0]
            x, y, d, h = struct.unpack("<iiii", g[13:29])
            self.vias.append(dict(net=struct.unpack("<H", g[3:5])[0], x=x / 1e4, y=y / 1e4, d=d / 1e4, h=h / 1e4))
        for _, subs in binrecs(rd("Arcs6/Data")):
            g = subs[0]
            x, y, r = struct.unpack("<iii", g[13:25])
            a1, a2 = struct.unpack("<dd", g[25:41])
            self.arcs.append(dict(layer=g[0], net=struct.unpack("<H", g[3:5])[0], comp=struct.unpack("<H", g[7:9])[0],
                                  x=x / 1e4, y=y / 1e4, r=r / 1e4, a1=a1, a2=a2, w=struct.unpack("<i", g[41:45])[0] / 1e4))
        for _, subs in binrecs(rd("Fills6/Data")):
            g = subs[0]
            x1, y1, x2, y2 = [v / 1e4 for v in struct.unpack("<iiii", g[13:29])]
            self.fills.append(dict(layer=g[0], net=struct.unpack("<H", g[3:5])[0], x1=x1, y1=y1, x2=x2, y2=y2))
        self._read_pours(rd)

    def _read_pours(self, rd):
        """Poured polygon copper is stored as Regions6 copper regions; the net comes from the owning polygon."""
        pd, i, pnet = rd("Polygons6/Data"), 0, []
        while i < len(pd):
            n = struct.unpack("<I", pd[i:i + 4])[0]
            s = pd[i + 4:i + 4 + n].decode("latin1")
            i += 4 + n
            m = re.search(r"\|NET=(\d+)", s)
            pnet.append(int(m.group(1)) if m else NO_NET)
        rr, i = rd("Regions6/Data"), 0
        while i < len(rr):
            n = struct.unpack("<I", rr[i + 1:i + 5])[0]
            r = rr[i + 5:i + 5 + n]
            i += 5 + n
            if r[0] not in (TOP, BOT):
                continue
            pl = struct.unpack("<I", r[18:22])[0]
            props = r[22:22 + pl].decode("latin1", "replace")
            net, poly = struct.unpack("<H", r[3:5])[0], struct.unpack("<H", r[5:7])[0]
            if "KIND=1" in props or ("ISSHAPEBASED" not in props and net >= NO_NET and poly >= NO_NET):
                continue  # polygon cutout / keepout, not copper
            if net >= NO_NET and poly < len(pnet):
                net = pnet[poly]
            t = r[22 + pl:]
            k = struct.unpack("<I", t[:4])[0]
            q = 4
            outer = [tuple(v / 1e4 for v in struct.unpack("<dd", t[q + 16 * j:q + 16 * j + 16])) for j in range(k)]
            q += 16 * k
            holes = []
            while q < len(t):
                m = struct.unpack("<I", t[q:q + 4])[0]
                q += 4
                holes.append([tuple(v / 1e4 for v in struct.unpack("<dd", t[q + 16 * j:q + 16 * j + 16])) for j in range(m)])
                q += 16 * m
            self.pours.append(dict(layer=r[0], net=net, outer=outer, holes=holes))

    # ---- helpers -------------------------------------------------------------------------
    def net(self, i):
        return self.nets[i] if i < len(self.nets) else None

    def origin(self):
        return mil(self.board.get("ORIGINX", "0mil")), mil(self.board.get("ORIGINY", "0mil"))

    def outline(self):
        b, pts, k = self.board, [], 0
        while "VX%d" % k in b:
            pts.append((mil(b["VX%d" % k]), mil(b["VY%d" % k])))
            k += 1
        return pts

    def netlist(self):
        """{designator: {pad: net}} -- same shape as schfile.netlist()."""
        out = {}
        for q in self.pads:
            if q["comp"] < NO_NET:
                out.setdefault(self.names[q["comp"]], {})[q["name"]] = self.net(q["net"]) or "-"
        return out

    def components(self):
        out = []
        for c in self.comps:
            out.append(dict(des=c.get("SOURCEDESIGNATOR"), x=mil(c["X"]), y=mil(c["Y"]), rot=float(c.get("ROTATION", 0)),
                            layer=c.get("LAYER", "TOP"), pattern=c.get("PATTERN", ""), descr=c.get("SOURCEDESCRIPTION", "")))
        return out
