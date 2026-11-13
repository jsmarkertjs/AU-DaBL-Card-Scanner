# Adafruit 2.13" eInk FeatherWing (#4195) — wiring notes

- **Product:** Adafruit 2.13" Monochrome eInk / ePaper Display FeatherWing,
  **250x122** with **SSD1680** driver, ID `4195`
  (<https://www.adafruit.com/product/4195>)
- **Panel revision:** newer units ship with the **GDEY0213B74** panel, which needs
  a **16-pixel memory offset** (still SSD1680).
- **Power:** **3.3V only** — never 5V.
- **Bus:** SPI0 (write-only for the display).
- The FeatherWing only silk-screens the four eInk **control** signals
  (`DC`, `ECS`, `CS`, `SRCS`); the power/SPI pins follow the standard Feather
  header and are labeled on the front (`Reset`, `3V`, `GND`, `SPI`).

## Wiring: FeatherWing -> Raspberry Pi 3 B+

| Our wire | Feather pin name | FeatherWing header position | Raspberry Pi pin (GPIO) |
|----------|------------------|-----------------------------|--------------------------|
| RST      | RST              | pin 1 — the very first pin on the row next to the reset button | GPIO13 — physical pin 33 |
| 3V3      | 3V               | pin 2 — directly below RST | 3.3V — physical pin 1 (or 17) |
| GND      | GND              | pin 4 — two below 3V3 (pin 3 = AREF, skip it) | GND — physical pin 6 |
| SCK      | SCK              | pin 11 — first pin of the 3-pin `SPI` group | GPIO11 / SCLK — physical pin 23 |
| MOSI     | MO               | pin 12 — right below SCK (middle of `SPI`) | GPIO10 / MOSI — physical pin 19 |
| ECS      | EINK CS = D9     | left row, labeled **ECS** | GPIO8 / CE0 — physical pin 24 |
| DC       | D10              | left row, labeled **DC** | GPIO22 — physical pin 15 |

All jumpers go into the **header sockets** (male-to-female Dupont: male end into
the FeatherWing, female end onto the Pi). The flat pads next to the `DC`/`ECS`
labels carry the **same** signals as the adjacent sockets.

## Not connected

| Signal | Where | Why |
|--------|-------|-----|
| MISO | 3rd pin of the `SPI` group (pin 13) | eInk is write-only; only needed for SRAM/SD |
| CS | labeled `CS` | microSD card chip-select (unused) |
| SRCS | labeled `SRCS` | onboard SRAM chip-select (unused on the Pi) |
| BUSY | solder pad only | optional; we use a fixed refresh delay instead |

## Raspberry Pi 3 B+ header reference (used pins marked `◄`)

```
        3V3 ●  1 ┃┃ 2  ● 5V
      GPIO2 ●  3 ┃┃ 4  ● 5V
      GPIO3 ●  5 ┃┃ 6  ● GND   ◄── GND
      GPIO4 ●  7 ┃┃ 8  ● GPIO14
        GND ●  9 ┃┃10  ● GPIO15
     GPIO17 ● 11 ┃┃12  ● GPIO18
     GPIO27 ● 13 ┃┃14  ● GND
     GPIO22 ● 15 ┃┃16  ● GPIO23   ◄── DC
       3V3 ● 17 ┃┃18  ● GPIO24
     GPIO10 ● 19 ┃┃20  ● GND      ◄── MOSI
      GPIO9 ● 21 ┃┃22  ● GPIO25
     GPIO11 ● 23 ┃┃24  ● GPIO8    ◄── SCK (23), ECS (24)
        GND ● 25 ┃┃26  ● GPIO7
      GPIO0 ● 27 ┃┃28  ● GPIO1
      GPIO5 ● 29 ┃┃30  ● GND
      GPIO6 ● 31 ┃┃32  ● GPIO12
     GPIO13 ● 33 ┃┃34  ● GND      ◄── RST
     GPIO19 ● 35 ┃┃36  ● GPIO16
     GPIO26 ● 37 ┃┃38  ● GPIO20
        GND ● 39 ┃┃40  ● GPIO21
```

## Setup

1. Enable SPI: `sudo raspi-config` -> Interface Options -> SPI -> Enable, reboot.
2. Confirm SPI is present: `ls /dev/spidev0.*`.

## Software

`card_reader.py` drives the panel through the `EInkDisplay` class:

- Libraries: **`adafruit-blinka`** + **`adafruit-circuitpython-epd`**
  (`adafruit_epd.ssd1680.Adafruit_SSD1680`) and **`Pillow`**. `deploy/install.sh`
  installs them with `pip3` (using `--break-system-packages` on Bookworm) plus the
  apt packages `python3-pil` and `fonts-dejavu`, and enables SPI.
- Drawing happens on a **background thread**, so the multi-second refresh never
  blocks the swipe loop. A missing panel, missing library, or SPI off is
  non-fatal: the app warns once and keeps running.
- The first name on the "Welcome" screen comes from the Canvas roster (no extra
  API call).

Config (`/etc/au-card-reader/config.ini`):

```ini
[display]
enabled = true
cs = CE0        # GPIO8  - eInk chip select (ECS)
dc = D22        # GPIO22 - data/command
rst = D13       # GPIO13 - reset
busy =          # blank: BUSY pad not wired (fixed delay)
idle_text = Swipe your AU card
subtitle = Design & Build Lab
```

`cs`/`dc`/`rst` accept Blinka names (`CE0`, `D22`) or plain BCM numbers.

Test without swiping (cycles idle + every result):

```bash
python3 card_reader.py --test-display
```

Screens: idle prompt; `Welcome, {First}!` (fully cleared); otherwise the log
factor, e.g. `Waiver Signed – Missing Quiz`. It returns to idle after each swipe.

## Notes

- **RST caveat:** the FeatherWing ties RST to its auto-reset circuit; if the
  panel won't reset, tie RST high instead of driving GPIO13.
- **16-pixel offset:** if the image is shifted on the GDEY0213B74 panel, set the
  driver's column/row start.
- **Safe to test:** the eInk is write-only, so a swapped/mis-wired data line
  cannot damage anything — worst case is "no image," then swap the wire.
- No GPIO conflicts: the card reader is USB HID and the tower light is USB serial.
