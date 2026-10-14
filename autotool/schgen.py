"""Phase 5: create a new Altium project from a design spec (JSON).

Spec (all coordinates in mil):
{
  "name": "LedBoard",
  "dir":  "D:\\pcb\\PCB_Project\\LedBoard",
  "board": {"width": 1600, "height": 1000, "clearance": 10},
  "parts": [
    {"des": "J1", "ref": "TBP02R138102BE", "lib": "<IntLib/SchLib path>", "comment": "5V IN",
     "pcb": {"x": 150, "y": 500, "rot": 0, "fixed": true}},          # optional PCB placement hint
    {"des": "R1", "ref": "ERA6VRB1002V", "lib": "...", "comment": "330R", "params": {"MP": "..."}}
  ],
  "nets": {"+5": ["J1.1", "R1.1"], "GND": ["J1.2", "D1.C"], "LED_A": ["R1.2", "D1.A"]},
  "net_widths": {"+5": 20, "GND": 20},
  "pour": {"net": "GND", "layer": "bottom"}
}
Parts must exist in the library catalog (python -m autotool lib-index). The schematic is drawn as a grid of parts,
every connected pin gets a short wire and a net label (unconnected pins get a No-ERC marker), so the netlist is exact.
After this step, open the project in Altium and run Design > Update PCB Document (review the ECO, Execute Changes),
then run 'python -m autotool layout' to place and route the board.
"""
import json
import os
import shutil

from . import altium, libindex, outputs, project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLANK = os.path.join(ROOT, "templates", "blank")
STUB = 200      # wire length from pin end to the net label (mil)
GRID = 100


def _q(s):
    return str(s).replace("'", "''")


def validate(spec, cat):
    """Resolve every part in the catalog and check every net member names a real pin. Returns (parts, errors)."""
    errs, parts = [], {}
    by = {}
    for c in cat:
        by.setdefault(c["libref"], []).append(c)
    for p in spec["parts"]:
        cands = [c for c in by.get(p["ref"], []) if not p.get("lib") or os.path.normcase(c["library"]) == os.path.normcase(p["lib"])]
        if not cands:
            errs.append("%s: '%s' not found in the library catalog%s" % (p["des"], p["ref"], " (" + p["lib"] + ")" if p.get("lib") else ""))
            continue
        parts[p["des"]] = dict(p, lib=cands[0]["library"], cat=cands[0])
    seen = {}
    for net, members in spec["nets"].items():
        for m in members:
            des, _, pin = m.partition(".")
            if des not in parts:
                if des not in [p["des"] for p in spec["parts"]]:
                    errs.append("net %s: unknown part %s" % (net, des))
                continue
            pins = {x["des"] for x in parts[des]["cat"]["pins"]}
            if pin not in pins:
                errs.append("net %s: %s has no pin '%s' (pins: %s)" % (net, des, pin, ", ".join(sorted(pins))))
            if m in seen:
                errs.append("pin %s is in two nets: %s and %s" % (m, seen[m], net))
            seen[m] = net
    return parts, errs


def _bbox(c):
    xs, ys = [0], [0]
    for p in c["pins"]:
        dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][p["orient"]]
        xs += [p["x"], p["x"] + dx * p["length"]]
        ys += [p["y"], p["y"] + dy * p["length"]]
    return min(xs), min(ys), max(xs), max(ys)


def layout_sheet(parts):
    """Simple row packing: connectors first, then ICs, then the rest; leaves room for stubs + labels."""
    order = sorted(parts.values(), key=lambda p: (0 if p["des"][0] == "J" else 1 if p["des"][0] == "U" else 2, p["des"]))
    x, y, row_h, pos = 1000, 7000, 0, {}
    for p in order:
        x0, y0, x1, y1 = _bbox(p["cat"])
        w, h = x1 - x0 + 2 * (STUB + 900), y1 - y0 + 2 * (STUB + 300)
        if x + w > 15000:
            x, y, row_h = 1000, y - row_h, 0
        px = (x - x0 + STUB + 900) // GRID * GRID
        py = (y - y1 - STUB - 300) // GRID * GRID
        pos[p["des"]] = (px, py)
        x += w
        row_h = max(row_h, h)
    return pos


