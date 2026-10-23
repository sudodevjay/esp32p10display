# ESP32 P10 Display Controller (Altium Designer)

2-layer controller board for a P10 / HUB75-style LED panel:

- ESP32-WROOM-32, auto-reset / auto-boot from an FT232 USB-UART (DTR/RTS)
- 230 VAC input: T1A fuse, MOV, NTC inrush limiter, X2 cap, HLK-10M05 (5 V) or 5 V DC terminal with reverse-polarity protection
- LM1117-3.3 regulator (with heatsink copper), power LED
- Isolated RS485 (ADuM1201 + RFM-0505S + SN65176B) with automatic direction control, TVS/PTC line protection
- Relay output (SRD-05VDC, NO/COM/NC terminal) on GPIO26, two opto-isolated contact inputs on GPIO34 / GPIO35
- 10/100 Ethernet: W5500 + HR911105A MagJack

## Files

| File | |
|---|---|
| `esp32p10displayProject.PrjPcb` | Altium project |
| `esp32p10displaySheet1.SchDoc`, `esp32p10displaySheet2.SchDoc`, `esp32p10displaySheet3.SchDoc` | schematics (main, isolated RS485 + inputs, Ethernet) |
| `esp32p10displayPCB1.PcbDoc` | PCB, 95.5 x 153.5 mm |
| `Project Outputs for esp32p10displayProject/esp32p10display_Gerber_2026-10-19.zip` | Gerber + drill for the fab (JLCPCB) |
| `Project Outputs .../BOM/` | BOM, JLCPCB BOM + CPL |
| `Project Outputs .../Simulation/` | ngspice results: power, relay, RS485, inputs, LAN, SPI |

## Firmware pins

| Function | GPIO |
|---|---|
| P10 A / B / C / LAT / OE / CLK / R1 | 19 / 23 / 18 / 5 / 4 / 14 / 13 |
| RS485 TX / RX | 16 / 17 |
| W5500 SCLK / MOSI / MISO / CS / INT / RST | 2 / 33 / 32 / 27 / 36 / 15 |
| Relay | 26 |
| Input 1 / Input 2 (active low) | 34 / 35 |

## Notes for use

- RS485: at most one 120 ohm termination on the bus, baud rate up to 38400.
- Do not connect mains and the 5 V DC input at the same time.
- First power-up: check 5 V, 3V3 and ISO_5V (<= 5.5 V) before fitting the ESP32.
- W5500 SPI: 47 ohm series resistors R39 (SCLK), R40 (MISO), R41 (MOSI), R42 (CS) damp the ringing; no firmware change is needed (SPI up to 20 MHz at the default drive strength).
- Check the VR1 (LM1117) temperature with WiFi and Ethernet both active; above ~90 C a buck regulator is the next step.

Checks and outputs were produced with the `altium_autotool` branch of this repository.

## Opening the project on another computer

1. Install Altium Designer 24 (or newer; built with 24.5.2) and `git clone` this repository.
2. Open `esp32p10displayProject.PrjPcb`. All component libraries this design uses are in `Libraries/` and are already attached to the project (Projects panel > Libraries), so symbols, footprints and 3D models resolve without any extra setup.
3. 18 parts originally came from a private database library ("Celestial Altium Library"). That database is not included; those parts keep their symbol and footprint inside the schematic and PCB, so everything works. Just do not run "Update from database" on them.
4. To order the board you do not need Altium: use `Project Outputs for esp32p10displayProject/` (Gerber zip, `BOM/` with BOM, JLCPCB BOM + CPL, and `Component_Order_List_*.xlsx` with the purchase list).
