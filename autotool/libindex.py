"""Catalog of the central Altium library (default D:\\pcb\\pcblibrary\\Libraries): every schematic component in every
IntLib / SchLib with description, footprint(s), parameters and pins. Used to write design specs (phase 5) with parts
that really exist, and to know pin names/numbers without opening Altium."""
import io
import json
import os
import struct
import zlib

import olefile

LIB_ROOT = r"D:\pcb\pcblibrary\Libraries"
CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library_catalog.json")
ETYPE = ["input", "io", "output", "open_collector", "passive", "hiz", "open_emitter", "power"]


def _records(b):
    i = 0
    while i < len(b):
        n = struct.unpack("<I", b[i:i + 4])[0]
        ln, t = n & 0xFFFFFF, n >> 24
        yield t, b[i + 4:i + 4 + ln]
        i += 4 + ln


def _txt(r):
    return {k.upper(): v for k, v in (x.split("=", 1) for x in r.decode("latin1").rstrip("\x00").split("|") if "=" in x)}


def _pstr(b, i):
    n = b[i]
    return b[i + 1:i + 1 + n].decode("latin1"), i + 1 + n


def _binpin(r):
    """Binary pin record (SchLib): returns dict or None."""
    try:
        i = 4 + 1 + 2 + 1 + 4          # record id, ?, owner part, display mode, symbol edges
        desc, i = _pstr(r, i)
        etype = r[i + 1]
        congl = r[i + 2]
        length, x, y = struct.unpack("<hhh", r[i + 3:i + 9])
        i += 9 + 4                      # + colour
        name, i = _pstr(r, i)
        des, i = _pstr(r, i)
        return dict(des=des, name=name, etype=ETYPE[etype] if etype < len(ETYPE) else str(etype), x=x * 10, y=y * 10,
                    length=length * 10, orient=congl & 3, hidden=bool(congl & 8), part=struct.unpack("<h", r[5:7])[0])
    except Exception:
        return None


def schlib_components(ole):
    out = []
    for e in ole.listdir():
        if len(e) == 2 and e[1] == "Data":
            comp = dict(libref=e[0], description="", footprints=[], params={}, pins=[], parts=1)
            for t, r in _records(ole.openstream("/".join(e)).read()):
                if t == 1:
                    p = _binpin(r)
                    if p:
                        comp["pins"].append(p)
                    continue
                d = _txt(r)
                rec = d.get("RECORD")
                if rec == "1":
                    comp["libref"] = d.get("LIBREFERENCE", e[0])
                    comp["description"] = d.get("COMPONENTDESCRIPTION", "")
                    comp["parts"] = int(d.get("PARTCOUNT", "2")) - 1 or 1
                elif rec == "41" and d.get("NAME"):
                    comp["params"][d["NAME"]] = d.get("TEXT", "")
                elif rec == "45" and d.get("MODELTYPE") == "PCBLIB":
                    comp["footprints"].append(d.get("MODELNAME"))
                elif rec == "2":   # older text pin
                    comp["pins"].append(dict(des=d.get("DESIGNATOR"), name=d.get("NAME", ""), etype=ETYPE[int(d.get("ELECTRICAL", 4))],
                                             x=int(d.get("LOCATION.X", 0)), y=int(d.get("LOCATION.Y", 0)), length=int(d.get("PINLENGTH", 0)),
                                             orient=int(d.get("PINCONGLOMERATE", 0)) & 3, hidden=False, part=int(d.get("OWNERPARTID", 1))))
            out.append(comp)
    return out


def index(root=LIB_ROOT, out=CATALOG, log=print):
    cat = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith("_backup")]
        for fn in fns:
            path = os.path.join(dp, fn)
            ext = fn.lower().rsplit(".", 1)[-1]
            try:
                if ext == "intlib":
                    o = olefile.OleFileIO(path)
                    for e in o.listdir():
                        if e[0] == "SchLib":
                            raw = o.openstream("/".join(e)).read()
                            comps = schlib_components(olefile.OleFileIO(io.BytesIO(zlib.decompress(raw[1:]))))
                            for c in comps:
                                cat.append(dict(c, library=path, kind="IntLib"))
                elif ext == "schlib":
                    for c in schlib_components(olefile.OleFileIO(path)):
                        cat.append(dict(c, library=path, kind="SchLib"))
            except Exception as ex:
                log("  skip %s: %s" % (fn, ex))
    json.dump(cat, open(out, "w", encoding="utf-8"), indent=1)
    log("catalog: %d components from %s -> %s" % (len(cat), root, out))
    return cat


def load(path=CATALOG):
    return json.load(open(path, encoding="utf-8"))


def search(words, cat=None):
    cat = cat or load()
    w = [x.lower() for x in words]
    hits = []
    for c in cat:
        blob = " ".join([c["libref"], c["description"], " ".join(c["footprints"]), " ".join(c["params"].values()), os.path.basename(c["library"])]).lower()
        if all(x in blob for x in w):
            hits.append(c)
    return hits