def build(spec_path, log=print):
    spec = json.load(open(spec_path, encoding="utf-8"))
    cat = libindex.load()
    parts, errs = validate(spec, cat)
    if errs:
        for e in errs:
            log("  ERROR " + e)
        raise SystemExit("spec has %d errors" % len(errs))
    d, name = spec["dir"], spec["name"]
    os.makedirs(d, exist_ok=True)
    sch, pcb, prj, oj = (os.path.join(d, name + ext) for ext in (".SchDoc", ".PcbDoc", ".PrjPcb", ".OutJob"))
    for f in (sch, pcb, prj):
        if os.path.exists(f):
            raise SystemExit("%s exists already: choose a new name/dir (nothing is overwritten)" % f)
    shutil.copy(os.path.join(BLANK, "Blank.SchDoc"), sch)
    shutil.copy(os.path.join(BLANK, "Blank.PcbDoc"), pcb)
    open(prj, "w", encoding="latin1", newline="\r\n").write(
        "[Design]\nVersion=1.0\nHierarchyMode=0\nChannelRoomNamingStyle=0\nOutputPath=Project Outputs for %s\n\n"
        "[Document1]\nDocumentPath=%s\n\n[Document2]\nDocumentPath=%s\n\n[Document3]\nDocumentPath=%s\n"
        % (name, os.path.basename(sch), os.path.basename(pcb), os.path.basename(oj)))
    p = project.load(prj)
    open(oj, "w", encoding="latin1", newline="\r\n").write(outputs.new_outjob_text(p).replace("\r\n", "\n"))
    cfg = {"clearance_mil": spec.get("board", {}).get("clearance", 10), "net_classes": spec.get("net_classes", []),
           "exempt_components": spec.get("exempt_components", []), "outline_layer": "GM1"}
    json.dump(cfg, open(os.path.join(d, "autotool.json"), "w"), indent=1)
    json.dump(spec, open(os.path.join(d, "design_spec.json"), "w"), indent=1)

    pos = layout_sheet(parts)
    pin_net = {}
    for net, members in spec["nets"].items():
        for m in members:
            pin_net[m] = net
    lines = []
    for des, part in parts.items():
        x, y = pos[des]
        lines.append("  PlacePart('%s', '%s', '%s', '%s', %d, %d);" % (_q(part["ref"]), _q(part["lib"]), _q(des), _q(part.get("comment", "")), x, y))
        for k, v in (part.get("params") or {}).items():
            lines.append("  SetParam('%s', '%s', '%s');" % (_q(des), _q(k), _q(v)))
    conn = "\n".join("  Conn.Add('%s=%s');" % (_q(m), _q(n)) for m, n in pin_net.items())
    body = SCRIPT % dict(prj=prj, sch=sch, place="\n".join(lines), connlist=conn)
    log("creating schematic with %d parts and %d nets ..." % (len(parts), len(spec["nets"])))
    out = altium.run_script(body, timeout=600)
    for l in out:
        if l.startswith("!!") or "WARN" in l:
            log("  " + l)
    from .schfile import netlist
    got = netlist([sch])
    want = {}
    for m, n in pin_net.items():
        des, _, pin = m.partition(".")
        want.setdefault(des, {})[pin] = n
    bad = []
    for des, pins in want.items():
        for pin, n in pins.items():
            g = got.get(des, {}).get(pin)
            if g != n:
                bad.append("%s.%s: want %s, schematic has %s" % (des, pin, n, g))
    log("schematic netlist check: %s" % ("OK, matches the spec" if not bad else "%d mismatches" % len(bad)))
    for b in bad[:20]:
        log("  " + b)
    return dict(project=prj, schdoc=sch, pcbdoc=pcb, mismatches=bad, log=out)


