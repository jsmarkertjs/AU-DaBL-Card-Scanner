# AU DaBL Card Scanner

A Raspberry Pi reads student AU IDs from a keyboard-emulating card reader,
checks Canvas to see whether a specific assignment has been **graded**, and
sets an Adafruit USB tower light:

| Light | Meaning |
| ----- | ------- |
| 🟡 Yellow | Idle / waiting |
| 🟢 Green | Assignment submitted and graded |
| 🔴 Red | Not graded, or student not found |
| 🔴 Blinking | Canvas / network error |

The buzzer is never used (it browns out the device — see `docs/tower-lamp.md`).

## How it works

One file, `card_reader.py`:

```
card swipe -> AU ID -> Canvas roster + submission -> tower light color
```

- The reader types the card data then Enter. `parse_auid` takes the middle
  7 digits of the digit run: `;00555036321?` -> `5550363`.
- On the Pi the reader is read via Linux evdev (works with no window focus).
- Canvas is queried with a token: roster lookup once, then the assignment
  submission for that student; green when graded.
- The tower light is a USB-serial device (CH340, VID `0x1A86`), 9600 8-N-1,
  one byte per command.

## Setup on the Pi

```bash
sudo apt install -y git
git clone https://github.com/jsmarkertjs/AU-DaBL-Card-Scanner.git ~/au-card-reader
sudo bash ~/au-card-reader/deploy/install.sh
```

Then:

1. Set the token: `sudo nano /etc/au-card-reader/env` (`CANVAS_TOKEN=...`).
2. Set `course_id` and `assignment_id`: `sudo nano /etc/au-card-reader/config.ini`.
3. `sudo systemctl restart au-card-reader`
4. Watch it: `journalctl -u au-card-reader -f`

Update later: `cd ~/au-card-reader && git pull && sudo bash deploy/update.sh`.

## Test the lamp

```bash
python3 card_reader.py --test-lamp
```

Cycles yellow → green → red → error blink → yellow.

To see what the Pi detects (pick the reader's name for `reader.device_name`):

```bash
python3 card_reader.py --list-devices
```

The tower light is auto-detected by USB VID `0x1A86`, and the reader by
scanning input devices for a keyboard, so neither depends on a fixed port.

## Run locally on a laptop (no Pi)

```bash
python card_reader.py --config config.ini --stdin     # type/paste swipes
python card_reader.py --config config.ini --test-lamp # exercise the tower
```

`config.ini` is gitignored; start from `config.ini.example`.

## Notes

- The Pi needs internet for Canvas.
- `dialout` group = tower serial port; `input` group = card reader.
- If the tower serial gets grabbed on the Pi, mask `brltty`/`ModemManager`
  (see `docs/tower-lamp.md`).
