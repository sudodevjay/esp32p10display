"""Command line:
  python -m autotool check   <project.PrjPcb>   file-based checks only (Altium not needed)
  python -m autotool release <project.PrjPcb>   Altium DRC+ERC+Gerber/drill, then all checks, zip, BOM, CPL, renders, report
  python -m autotool source  <project.PrjPcb> --boards 5   price/stock compare, cheapest per part, carts, datasheets, PDF
  python -m autotool jlc-login                           sign in to JLCPCB once (visible browser, session is saved)
  python -m autotool jlc-quote <project.PrjPcb> --qty 5 [--color Green --thickness 1.6mm --finish "LeadFree HASL"] [--cart]
  python -m autotool lib-index                           catalog of D:\pcb\pcblibrary\Libraries (parts, pins, footprints)
  python -m autotool lib-search <words...>               find parts in the catalog
  python -m autotool new    <design_spec.json>           create project + schematic from a spec (then run the ECO in Altium)
  python -m autotool layout <project.PrjPcb> [--apply]   auto place + route (preview + check first; --apply writes the PCB)
"""
import argparse
import datetime
import os
import sys

from . import altium, checks, outputs, project
from .pcbfile import PCB
from .schfile import netlist


def run_checks(prj):
    pcb = PCB(prj.pcbdoc)
    r = {}
    r["netlist_diff"] = checks.compare_netlists(netlist(prj.schdocs), pcb.netlist())
    r["copper"] = checks.copper_checks(pcb, prj.settings)
    r["dangling"] = checks.dangling_track_ends(pcb)
    r["drc"] = checks.altium_drc(os.path.join(prj.reports_dir, os.path.splitext(os.path.basename(prj.pcbdoc))[0] + ".html"))
    r["erc"] = checks.altium_erc(os.path.join(prj.reports_dir, prj.name + ".html"))
    return r


def verdict(r):
    """List of blocking problems (empty = OK to order)."""
    bad = []
    if r["netlist_diff"]:
        bad.append("schematic and PCB netlists differ (%d)" % len(r["netlist_diff"]))
    if r["copper"]["violations"]:
        bad.append("%d copper clearance violations" % len(r["copper"]["violations"]))
    if r["copper"]["opens"]:
        bad.append("%d nets not fully connected" % len(r["copper"]["opens"]))
    if r["copper"]["edge"]:
        bad.append("%d copper items too close to / outside the board edge" % len(r["copper"]["edge"]))
    if r["drc"] is None:
        bad.append("no Altium DRC report")
    elif r["drc"]["total"]:
        bad.append("Altium DRC: %d violations" % r["drc"]["total"])
    if r["erc"] is None:
        bad.append("no Altium ERC report")
    elif r["erc"]["errors"]:
        bad.append("Altium ERC: %d errors" % len(r["erc"]["errors"]))
    return bad


def report(prj, r, extra, path):
    bad = verdict(r)
    c = r["copper"]
    L = ["# %s - AutoTool report" % prj.name, "", "Generated %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "",
         "## Verdict: %s" % ("READY TO ORDER" if not bad else "NOT READY"), ""]
    L += ["- " + b for b in bad] or ["- all checks passed"]
    L += ["", "## Checks", "", "| Check | Result |", "|---|---|",
          "| Netlist schematic vs PCB | %s |" % ("identical" if not r["netlist_diff"] else "%d differences" % len(r["netlist_diff"])),
          "| Copper clearance (own) | %d violations |" % len(c["violations"]),
          "| Open nets (own) | %d |" % len(c["opens"]),
          "| Board edge (own) | %d |" % len(c["edge"]),
          "| Track ends touching nothing | %d (warning) |" % len(r["dangling"]),
          "| Pads without a net | %d (warning) |" % len(c["pads_without_net"]),
          "| Altium DRC | %s |" % ("missing" if r["drc"] is None else "%s violations (%s)" % (r["drc"]["total"], r["drc"]["date"])),
          "| Altium ERC | %s |" % ("missing" if r["erc"] is None else "%d errors, %d warnings (%s)" % (len(r["erc"]["errors"]), len(r["erc"]["warnings"]), r["erc"]["date"]))]
    def sect(title, rows):
        if rows:
            L.extend(["", "### " + title, ""] + ["- " + str(x) for x in rows[:50]])
    sect("Netlist differences", r["netlist_diff"])
    sect("Clearance violations", ["%(kind)s %(dist)s < %(need)s mil: %(a)s <-> %(b)s" % v for v in c["violations"]])
    sect("Open nets", ["%s: %s" % kv for kv in c["opens"].items()])
    sect("Board edge", c["edge"])
    sect("Track ends touching nothing (check: may end inside a pour on another layer)", ["%(net)s L%(layer)s at %(at)s" % d for d in r["dangling"]])
    sect("Pads without a net", c["pads_without_net"])
    if r["drc"] and r["drc"]["total"]:
        sect("Altium DRC rules with violations", r["drc"]["rules"])
    if r["erc"]:
        sect("Altium ERC errors", [" ".join(m) for m in r["erc"]["errors"]])
        sect("Altium ERC warnings", [" ".join(m) for m in r["erc"]["warnings"]])
    if extra:
        L += ["", "## Files", ""] + ["- %s: `%s`" % kv for kv in extra.items()]
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return bad