SCRIPT = r"""
Var Doc : ISch_Document; Conn : TStringList;

Function Pt(X, Y : Integer) : TLocation;
Begin
  Result := Point(MilsToCoord(X), MilsToCoord(Y));
End;

Function FindComp(Des : String) : ISch_Component;
Var It : ISch_Iterator; C : ISch_Component;
Begin
  Result := Nil;
  It := Doc.SchIterator_Create; It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  While C <> Nil Do Begin
    If C.Designator.Text = Des Then Result := C;
    C := It.NextSchObject;
  End;
  Doc.SchIterator_Destroy(It);
End;

Procedure PlacePart(Ref, Lib, Des, Com : String; X, Y : Integer);
Var C : ISch_Component;
Begin
  C := SchServer.LoadComponentFromLibrary(Ref, Lib);
  If C = Nil Then Begin L('!! cannot load ' + Ref + ' from ' + Lib); Exit; End;
  Doc.RegisterSchObjectInContainer(C);
  C.MoveToXY(MilsToCoord(X), MilsToCoord(Y));
  C.Designator.Text := Des;
  If Com <> '' Then C.Comment.Text := Com;
  SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, C.I_ObjectAddress);
  L('placed ' + Des);
End;

Procedure SetParam(Des, Nm, V : String);
Var C : ISch_Component; It : ISch_Iterator; P : ISch_Parameter; Found : Boolean;
Begin
  C := FindComp(Des); Found := False;
  It := C.SchIterator_Create; It.AddFilter_ObjectSet(MkSet(eParameter));
  P := It.FirstSchObject;
  While P <> Nil Do Begin
    If P.Name = Nm Then Begin P.Text := V; Found := True; End;
    P := It.NextSchObject;
  End;
  C.SchIterator_Destroy(It);
  If Not Found Then Begin
    P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    P.Name := Nm; P.Text := V; P.IsHidden := True;
    C.AddSchObject(P);
  End;
End;

Function NetOf(Key : String) : String;
Var i : Integer;
Begin
  Result := '';
  i := Conn.IndexOfName(Key);
  If i >= 0 Then Result := Conn.ValueFromIndex(i);
End;

Procedure Wire(X1, Y1, X2, Y2 : Integer);
Var W : ISch_Wire;
Begin
  W := SchServer.SchObjectFactory(eWire, eCreate_Default);
  W.Location := Pt(X1, Y1);
  W.InsertVertex := 1; W.SetState_Vertex(1, Pt(X1, Y1));
  W.InsertVertex := 2; W.SetState_Vertex(2, Pt(X2, Y2));
  Doc.RegisterSchObjectInContainer(W);
  SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, W.I_ObjectAddress);
End;

Procedure ConnectPins;
Var CIt, PIt : ISch_Iterator; C : ISch_Component; P : ISch_Pin; X, Y, X2, Y2, Lg, O : Integer; Nm : String;
    NL : ISch_NetLabel; NE : ISch_NoERC;
Begin
  CIt := Doc.SchIterator_Create; CIt.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := CIt.FirstSchObject;
  While C <> Nil Do Begin
    PIt := C.SchIterator_Create; PIt.AddFilter_ObjectSet(MkSet(ePin));
    P := PIt.FirstSchObject;
    While P <> Nil Do Begin
      Lg := CoordToMils(P.PinLength); O := P.Orientation;
      X := CoordToMils(P.Location.X); Y := CoordToMils(P.Location.Y);
      If O = eRotate0 Then X := X + Lg Else If O = eRotate90 Then Y := Y + Lg
      Else If O = eRotate180 Then X := X - Lg Else Y := Y - Lg;
      Nm := NetOf(C.Designator.Text + '.' + P.Designator);
      If Nm = '' Then Begin
        NE := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
        NE.Location := Pt(X, Y);
        Doc.RegisterSchObjectInContainer(NE);
      End Else Begin
        X2 := X; Y2 := Y;
        If O = eRotate0 Then X2 := X + %(stub)d Else If O = eRotate90 Then Y2 := Y + %(stub)d
        Else If O = eRotate180 Then X2 := X - %(stub)d Else Y2 := Y - %(stub)d;
        Wire(X, Y, X2, Y2);
        NL := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
        NL.Location := Pt(X2, Y2); NL.Text := Nm;
        If (O = eRotate90) Or (O = eRotate270) Then NL.Orientation := eRotate90;
        Doc.RegisterSchObjectInContainer(NL);
        SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, NL.I_ObjectAddress);
      End;
      P := PIt.NextSchObject;
    End;
    C.SchIterator_Destroy(PIt);
    C := CIt.NextSchObject;
  End;
  Doc.SchIterator_Destroy(CIt);
End;

Procedure RunAll;
Begin
  L('schgen start');
  Conn := TStringList.Create;
  Conn.CommaText := '';
  %(connlist)s
  OpenDoc('%(prj)s', 'Project'); OpenDoc('%(sch)s', 'Document');
  Doc := SchServer.GetSchDocumentByPath('%(sch)s');
  If Doc = Nil Then Begin L('!! no schematic'); Exit; End;
  Doc.SheetStyle := eSheetA3;
  SchServer.ProcessControl.PreProcess(Doc, '');
%(place)s
  ConnectPins;
  SchServer.ProcessControl.PostProcess(Doc, '');
  Doc.GraphicallyInvalidate;
  ResetParameters; AddStringParameter('ObjectKind', 'Document'); AddStringParameter('FileName', '%(sch)s'); RunProcess('WorkspaceManager:SaveObject');
  ResetParameters; AddStringParameter('ObjectKind', 'Project'); AddStringParameter('FileName', '%(prj)s'); RunProcess('WorkspaceManager:SaveObject');
  L('saved');
  L('@@DONE');
End;
""".replace("%(stub)d", str(STUB))
