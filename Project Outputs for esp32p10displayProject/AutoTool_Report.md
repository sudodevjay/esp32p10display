# esp32p10displayProject - AutoTool report

Generated 2026-10-19 15:14

## Verdict: NOT READY

- schematic and PCB netlists differ (6)
- 36 copper clearance violations
- Altium DRC: 51 violations

## Checks

| Check | Result |
|---|---|
| Netlist schematic vs PCB | 6 differences |
| Copper clearance (own) | 36 violations |
| Open nets (own) | 0 |
| Board edge (own) | 0 |
| Track ends touching nothing | 0 (warning) |
| Pads without a net | 14 (warning) |
| Altium DRC | 51 violations (19-10-2026 15:14:05) |
| Altium ERC | 0 errors, 13 warnings (19-10-2026 15:14:07) |

### Netlist differences

- J9-: schematic <no pin>, PCB -
- J9-7: schematic <no pin>, PCB -
- U7-12: schematic <no pin>, PCB -
- U7-13: schematic <no pin>, PCB -
- U7-46: schematic <no pin>, PCB -
- U7-47: schematic <no pin>, PCB -

### Clearance violations

- clearance 8.7 < 10 mil: pad U7-48 <-> pad U7-47
- clearance 8.7 < 10 mil: pad U7-43 <-> pad U7-42
- clearance 8.7 < 10 mil: pad U7-38 <-> pad U7-37
- clearance 8.7 < 10 mil: pad U7-36 <-> pad U7-35
- clearance 8.7 < 10 mil: pad U7-35 <-> pad U7-34
- clearance 8.7 < 10 mil: pad U7-34 <-> pad U7-33
- clearance 8.7 < 10 mil: pad U7-33 <-> pad U7-32
- clearance 8.7 < 10 mil: pad U7-32 <-> pad U7-31
- clearance 8.7 < 10 mil: pad U7-31 <-> pad U7-30
- clearance 8.7 < 10 mil: pad U7-30 <-> pad U7-29
- clearance 8.7 < 10 mil: pad U7-29 <-> pad U7-28
- clearance 8.7 < 10 mil: pad U7-28 <-> pad U7-27
- clearance 8.7 < 10 mil: pad U7-27 <-> pad U7-26
- clearance 8.7 < 10 mil: pad U7-26 <-> pad U7-25
- clearance 8.7 < 10 mil: pad U7-24 <-> pad U7-23
- clearance 8.7 < 10 mil: pad U7-23 <-> pad U7-22
- clearance 8.7 < 10 mil: pad U7-22 <-> pad U7-21
- clearance 8.7 < 10 mil: pad U7-21 <-> pad U7-20
- clearance 8.7 < 10 mil: pad U7-20 <-> pad U7-19
- clearance 8.7 < 10 mil: pad U7-19 <-> pad U7-18
- clearance 8.7 < 10 mil: pad U7-18 <-> pad U7-17
- clearance 8.7 < 10 mil: pad U7-17 <-> pad U7-16
- clearance 8.7 < 10 mil: pad U7-16 <-> pad U7-15
- clearance 8.7 < 10 mil: pad U7-15 <-> pad U7-14
- clearance 8.7 < 10 mil: pad U7-14 <-> pad U7-13
- clearance 8.7 < 10 mil: pad U7-12 <-> pad U7-11
- clearance 8.7 < 10 mil: pad U7-11 <-> pad U7-10
- clearance 8.7 < 10 mil: pad U7-10 <-> pad U7-9
- clearance 8.7 < 10 mil: pad U7-9 <-> pad U7-8
- clearance 8.7 < 10 mil: pad U7-8 <-> pad U7-7
- clearance 8.7 < 10 mil: pad U7-7 <-> pad U7-6
- clearance 8.7 < 10 mil: pad U7-6 <-> pad U7-5
- clearance 8.7 < 10 mil: pad U7-5 <-> pad U7-4
- clearance 8.7 < 10 mil: pad U7-4 <-> pad U7-3
- clearance 8.7 < 10 mil: pad U7-3 <-> pad U7-2
- clearance 8.7 < 10 mil: pad U7-2 <-> pad U7-1

### Pads without a net

- J9-
- J9-
- J9-7
- U7-47
- U7-46
- U7-45
- U7-44
- U7-43
- U7-26
- U7-24
- U7-18
- U7-13
- U7-12
- U7-7

### Altium ERC warnings

- Warning esp32p10displaySheet1.SchDoc ETH_MISO contains IO Pin and Output Pin objects (Pin U1-8, Pin U7-34).
- Warning esp32p10displaySheet1.SchDoc Net +5 has no driving source (Pin C3-P, Pin C5-2, Pin D7-C, Pin D9-C, Pin K1-COIL1, Pin PS1-4, Pin PS2-2, Pin Q3-2, Pin VR1-3)
- Warning esp32p10displaySheet1.SchDoc Net AC_L_PS has no driving source (Pin C16-1, Pin PS1-2, Pin RT1-2)
- Warning esp32p10displaySheet1.SchDoc Net EN has no driving source (Pin C9-1, Pin Q2-3, Pin R1-2, Pin U1-3)
- Warning esp32p10displaySheet1.SchDoc Net ETH_INT has no driving source (Pin R21-1, Pin U1-4, Pin U7-36)
- Warning esp32p10displaySheet3.SchDoc Net ETH_RXN has no driving source (Pin C31-2, Pin R27-1, Pin U7-5)
- Warning esp32p10displaySheet3.SchDoc Net ETH_RXP has no driving source (Pin C30-2, Pin R26-1, Pin U7-6)
- Warning esp32p10displaySheet3.SchDoc Net ETH_XI has no driving source (Pin C32-1, Pin U7-30, Pin Y1-2)
- Warning esp32p10displaySheet1.SchDoc Net IN1 has no driving source (Pin C17-1, Pin R18-1, Pin U1-6, Pin U5-4)
- Warning esp32p10displaySheet1.SchDoc Net IN2 has no driving source (Pin C18-1, Pin R19-1, Pin U1-7, Pin U6-4)
- Warning esp32p10displaySheet2.SchDoc Net NetD5_A has no driving source (Pin D5-A, Pin R8-1, Pin U2-11)
- Warning esp32p10displaySheet1.SchDoc Off grid Net Label AC_N at 1165.476mil,8700mil
- Warning esp32p10displaySheet2.SchDoc Off grid Net Label DI at 3706.922mil,5500mil
