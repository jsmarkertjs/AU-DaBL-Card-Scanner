# Adafruit USB Tower Light (#5125) — notes

- **Product:** Adafruit Tri-Color USB Controlled Tower Light with Buzzer, ID `5125`
  (<https://www.adafruit.com/product/5125>)
- **Interface:** USB-to-UART via **CH340** (WCH). USB `VID 0x1A86` / `PID 0x7523`.
- **Serial:** **9600 baud, 8-N-1**; send one raw byte per command.
- **On Windows:** shows as `USB-SERIAL CH340` (e.g. COM6); auto-detect by VID.
- **On the Pi:** `/dev/ttyUSB0` (ch341 kernel driver). User must be in the
  `dialout` group.

## Commands (one byte each)

| Color  | ON     | OFF    | BLINK  |
|--------|--------|--------|--------|
| Red    | `0x11` | `0x21` | `0x41` |
| Yellow | `0x12` | `0x22` | `0x42` |
| Green  | `0x14` | `0x24` | `0x44` |
| Buzzer | `0x18` | `0x28` | `0x48` |

## Do not use the buzzer

Driving the buzzer draws more current than the USB port supplies; the CH340
resets and drops off USB, and writes then fail with `Access is denied` until a
physical unplug/replug. The app never sends buzzer bytes.

## Mapping used by the app

| State | Light |
|-------|-------|
| idle / waiting | Yellow solid (`0x12`) |
| assignment graded | Green solid (`0x14`) |
| not graded / not found | Red solid (`0x11`) |
| Canvas / network error | Red blink (`0x41`) |

## Troubleshooting

- **No port found:** unplug/replug; re-scan for `VID 0x1A86`. Don't hardcode COM.
- **`Access is denied` on write:** device disconnected (likely buzzer); replug.
- **Pi grabs the port elsewhere:** `brltty` or `ModemManager` can claim CH340
  serial devices. Remove/mask them if the port is unstable:
  `sudo systemctl mask brltty ModemManager` (or `sudo apt remove brltty`).
