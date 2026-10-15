# AU DaBL Card Scanner

A Raspberry Pi reads student AU IDs from a keyboard-emulating card reader,
checks Canvas for the **Safety Waiver** (graded) and the **Safety Quiz**
(score ≥ passing), and sets an Adafruit USB tower light:

| Light | Meaning |
| ----- | ------- |
| 🟢 Solid green | Fully cleared — waiver **and** quiz |
| 🟢🟡 Green/Yellow (alternating) | Waiver done, quiz missing |
| 🟡🔴 Yellow/Red (alternating) | Quiz passed, waiver missing |
| 🔴 Solid red | Neither — no progress |
| 🔴 Blinking red | Not in the course (also Canvas/network error) |
| 🟡 Solid yellow | Idle / waiting |

The buzzer is never used (it browns out the device — see `docs/tower-lamp.md`).

## How it works

One file, `card_reader.py`:

```
card swipe -> AU ID -> Canvas roster + waiver/quiz submissions -> tower light
```

- The reader types the card data then Enter. `parse_auid` takes the middle
  7 digits of the digit run: `;00555036321?` -> `5550363`.
- On the Pi the reader is read via Linux evdev (works with no window focus).
- Canvas is queried with a token: roster lookup once, then the waiver and quiz
  submissions for that student.
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
2. In `/etc/au-card-reader/config.ini` set `course_id`, `assignment_id`
   (Safety Waiver), `quiz_assignment_id` (Safety Quiz), and `quiz_passing_score`.
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

## Logging swipes to a Google Sheet

Every swipe appends a `Timestamp`, `AUID`, and `Status` row to a Google Sheet,
via a small Apps Script that the Pi calls over HTTPS. The `Status` is one of:

- `Fully Cleared`
- `Waiver Signed – Missing Quiz`
- `Quiz Passed – Missing Waiver`
- `Incomplete – No Progress`
- `Unregistered`

The Pi only needs `requests`; no Google libraries or key files.

One-time setup:

1. Create a Google Sheet and put your column headers in row 1: **Timestamp,
   AUID, Status** (the script appends `[timestamp, auid, status]`, so order the
   headers to match).
2. In the sheet: **Extensions → Apps Script**, paste:

   ```js
   const SHEET_ID = 'YOUR_SPREADSHEET_ID';        // from the sheet's URL
   const SECRET   = 'YOUR_LONG_RANDOM_SECRET';    // make one up, keep it secret

   function doPost(e) {
     const body = JSON.parse(e.postData.contents);
     if (body.token !== SECRET) return ContentService.createTextOutput('forbidden');
     const lock = LockService.getScriptLock();
     lock.waitLock(10000);
     try {
       SpreadsheetApp.openById(SHEET_ID).getSheets()[0]
         .appendRow([body.timestamp, body.auid, body.status]);
     } finally { lock.releaseLock(); }
     return ContentService.createTextOutput('ok');
   }
   ```

3. **Deploy → New deployment → Web app**; **Execute as: Me**;
   **Who has access: Anyone** → copy the `/exec` URL.
4. Keep the sheet itself **private** (the web app writes on your behalf, so the
   sheet does not need to be shared).

Then in `/etc/au-card-reader/config.ini` on the Pi:

```ini
[log]
enabled = true
url = https://script.google.com/macros/s/XXXX/exec
token = YOUR_LONG_RANDOM_SECRET
timeout = 5
```

Test it without swiping:

```bash
python3 card_reader.py --log-test --config /etc/au-card-reader/config.ini
```

If the Pi is offline, the row is simply skipped (a warning is logged).

**Do not commit the `/exec` URL, the secret, or the sheet link** — they live
only in `config.ini` (gitignored).

## Sound effects per card

When a card is swiped, if a sound file named after its AUID exists, it's played
through the headphone jack (queued, one at a time). Files live in `Sounds/` and
are **gitignored** — name them `<AUID>.mp3` (or `.wav`/`.ogg`), e.g.
`Sounds/5550363.mp3`. This runs alongside the light/log — nothing else changes.

Config (`/etc/au-card-reader/config.ini`):

```ini
[audio]
enabled = true
dir = Sounds
player = mpg123
volume = 80
device = auto
player_args =
```

- Requires `mpg123` on the Pi (`sudo apt install mpg123`; `install.sh` does this).
- **Output device is auto-selected** each time it plays: it prefers a **USB
  speaker** if one is plugged in, otherwise the **headphone jack** — so it works
  whichever is connected, and hot-plugging is fine. Override with an explicit
  ALSA device (e.g. `device = plughw:1,0`) or `device = none` for the default.
- Test without swiping: `python3 card_reader.py --play 5550363`.
- If it's silent from the service, try forcing ALSA: `player_args = -o alsa`.

## Run locally on a laptop (no Pi)

```bash
python card_reader.py --config config.ini --stdin     # type/paste swipes
python card_reader.py --config config.ini --test-lamp # exercise the tower
```

`config.ini` is gitignored; start from `config.ini.example`.

## Notes

- The Pi needs internet for Canvas.
- Groups: `dialout` = tower serial, `input` = card reader, `audio` = sound.
- If the tower serial gets grabbed on the Pi, mask `brltty`/`ModemManager`
  (see `docs/tower-lamp.md`).
