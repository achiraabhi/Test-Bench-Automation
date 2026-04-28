---
name: New instrument request
about: Request support for an instrument not yet in the library
labels: enhancement
---

**Instrument details**
- Manufacturer:
- Model:
- Interface (USB-TMC / RS-232 / GPIB / LAN):
- SCPI standard compliance (yes / partial / no):

**VISA resource string example**
```
e.g. GPIB0::24::INSTR
```

**Key SCPI commands needed**
| Purpose | Command |
|---|---|
| Enter remote mode | |
| Configure measurement | |
| Trigger / read | |
| Return to local | |

**Serial settings (RS-232 only)**
- Baud rate:
- Data bits / parity / stop bits:
- Flow control:
- Line termination:

**Additional context**
Any quirks, timing requirements, or non-standard behaviour worth noting.
