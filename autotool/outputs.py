"""Fabrication outputs: OutJob handling, Gerber zip for the fab, BOM (full + JLCPCB format), CPL (pick & place), renders."""
import contextlib
import csv
import datetime
import os
import re
import shutil
import subprocess
import zipfile

from .pcbfile import PCB
from .schfile import parts

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "AutoTool.OutJob")
ERC_BLOCK = """OutputType{n}=Electrical Rules Check
OutputName{n}=Electrical Rules Check
OutputCategory{n}=Validation
OutputDocumentPath{n}=
OutputVariantName{n}=
OutputEnabled{n}=1
OutputEnabled{n}_OutputMedium1=0
OutputEnabled{n}_OutputMedium2=2
OutputDefault{n}=0
"""
FAB_EXT = {"GTL", "GBL", "GTO", "GBO", "GTS", "GBS", "GTP", "GBP", "TXT", "DRL"}
INNER = re.compile(r"^G\d+$|^GP\d+$")


def pick_outjob(prj):
    """The project's OutJob that has a Gerber output; None if there is none."""
    for oj in prj.outjobs:
        if os.path.exists(oj) and "OutputType" in open(oj, encoding="latin1").read() and "=Gerber" in open(oj, encoding="latin1").read():
            return oj
    return None


def new_outjob_text(prj):
    t = open(TEMPLATE, encoding="latin1").read()
    return t.replace("{PROJDIR}", prj.dir).replace("{PRJNAME}", prj.name).replace("{PCBDOC}", os.path.basename(prj.pcbdoc))


@contextlib.contextmanager
def outjob_with_erc(oj):
    """Temporarily add an ERC output (Reports medium) to an OutJob that lacks one; restore the file afterwards."""
    orig = open(oj, "rb").read()
    t = orig.decode("latin1")
    if "=Electrical Rules Check" in t:
        yield oj
        return
    n = max(int(x) for x in re.findall(r"^OutputType(\d+)=", t, re.M)) + 1
    t = t.replace("\r\n", "\n")
    i = t.index("\n[PublishSettings]")
    t = t[:i].rstrip("\n") + "\n" + ERC_BLOCK.format(n=n) + t[i:]
    open(oj, "wb").write(t.replace("\n", "\r\n").encode("latin1"))
    try:
        yield oj
    finally:
        open(oj, "wb").write(orig)


def gerber_zip(prj, out_dir=None):
    """Zip the Gerber/drill set for the fab. Only copper, mask, paste, silk, drill and the outline layer go in; the
    keep-out (GKO) and other mechanical layers are left out because fabs may read them as extra outline."""
    s = prj.settings
    files = []
    stem = os.path.splitext(os.path.basename(prj.pcbdoc))[0].lower()
    for f in sorted(os.listdir(prj.fab_dir)):
        if not f.lower().startswith(stem + ".") and not f.lower().startswith(stem + "-"):
            continue  # skip 'Status Report.Txt' and other non-board files
        ext = f.rsplit(".", 1)[-1].upper()
        if ext in FAB_EXT or ext == s["outline_layer"].upper() or INNER.match(ext):
            files.append(f)
    name = s["zip_name"].format(prj=prj.name, date=datetime.date.today().isoformat())
    path = os.path.join(out_dir or prj.outputs_dir, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(prj.fab_dir, f), f)
    return path, files


def _nat(d):
    m = re.match(r"([A-Za-z_]*)(\d*)", d)
    return m.group(1), int(m.group(2) or 0), d


