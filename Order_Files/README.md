# Order files (everything in one place)

> **Parts list = P10-display-only build.** The PCB is the full board (same Gerber), but the purchase list and `Component_Order_List` cover only the parts needed to run the P10 display: ESP32 + P10 connector + 5V DC input (J5, from an external SMPS) + 3V3 + programming/auto-reset + PWR LED. The AC mains input (HLK etc.), RS485, Ethernet, relay, input and EEPROM parts are left unfitted (list on sheet 'Not fitted (DNP)'). `BOM_full.csv` is the BOM of the whole board.

| File | What it is |
|---|---|
| [esp32p10display_Gerber_2026-10-23.zip](esp32p10display_Gerber_2026-10-23.zip) | Gerber + drill, upload this to a PCB fab (JLCPCB: 2-layer, 95.5 x 153.52 mm, 1.6 mm) |
| [BOM_full.csv](BOM_full.csv) | Full bill of materials with designators |
| [BOM_JLCPCB.csv](BOM_JLCPCB.csv) and [CPL_JLCPCB.csv](CPL_JLCPCB.csv) | JLCPCB assembly BOM and pick-and-place file |
| [Component_Order_List.xlsx](Component_Order_List.xlsx) | Excel purchase list: component, pieces, website, quantity, price per piece, total (Sharvi + Robu) |
| [board_top.png](board_top.png), [board_bottom.png](board_bottom.png) | Renders of the finished board |

Design files (Altium): see the repository root, open `esp32p10displayProject.PrjPcb`.