def cmd_source(prj, a):
    from . import sourcing
    _, _, lines = outputs.bom(prj)
    print("sourcing %d BOM lines for %d board(s) ..." % (len(lines), a.boards))
    res, fx, active = sourcing.source(lines, boards=a.boards, spare_pct=a.spare,
                                      providers=[s for s in a.suppliers.split(",") if s] or None)
    out = sourcing.write_outputs(res, fx, active, os.path.join(prj.outputs_dir, "Sourcing"), prj.name, a.boards)
    # fill the LCSC numbers into the JLCPCB assembly BOM (exact LCSC matches only)
    lc = {}
    for r in res:
        o = [x for x in r["offers"] if x["supplier"] == "lcsc" and x["match"] == "exact" and x["total"] is not None]
        if o:
            lc[r["line"]["mpn"]] = min(o, key=lambda x: x["total"])["sku"]
    jlc = outputs.bom(prj, lcsc=lc)[1]
    found = sum(1 for r in res if r["best"])
    print("\nfound %d/%d lines | report %s" % (found, len(res), out["report_pdf"] or out["report_html"]))
    for s, (p, t, n) in out["carts"].items():
        print("  cart %-9s %2d lines  Rs %9.2f  %s" % (s, n, t, p))
    print("  JLCPCB BOM with LCSC numbers (%d lines): %s" % (len(lc), jlc))
    return 0


