"""Offline router test: keep the esp32p10display placement, re-route everything, verify. Nothing is written."""
import time, sys
from shapely.geometry import Polygon
from autotool.pcbfile import PCB
from autotool import layout
p = PCB(r"C:\Users\Public\Documents\Altium\esp32p10displayProject\esp32p10displayPCB1.PcbDoc")
fps = layout.footprints(p)
pads = []
for d, f in fps.items():
    for q in layout.placed_pads(f, f["x"], f["y"], f["rot"]):
        pads.append(dict(q, des=d))
board = Polygon(p.outline())
W = {'NetF3_1': 40, 'AC_L_F': 40, 'AC_L_PS': 40, 'AC_N': 40, 'GND': 20, '+5': 20, '3V3': 20, 'ISO_5V': 20, 'ISO_GND': 20}
t = time.time()
r = layout.route(board, pads, W, clearance=10, G=5, pour={"net": "GND", "layer": "bottom"}, iters=int(sys.argv[1]) if len(sys.argv) > 1 else 25)
print("time %.0fs tracks %d vias %d failed %s overlap %d stitched %d" % (time.time() - t, len(r["tracks"]), len(r["vias"]), r["failed"], r["overlap_cells"], len(r["stitched"])))
plan = dict(board=list(board.exterior.coords), pads=pads, tracks=r["tracks"], vias=r["vias"], pour={"net": "GND", "layer": "bottom"}, clearance=10)
layout.verify(plan)
import pickle; pickle.dump(plan, open("work/esp32_route_test.pkl", "wb"))
