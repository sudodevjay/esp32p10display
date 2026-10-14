# ESP32 P10 Display Controller (Altium Designer)

2-layer controller board for a P10 / HUB75-style LED panel:

- ESP32-WROOM-32, auto-reset / auto-boot from an FT232 USB-UART (DTR/RTS)
- 230 VAC input: T1A fuse, MOV, NTC inrush limiter, X2 cap, HLK-10M05 (5 V) or 5 V DC terminal with reverse-polarity protection
- LM1117-3.3 regulator, power LED
- Isolated RS485 (ADuM1201 + RFM-0505S + SN65176B) with automatic direction control, TVS/PTC line protection

## Files

| File | |
|---|---|
| `esp32p10displayProject.PrjPcb` | Altium project |
| `esp32p10displaySheet1.SchDoc`, `esp32p10displaySheet2.SchDoc` | schematics (main, isolated RS485) |
| `esp32p10displayPCB1.PcbDoc` | PCB, 95.5 x 105.4 mm |
| `Project Outputs for esp32p10displayProject/esp32p10display_Gerber_2026-10-14.zip` | Gerber + drill for the fab (JLCPCB) |
| `Project Outputs .../BOM/` | BOM, JLCPCB BOM + CPL |

## Notes for use

- RS485: at most one 120 ohm termination on the bus, baud rate up to 38400.
- Do not connect mains and the 5 V DC input at the same time.
- First power-up: check 5 V, 3V3 and ISO_5V (<= 5.5 V) before fitting the ESP32.

Checks and outputs were produced with the `altium_autotool` branch of this repository.
