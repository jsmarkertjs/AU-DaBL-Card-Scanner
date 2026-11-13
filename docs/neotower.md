# Adafruit NeoPixel tower (36 LEDs) — wiring notes

- **LEDs:** 4 NeoPixel (WS2812/WS2811) strips of **9 LEDs each**, chained into a
  single data line → **36 LEDs**.
- **Sections:** within each 9-LED strip the thirds are **green (0-2)**,
  **yellow (3-5)**, **red (6-8)**. By global index `i`, the color is decided by
  `i % 9` (so every strip repeats the green/yellow/red pattern):
  `0-2` green, `3-5` yellow, `6-8` red.
- **Driven directly by the Pi** (no extra board) with `rpi_ws281x` +
  `adafruit-circuitpython-neopixel` (via Blinka).
- **Color order:** `GRB` (the WS2812 default). **Brightness:** `80`.

## Wiring — strip → Raspberry Pi 3 B+

| Strip wire | Raspberry Pi pin | Notes |
|------------|------------------|-------|
| **Din** (data-in) | **GPIO18** — physical pin 12 | WS2812 data |
| **5V** | 5V — physical pin 2 (or 4) | power |
| **GND** | GND — physical pin 6 | common ground |

Recommended on the data line:
- a **~470Ω resistor in series** with Din, right at the first LED;
- a **~1000µF capacitor** across 5V ↔ GND near the strip.

If the first pixel flickers or shows wrong colors, add a **3.3V→5V level shifter**
(74AHCT125) — WS2812 wants 5V logic and the Pi outputs 3.3V. Short wires often
work without one.

### Power

36 LEDs at brightness 80 can pull **~0.7A** if everything goes white. Run the Pi
from a **5V/3A supply**; a weak supply will brown out (the Pi reboots or the top
LEDs dim). Keep `brightness = 80` in the config.

> **Audio:** the NeoPixel driver uses the Pi's PWM/DMA, which is shared with the
> 3.5mm jack. Use a **USB speaker** so sound doesn't crackle — the app already
> prefers a USB audio card (see the README).

## States (same key as the USB tower)

| State | NeoPixel tower |
|-------|----------------|
| Idle | **rainbow flow** — the three sections cycle the color wheel 1/3 (120°) apart, ~8s per full cycle |
| Fully Cleared | **whole tower green** |
| Waiver Signed – Missing Quiz | **whole tower** alternating green / yellow |
| Quiz Passed – Missing Waiver | **whole tower** alternating yellow / red |
| Incomplete – No Progress | **whole tower red** |
| Unregistered / Canvas error | **whole tower blinking red** |

Result states light the **whole tower** in one color, exactly like the USB tower
light; the green/yellow/red sections are only used for the idle rainbow. The USB
tower runs in parallel (idle = off). Both are driven by `TowerSet` in
`card_reader.py`.

## Config

```ini
[neotower]
enabled = true
pin = D18             # Blinka name or BCM number
count = 36
leds_per_strip = 9    # 3 green, 3 yellow, 3 red per strip
brightness = 80       # 0-255
order = GRB           # strip color order (use RGB if red/green come out swapped)
idle_seconds = 8      # duration of one full rainbow cycle
```

## Requirements / running as root

`rpi_ws281x` pokes the PWM/DMA hardware through `/dev/mem`, so the NeoPixel code
**must run as root**. The systemd service is installed with `User=root`
(`deploy/au-card-reader.service`); `deploy/install.sh` pulls in
`rpi_ws281x` + `adafruit-circuitpython-neopixel`.

## Test

```bash
sudo python3 card_reader.py --config /etc/au-card-reader/config.ini --test-neotower
```

Cycles idle (rainbow) → green → yellow → red → blink → idle. `--test-lamp`
cycles both the USB tower and the NeoPixel tower together.