def cmd_jlc(prj, a):
    import glob
    import json
    from . import jlc
    z = a.zip or max(glob.glob(os.path.join(prj.outputs_dir, "*.zip")), key=os.path.getmtime, default="")
    if not z:
        sys.exit("no Gerber zip found: run 'release' first or pass --zip")
    print("JLCPCB quote for", z)
    r = jlc.run(jlc.quote(z, os.path.join(prj.outputs_dir, "JLCPCB"), qty=a.qty, cart=a.cart, headless=not a.show,
                          options={"PCB Color": a.color, "PCB Thickness": a.thickness, "Surface Finish": a.finish}))
    out = os.path.join(prj.outputs_dir, "JLCPCB", "quote_%s.json" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    json.dump(r, open(out, "w"), indent=1)
    print("\n%s | %d pcs | price $%s | %s" % (r["detected"], a.qty, r["price_usd"], r["shipping"]))
    print("screenshots:", ", ".join(r["screenshots"]))
    if a.cart:
        print("added to cart:", r["added_to_cart"], "- open jlcpcb.com > Cart to review and pay")
    return 0 if r["detected"] else 1


def cmd_layout(prj, a):
    import json
    from . import layout
    sp = os.path.join(prj.dir, "design_spec.json")
    spec = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    plan = layout.make_plan(prj, spec)
    out = os.path.join(prj.outputs_dir, "Layout")
    os.makedirs(out, exist_ok=True)
    json.dump({k: v for k, v in plan.items()}, open(os.path.join(out, "layout_plan.json"), "w"), indent=0)
    png = layout.preview(plan, os.path.join(out, "layout_preview.png"))
    viol, opens = layout.verify(plan)
    print("preview:", png)
    if plan["failed"] or plan["overlap_cells"] or viol or opens:
        print("plan NOT clean (failed nets %s, overlaps %d, clearance %d, opens %d): not applied" %
              (plan["failed"], plan["overlap_cells"], len(viol), len(opens)))
        return 1
    if not a.apply:
        print("plan is clean. Look at the preview, then run again with --apply to write it into the PCB.")
        return 0
    log = layout.apply(prj, plan)
    print("applied:", " | ".join(log[-4:]))
    print("next: python -m autotool release \"%s\"" % prj.prjpcb)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autotool")
    ap.add_argument("cmd", choices=["check", "release", "source", "jlc-login", "jlc-quote", "lib-index", "lib-search", "new", "layout"])
    ap.add_argument("prjpcb", nargs="?")
    ap.add_argument("words", nargs="*")
    ap.add_argument("--no-altium", action="store_true", help="release: skip running Altium (reuse existing outputs)")
    ap.add_argument("--boards", type=int, default=1, help="source: number of boards to buy parts for")
    ap.add_argument("--spare", type=int, default=10, help="source: extra parts in percent (min 1 piece for small qty)")
    ap.add_argument("--suppliers", default="", help="source: comma list, default all configured (lcsc,element14,mouser,digikey)")
    ap.add_argument("--qty", type=int, default=5, help="jlc-quote: PCB quantity (one of JLC's preset values)")
    ap.add_argument("--color", default="Green")
    ap.add_argument("--thickness", default="1.6mm")
    ap.add_argument("--finish", default="HASL(with lead)", help='e.g. "HASL(with lead)", "LeadFree HASL", "ENIG"')
    ap.add_argument("--zip", default="", help="jlc-quote: Gerber zip (default: newest zip in Project Outputs)")
    ap.add_argument("--cart", action="store_true", help="jlc-quote: also SAVE TO CART (signed-in profile needed)")
    ap.add_argument("--show", action="store_true", help="jlc-quote: visible browser")
    ap.add_argument("--apply", action="store_true", help="layout: write the planned layout into the PcbDoc")
    a = ap.parse_args(argv)
    if a.cmd == "lib-index":
        from . import libindex
        libindex.index()
        return 0
    if a.cmd == "lib-search":
        from . import libindex
        for c in libindex.search(([a.prjpcb] if a.prjpcb else []) + a.words)[:40]:
            print("%-28s %-26s pins=%-3d %s | %s" % (c["libref"][:28], (c["footprints"] or ["-"])[0][:26], len(c["pins"]), c["description"][:50], c["library"]))
        return 0
    if a.cmd == "new":
        from . import schgen
        r = schgen.build(a.prjpcb)
        print("\nproject:", r["project"])
        print("next: open it in Altium, Design > Update PCB Document, review the ECO, Execute Changes, save;")
        print("      then: python -m autotool layout \"%s\"" % r["project"])
        return 0 if not r["mismatches"] else 1
    if a.cmd == "jlc-login":
        from . import jlc
        return 0 if jlc.run(jlc.login()) else 1
    if not a.prjpcb:
        ap.error("project .PrjPcb is required for " + a.cmd)
    prj = project.load(a.prjpcb)
    print("project:", prj.name, "| sheets:", len(prj.schdocs), "| pcb:", os.path.basename(prj.pcbdoc))
    extra = {}
    if a.cmd == "source":
        return cmd_source(prj, a)
    if a.cmd == "jlc-quote":
        return cmd_jlc(prj, a)
    if a.cmd == "layout":
        return cmd_layout(prj, a)
    if a.cmd == "release" and not a.no_altium:
        oj = outputs.pick_outjob(prj)
        if oj is None:
            sys.exit("This project has no OutJob with a Gerber output. Add templates/AutoTool.OutJob to the project in Altium "
                     "(Project > Add Existing), or generate one with outputs.new_outjob_text().")
        print("running Altium outputs via", os.path.basename(oj), "...")
        with outputs.outjob_with_erc(oj):
            log = altium.generate_outputs(prj.prjpcb, oj)
        print("  " + " | ".join(log[-3:]))
    r = run_checks(prj)
    if a.cmd == "release":
        z, files = outputs.gerber_zip(prj)
        full, jlc, _ = outputs.bom(prj)
        extra = {"Gerber zip (upload to fab)": z, "BOM": full, "BOM (JLCPCB format)": jlc, "CPL (JLCPCB format)": outputs.cpl(prj)}
        try:
            rd = outputs.render(prj, os.path.join(prj.outputs_dir, "Renders"))
            extra.update({"render " + k: v for k, v in rd.items()})
        except Exception as e:  # rendering is a convenience, never block a release on it
            print("render failed:", e)
    path = os.path.join(prj.outputs_dir, "AutoTool_Report.md")
    os.makedirs(prj.outputs_dir, exist_ok=True)
    bad = report(prj, r, extra, path)
    print("\nVERDICT:", "READY TO ORDER" if not bad else "NOT READY")
    for b in bad:
        print("  -", b)
    print("report:", path)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
