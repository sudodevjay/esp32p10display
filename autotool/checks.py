"""Independent design checks on the saved files (no Altium needed):
netlist schematic vs PCB, copper clearance incl. net-class barriers, opens, unconnected track ends, board edge,
plus parsing of Altium's own DRC / ERC HTML reports."""
import html
import math
import os
import re

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.strtree import STRtree

from .pcbfile import BOT, MULTI, NO_NET, TOP


# ---------------------------------------------------------------- netlist
def compare_netlists(sch, pcb):
    """Returns a list of human-readable differences (empty = identical)."""
    diffs = []
    for d in sorted(set(sch) | set(pcb)):
        if d not in pcb:
            diffs.append("%s only in schematic" % d)
            continue
        if d not in sch:
            diffs.append("%s only in PCB" % d)
            continue
        for p in sorted(set(sch[d]) | set(pcb[d])):
            a, b = sch[d].get(p, "<no pin>"), pcb[d].get(p, "<no pad>")
            if a != b:
                diffs.append("%s-%s: schematic %s, PCB %s" % (d, p, a, b))
    return diffs


# ---------------------------------------------------------------- copper geometry
def _padgeom(q):
    w, h = q["sx"], q["sy"]
    if q["shape"] == 1:
        r = min(w, h) / 2
        if abs(w - h) < 0.01:
            g = Point(0, 0).buffer(r, 16)
        else:
            g = (LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)]) if w > h else LineString([(0, -(h / 2 - r)), (0, h / 2 - r)])).buffer(r, 16)
    else:
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    return affinity.translate(affinity.rotate(g, q["rot"], origin=(0, 0)), q["x"], q["y"])


def copper_items(p):
    """[(layers, net, geometry, description, component)] for top/bottom copper."""
    items = []
    for q in p.pads:
        if q["layer"] == MULTI:
            L = (TOP, BOT)
        elif q["layer"] in (TOP, BOT):
            L = (q["layer"],)
        else:
            continue
        c = p.names[q["comp"]] if q["comp"] < NO_NET else ""
        items.append((L, p.net(q["net"]), _padgeom(q), "pad %s-%s" % (c, q["name"]), c))
    for t in p.tracks:
        if t["layer"] not in (TOP, BOT):
            continue
        a, b = (t["x1"], t["y1"]), (t["x2"], t["y2"])
        g = (LineString([a, b]) if a != b else Point(a)).buffer(t["w"] / 2, 16)
        c = p.names[t["comp"]] if t["comp"] < NO_NET else ""
        items.append(((t["layer"],), p.net(t["net"]), g, "track %s L%d (%.0f,%.0f)-(%.0f,%.0f)" % (p.net(t["net"]), t["layer"], *a, *b), c))
    for a in p.arcs:
        if a["layer"] not in (TOP, BOT):
            continue
        a1, a2 = a["a1"], a["a2"] + (360 if a["a2"] <= a["a1"] else 0)
        pts = [(a["x"] + a["r"] * math.cos(math.radians(a1 + (a2 - a1) * k / 24)),
                a["y"] + a["r"] * math.sin(math.radians(a1 + (a2 - a1) * k / 24))) for k in range(25)]
        c = p.names[a["comp"]] if a["comp"] < NO_NET else ""
        items.append(((a["layer"],), p.net(a["net"]), LineString(pts).buffer(a["w"] / 2, 16), "arc %s" % p.net(a["net"]), c))
    for v in p.vias:
        items.append(((TOP, BOT), p.net(v["net"]), Point(v["x"], v["y"]).buffer(v["d"] / 2, 16), "via %s (%.0f,%.0f)" % (p.net(v["net"]), v["x"], v["y"]), ""))
    for f in p.fills:
        if f["layer"] in (TOP, BOT):
            items.append(((f["layer"],), p.net(f["net"]), box(min(f["x1"], f["x2"]), min(f["y1"], f["y2"]), max(f["x1"], f["x2"]), max(f["y1"], f["y2"])),
                          "fill %s L%d" % (p.net(f["net"]), f["layer"]), ""))
    for r in p.pours:
        items.append(((r["layer"],), p.net(r["net"]), Polygon(r["outer"], r["holes"]).buffer(0), "pour %s L%d" % (p.net(r["net"]), r["layer"]), ""))
    return items


