"""Altium project (.PrjPcb) discovery: documents, output folders and per-project tool settings."""
import configparser
import json
import os
from dataclasses import dataclass, field

X2_CANDIDATES = [
    r"C:\Program Files\Altium\AD24\X2.exe",
    r"C:\Program Files\Altium\AD23\X2.exe",
    r"C:\Program Files\Altium\AD22\X2.exe",
]

# Per-project settings live in autotool.json next to the PrjPcb. Everything is optional.
DEFAULT_SETTINGS = {
    "clearance_mil": 10,            # basic copper clearance (should match the board rule)
    "edge_clearance_mil": 15,       # copper to board outline
    "net_classes": [],              # [{"name": "MAINS", "nets": [...], "self_mil": 120, "other_mil": 240}]
    "exempt_components": [],        # components whose own pads may straddle class barriers (isolators, DC-DC)
    "exempt_pad_mil": 50,           # min gap from other-class copper to the pads of an exempt component
    "ignore_unconnected_pads": [],  # designators whose net-less pads are fine (NC pins, mounting holes)
    "outline_layer": "GM1",         # Gerber layer that carries the board outline for the fab
    "zip_name": "{prj}_Gerber_{date}.zip",
}


def find_x2():
    for p in X2_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Altium X2.exe not found; set X2 path in autotool/project.py")


@dataclass
class Project:
    prjpcb: str
    dir: str = ""
    name: str = ""
    schdocs: list = field(default_factory=list)
    pcbdoc: str = ""
    outjobs: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    @property
    def outputs_dir(self):
        return os.path.join(self.dir, "Project Outputs for " + self.name)

    @property
    def fab_dir(self):
        return os.path.join(self.outputs_dir, "Fabrication")

    @property
    def reports_dir(self):
        return os.path.join(self.outputs_dir, "Reports")


def load(prjpcb):
    prjpcb = os.path.abspath(prjpcb)
    p = Project(prjpcb=prjpcb, dir=os.path.dirname(prjpcb), name=os.path.splitext(os.path.basename(prjpcb))[0])
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    raw = open(prjpcb, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM written by Altium
        raw = raw[3:]
    cp.read_string(raw.decode("latin1"))
    for sec in cp.sections():
        if not sec.startswith("Document"):
            continue
        rel = cp[sec].get("DocumentPath", "")
        full = os.path.normpath(os.path.join(p.dir, rel))
        ext = os.path.splitext(rel)[1].lower()
        if ext == ".schdoc":
            p.schdocs.append(full)
        elif ext == ".pcbdoc":
            p.pcbdoc = full
        elif ext == ".outjob":
            p.outjobs.append(full)
    p.settings = dict(DEFAULT_SETTINGS)
    cfg = os.path.join(p.dir, "autotool.json")
    if os.path.exists(cfg):
        p.settings.update(json.load(open(cfg, encoding="utf-8")))
    return p