def bom(prj, out_dir=None, lcsc=None):
    """Grouped BOM from the schematics, footprints taken from the PCB (what will actually be built)."""
    pcb = PCB(prj.pcbdoc)
    pat = {c["des"]: c["pattern"] for c in pcb.components()}
    rows = {}
    for c in parts(prj.schdocs):
        p = c["params"]
        mpn = p.get("MP") or p.get("Part Number") or p.get("Manufacturer Part Number") or p.get("MPN") or p.get("Comment", "")
        mf = p.get("MF") or p.get("Manufacturer") or ""
        lc = p.get("LCSC") or p.get("LCSC Part") or p.get("LCSC Part #") or (lcsc or {}).get(mpn, "")
        r = rows.setdefault((mpn, pat.get(c["des"], "")), dict(des=[], comment=p.get("Comment", ""), mf=mf, descr=c["descr"], lcsc=lc))
        r["des"].append(c["des"])
    out = sorted(rows.items(), key=lambda kv: _nat(sorted(kv[1]["des"], key=_nat)[0]))
    out_dir = out_dir or os.path.join(prj.outputs_dir, "BOM")
    os.makedirs(out_dir, exist_ok=True)
    date = datetime.date.today().isoformat()
    full = os.path.join(out_dir, "%s_BOM_%s.csv" % (prj.name, date))
    jlc = os.path.join(out_dir, "%s_BOM_JLCPCB_%s.csv" % (prj.name, date))
    with open(full, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["#", "Designators", "Qty", "Value / Comment", "Manufacturer", "Manufacturer Part No", "PCB Footprint", "Description", "LCSC Part #"])
        for i, ((mpn, fp), r) in enumerate(out, 1):
            d = sorted(r["des"], key=_nat)
            w.writerow([i, ", ".join(d), len(d), r["comment"], r["mf"], mpn, fp, r["descr"], r["lcsc"]])
    with open(jlc, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (mpn, fp), r in out:
            w.writerow([r["comment"] or mpn, ",".join(sorted(r["des"], key=_nat)), fp, r["lcsc"]])
    lines = [dict(des=sorted(r["des"], key=_nat), mpn=mpn, footprint=fp, comment=r["comment"], mf=r["mf"], lcsc=r["lcsc"]) for (mpn, fp), r in out]
    return full, jlc, lines


def cpl(prj, out_dir=None):
    """Pick-and-place file in JLCPCB format (mm, relative to the board origin = the Gerber origin)."""
    pcb = PCB(prj.pcbdoc)
    ox, oy = pcb.origin()
    out_dir = out_dir or os.path.join(prj.outputs_dir, "BOM")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s_CPL_JLCPCB_%s.csv" % (prj.name, datetime.date.today().isoformat()))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for c in sorted(pcb.components(), key=lambda c: _nat(c["des"])):
            w.writerow([c["des"], "%.4fmm" % ((c["x"] - ox) * 0.0254), "%.4fmm" % ((c["y"] - oy) * 0.0254),
                        "Top" if c["layer"].upper().startswith("TOP") else "Bottom", "%g" % (c["rot"] % 360)])
    return path


EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def render(prj, out_dir, width=3000):
    """Top/bottom PNG renders of the Gerber set (gerbonara -> SVG -> headless Edge)."""
    from pathlib import Path

    from gerbonara import LayerStack
    files = [Path(prj.fab_dir) / f for f in os.listdir(prj.fab_dir)
             if f.rsplit(".", 1)[-1].upper() in FAB_EXT | {prj.settings["outline_layer"].upper()}]
    st = LayerStack.from_files(files, autoguess=True)
    edge = next((e for e in EDGE if os.path.exists(e)), None)
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for side in ("top", "bottom"):
        svg = os.path.join(out_dir, "render_%s.svg" % side)
        open(svg, "w", encoding="utf-8").write(str(st.to_pretty_svg(side=side)))
        out[side] = svg
        if edge:
            page = os.path.join(out_dir, "render_%s.html" % side)
            open(page, "w").write("<html><body style='margin:0;background:#fff'><img src='%s' style='width:%dpx'></body></html>"
                                  % (os.path.basename(svg), width))
            png = os.path.join(out_dir, "render_%s.png" % side)
            subprocess.run([edge, "--headless", "--disable-gpu", "--screenshot=" + png, "--window-size=%d,%d" % (width, int(width * 1.15)),
                            Path(page).as_uri()], capture_output=True, timeout=120)
            if os.path.exists(png):
                _trim(png)
                out[side] = png
    return out


def _trim(png):
    from PIL import Image
    im = Image.open(png).convert("RGB")
    bb = im.convert("L").point(lambda v: 255 if v < 250 else 0).getbbox()
    if bb:
        im.crop(bb).save(png)