def copper_checks(p, settings):
    items = copper_items(p)
    base = settings["clearance_mil"]
    cls = {}
    for k in settings["net_classes"]:
        for n in k["nets"]:
            cls[n] = k
    exempt = set(settings["exempt_components"])

    exempt_pad = settings.get("exempt_pad_mil", 50)

    def need(a, b):
        if a[1] is None or b[1] is None:          # unused / net-less pads only need the basic clearance
            return base, "clearance"
        ka, kb = cls.get(a[1]), cls.get(b[1])
        if ka is None and kb is None:
            return base, "clearance"
        if ka is kb:
            return max(base, ka.get("self_mil", base)), ka["name"]
        kind = "%s-%s" % (ka["name"] if ka else "other", kb["name"] if kb else "other")
        if a[4] and a[4] == b[4] and a[4] in exempt:
            return base, "clearance"
        req = max(k.get("other_mil", base) for k in (ka, kb) if k)
        # parts that straddle a barrier (isolator, isolated DC-DC) have pads closer than the barrier by design;
        # copper next to their pads needs at least exempt_pad_mil
        if (a[4] in exempt and a[3].startswith("pad")) or (b[4] in exempt and b[3].startswith("pad")):
            req = min(req, exempt_pad)
        return req, kind

    maxreq = max([base] + [k.get("other_mil", base) for k in settings["net_classes"]] + [k.get("self_mil", base) for k in settings["net_classes"]])
    tree = STRtree([it[2] for it in items])
    viol = []
    for i, a in enumerate(items):
        for j in tree.query(a[2].buffer(maxreq)):
            j = int(j)
            if j <= i:
                continue
            b = items[j]
            if not set(a[0]) & set(b[0]):
                continue
            if a[1] is not None and a[1] == b[1]:
                continue
            if a[1] is None and b[1] is None and a[4] == b[4]:
                continue
            req, kind = need(a, b)
            d = a[2].distance(b[2])
            if d < req - 0.05:
                viol.append(dict(kind=kind, dist=round(d, 1), need=req, a=a[3], b=b[3]))

    # connectivity per net (pads only count as the things that must be joined)
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(items):
        if a[1] is None:
            continue
        for j in tree.query(a[2]):
            j = int(j)
            if j > i and items[j][1] == a[1] and set(a[0]) & set(items[j][0]) and a[2].intersects(items[j][2]):
                parent[find(i)] = find(j)
    groups = {}
    for i, it in enumerate(items):
        if it[1] and it[3].startswith("pad"):
            groups.setdefault(it[1], {}).setdefault(find(i), []).append(it[3][4:])
    opens = {n: list(g.values()) for n, g in groups.items() if len(g) > 1}

    ign = set(settings["ignore_unconnected_pads"])
    nonet = [it[3][4:] for it in items if it[1] is None and it[3].startswith("pad") and it[4] not in ign and it[4]]

    outline = Polygon(p.outline())
    edge = sorted((round(outline.exterior.distance(it[2]), 1), it[3]) for it in items
                  if outline.exterior.distance(it[2]) < settings["edge_clearance_mil"] or not outline.contains(it[2]))
    return dict(violations=viol, opens=opens, pads_without_net=nonet, edge=edge, items=len(items))


def dangling_track_ends(p):
    """Track ends that touch nothing of the same net on the same layer (pad, via, arc, other track or poured copper)."""
    objs = {}
    for q in p.pads:
        if q["net"] < NO_NET:
            for L in ((TOP, BOT) if q["layer"] == MULTI else (q["layer"],)):
                objs.setdefault((q["net"], L), []).append(_padgeom(q))
    for v in p.vias:
        for L in (TOP, BOT):
            objs.setdefault((v["net"], L), []).append(Point(v["x"], v["y"]).buffer(v["d"] / 2))
    for a in p.arcs:
        if a["layer"] in (TOP, BOT):
            objs.setdefault((a["net"], a["layer"]), []).append(
                Point(a["x"], a["y"]).buffer(a["r"] + a["w"] / 2).difference(Point(a["x"], a["y"]).buffer(max(a["r"] - a["w"] / 2, 0))))
    for r in p.pours:
        objs.setdefault((r["net"], r["layer"]), []).append(Polygon(r["outer"], r["holes"]).buffer(0))
    cu = [t for t in p.tracks if t["layer"] in (TOP, BOT) and t["net"] < NO_NET]
    lines = [LineString([(t["x1"], t["y1"]), (t["x2"], t["y2"])]) if (t["x1"], t["y1"]) != (t["x2"], t["y2"]) else Point(t["x1"], t["y1"]) for t in cu]
    out = []
    for i, t in enumerate(cu):
        for e in ((t["x1"], t["y1"]), (t["x2"], t["y2"])):
            P = Point(e)
            if any(g.distance(P) < 0.5 for g in objs.get((t["net"], t["layer"]), [])):
                continue
            if any(j != i and u["net"] == t["net"] and u["layer"] == t["layer"] and lines[j].distance(P) < max(u["w"] / 2, 0.5)
                   for j, u in enumerate(cu)):
                continue
            out.append(dict(net=p.net(t["net"]), layer=t["layer"], at=e))
    return out


# ---------------------------------------------------------------- Altium report parsing
def _text(path):
    t = open(path, encoding="utf-8", errors="ignore").read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))


def altium_drc(path):
    if not os.path.exists(path):
        return None
    t = _text(path)
    m = re.findall(r"Rule Violations Count(.*?)Total (\d+)", t)
    rules = re.findall(r"([A-Z][A-Za-z\- ]+?(?:Constraint|Rule|Clearance|Antennae|Sliver|Polygon)[^0-9]*?\)) (\d+)", m[0][0]) if m else []
    total = int(m[0][1]) if m else None
    i = t.find("Total %d" % total) if total is not None else -1
    details = t[i:i + 3000] if total else ""
    return dict(total=total, rules=[(r.strip(), int(n)) for r, n in rules if int(n)], details=details,
                date=" ".join(re.findall(r"Date: (\S+) Time: (\S+)", t)[0]) if re.search(r"Date: \S+ Time: \S+", t) else "")


def altium_erc(path):
    if not os.path.exists(path):
        return None
    t = _text(path)
    body = t[t.find("Class Document Message") + len("Class Document Message"):]
    body = body.split("Back to top")[0]
    msgs = re.findall(r"(Error|Warning|Fatal Error) (\S+\.SchDoc) (.*?)(?= (?:Error|Warning|Fatal Error) \S+\.SchDoc |$)", body.strip())
    return dict(errors=[m for m in msgs if m[0] != "Warning"], warnings=[m for m in msgs if m[0] == "Warning"],
                date=" ".join(re.findall(r"Date: (\S+) Time: (\S+)", t)[0]) if re.search(r"Date: \S+ Time: \S+", t) else "")
