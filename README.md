# Altium AutoTool

Python tooling around Altium Designer (AD24) for taking a PCB project from design to an order-ready package.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Checks + fabrication outputs for an existing project | **done, tested** on esp32p10display |
| 2 | Component sourcing: LCSC (no key) + element14 / Mouser / DigiKey (keys), cheapest per part, carts, datasheets, PDF | **done**; LCSC tested, keyed suppliers untested until keys are added |
| 3 | JLCPCB: upload Gerber, set options, read price/shipping, save to cart (payment stays manual) | **done**; quote tested with a dummy board, cart step needs `jlc-login` |
| 4 | Auto placement + 2-layer routing (PathFinder + numba A*), GND pour, offline verify, apply in Altium | router **tested offline** on esp32p10display (0 clearance, 0 opens, 27 s); placement + Altium apply not run yet |
| 5 | Project + schematic from a JSON spec, parts from the central library catalog | written; first Altium run pending |

Every phase stops for a human OK before anything that costs money or is hard to undo.

## Usage

```
cd D:\pcb\altium_autotool
python -m autotool check   "C:\...\MyProject.PrjPcb"          # file checks only, ~1 s, Altium not needed
python -m autotool release "C:\...\MyProject.PrjPcb"          # Altium DRC+ERC+Gerber/drill, then everything below
python -m autotool release "C:\...\MyProject.PrjPcb" --no-altium   # reuse existing Altium outputs
python -m autotool source  "C:\...\MyProject.PrjPcb" --boards 5     # cheapest supplier per part, carts, datasheets, PDF
python -m autotool jlc-login                                   # once: sign in to JLCPCB in a visible browser
python -m autotool jlc-quote "C:\...\MyProject.PrjPcb" --qty 5 --color Green [--cart]
python -m autotool lib-index                                   # rebuild the central library catalog
python -m autotool lib-search ams1117                          # find parts (lib ref, footprint, pins)
python -m autotool new     design_spec.json                    # new project + schematic (format: autotool/schgen.py)
python -m autotool layout  "C:\...\MyProject.PrjPcb" [--apply] # auto place + route; preview + check before --apply
```

Full flow for a new board: write `design_spec.json` -> `new` -> in Altium: Design > Update PCB Document, review the ECO,
Execute Changes, save -> `layout` (look at the preview) -> `layout --apply` -> `release` -> `source` -> `jlc-quote --cart`.

Keys for phase 2 go in `keys.json` (copy `keys.example.json`); keep that file private.
Router limitation for now: one clearance for all nets (net classes such as mains spacing are checked by `check`, not
yet obeyed by the router), 2 layers only.

`release` writes into `Project Outputs for <project>\`:

- `AutoTool_Report.md`: verdict (READY TO ORDER / NOT READY) and every finding
- `<zip_name>`: Gerber + drill set for the fab (copper, mask, paste, silk, drill, outline layer only; no GKO)
- `BOM\<prj>_BOM_<date>.csv`: grouped BOM (designators, qty, value, manufacturer, MPN, PCB footprint, LCSC #)
- `BOM\<prj>_BOM_JLCPCB_<date>.csv` and `BOM\<prj>_CPL_JLCPCB_<date>.csv`: JLCPCB assembly format
- `Renders\render_top.png`, `render_bottom.png`: Gerber renders for an eyes-on check

## What is checked

| Check | Source |
|---|---|
| Schematic netlist == PCB netlist (pin by pin) | own parser of SchDoc + PcbDoc |
| Copper clearance, incl. net-class barriers (mains, isolated side) | own geometry check (shapely) |
| Open nets (pads of one net not joined by copper, pours included) | own |
| Track ends that touch nothing | own |
| Copper near / outside the board outline | own |
| Altium DRC (all board rules) | Altium report |
| Altium ERC | Altium report (an ERC output is added to the OutJob for the run, then the file is restored) |

## Per-project settings: `autotool.json` next to the `.PrjPcb`

```json
{
  "clearance_mil": 10,
  "net_classes": [
    {"name": "MAINS", "nets": ["AC_L", "AC_N"], "self_mil": 100, "other_mil": 240},
    {"name": "ISO", "nets": ["ISO_5V", "ISO_GND"], "self_mil": 10, "other_mil": 60}
  ],
  "exempt_components": ["U4", "PS2"],
  "exempt_pad_mil": 50,
  "ignore_unconnected_pads": ["MH1", "MH2"],
  "outline_layer": "GM1",
  "zip_name": "{prj}_Gerber_{date}.zip"
}
```

## Requirements

- Altium Designer AD24 (X2.exe path in `autotool/project.py`); Altium may be open or closed
- The project needs an OutJob with a Gerber + NC Drill output (Fabrication medium) and DRC (Reports medium).
  `templates/AutoTool.OutJob` is a ready one: copy it next to the project and add it in Altium (Project > Add Existing).
- Python 3.11 with `olefile shapely gerbonara pillow` (and `playwright` for phase 3)

## Phase 2/3 notes

- element14, Mouser and DigiKey give free API keys (developer accounts on each site). LCSC has no official API.
- JLCPCB ordering through their official API needs an approved account; otherwise the tool will drive the website
  in a browser where you are logged in. Payment is never automated.

## Layout

```
autotool/
  project.py   PrjPcb discovery + settings
  pcbfile.py   PcbDoc parser (pads, tracks, vias, arcs, fills, pours, components)
  schfile.py   SchDoc parser (parts + parameters, multi-sheet netlist)
  checks.py    netlist compare, clearance/opens/edge, dangling ends, Altium DRC/ERC report parsing
  altium.py    DelphiScript runner (X2.exe -RScriptingSystem:RunScript)
  outputs.py   OutJob handling, Gerber zip, BOM, CPL, renders
  cli.py       commands + report
templates/AutoTool.OutJob
work/          generated scripts and logs
```
