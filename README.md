# AU Card Reader -> Canvas Check

A headless Raspberry Pi 3 B+ with a keyboard-emulating card reader. When a
student swipes, the Pi reads their AUID, checks Canvas for enrollment and
whether they have been **graded** on a specific assignment, then (once wired)
lights a red / yellow / green LED.

## Behavior

| Light | Meaning |
| ----- | ------- |
| 🔴 Red | No Canvas user found, or not enrolled in the course |
| 🟡 Yellow | Enrolled, but assignment not graded (includes submitted-ungraded) |
| 🟢 Green | Assignment graded (any grade present) |
| (blink) | API / network error (distinct from the three states) |

Until the lights are wired, outcomes print to the console / systemd log.

## How it works

```
[card reader] --evdev(Pi)/stdin(dev)--> input source
    -> parse swipe -> AUID
    -> evaluator (mock or Canvas + optional TTL cache)
    -> State {NOT_ENROLLED, NOT_DONE, DONE, ERROR}
    -> indicator (console | gpio)
```

A swipe looks like `;00555036321?`; the embedded AUID is `5550363`. Confirmed
samples (`;00555036321?` -> `5550363`, `;00539046522?` -> `5390465`) show the
AUID is the middle 7 digits of an 11-digit run, with a 2-digit prefix and a
variable 2-digit suffix. Extraction has two strategies:

1. `parsing.patterns` regexes (default `^;00(?P<auid>\d{7})\d{2}\?$`), tried first.
2. A digit-run fallback: take the longest digit run and drop
   `parsing.trim_prefix` / `parsing.trim_suffix` digits
   (`00555036321` with 2/2 -> `5550363`).

Because AU IDs are sometimes displayed with a leading zero, lookup tries
zero-padded variants too (see `parsing.auid_candidate_widths`).

## Project layout

```
au_card_reader/
  main.py            CLI + event loop
  config.py          YAML/env config
  parsing.py         raw swipe -> AUID
  state.py           State + Evaluation types
  evaluator.py       Evaluator interface, MockEvaluator, caching
  canvas_client.py   Canvas REST client + CanvasEvaluator
  mapping.py         AUID -> Canvas user id
  cache.py           tiny TTL cache
  inputs/            stdin (dev), evdev (Pi)
  indicators/        console (now), gpio (lights phase)
tests/               unit tests (stdlib unittest)
deploy/              systemd unit, env example, install script
config.example.yaml
```

## Develop on Windows (no Pi, no Canvas needed)

Requires Python 3.9+ with `requests` and `PyYAML`.

```powershell
python -m unittest discover -s tests          # run tests
";00555036321?" | python -m au_card_reader --source stdin --indicator console --mock --once
# or interactive: pipe lines into it
```

`--mock` returns deterministic states based on the sum of the AUID digits:
`sum % 3 == 0` -> DONE, `== 1` -> NOT_DONE, `== 2` -> NOT_ENROLLED,
all-zero -> ERROR.

### With real Canvas

1. Copy `config.example.yaml` to `config.yaml` and set `course_id`,
   `assignment_id`, and the base URL.
2. Put the token in `config.yaml` **or** set `AU_CANVAS_TOKEN` (preferred).
3. Inspect the course roster and verify how an AUID maps to a user:

```powershell
python -m au_card_reader --dump-roster     # id / sis_user_id / login_id per user
python -m au_card_reader --discover 5550363
```

The default `mapping.mode: roster` fetches the course roster once (cached
10 min) and matches the AUID and its zero-padded variants against each user's
`sis_user_id`, then `login_id`. If that finds nothing, provide
`auid_to_canvas_id.csv` (columns `auid,canvas_user_id`) and it takes precedence.
`mapping.mode: sis` instead calls `/users/sis_user_id:<auid>` per swipe (may
require elevated permissions).

## Deploy on the Pi (git clone)

The Pi is headless at runtime; code is delivered by cloning this repo. On the
Pi (monitor + keyboard):

```bash
sudo apt install -y git
git clone https://github.com/jsmarkertjs/AU-DaBL-Card-Scanner.git ~/au-card-reader
sudo bash ~/au-card-reader/deploy/install.sh
```

`install.sh` installs the system packages, points the systemd service at the
clone, adds the login user to the `input` group, and starts the service.

Then configure it:

1. Put the Canvas token in `/etc/au-card-reader/env`:
   `AU_CANVAS_TOKEN=...` (this file is never in the repo).
2. Edit `/etc/au-card-reader/config.yaml` with `base_url`, `course_id`,
   `assignment_id`.
3. `sudo systemctl restart au-card-reader`.

To update later:

```bash
cd ~/au-card-reader && git pull
sudo bash deploy/update.sh
```

Existing config/token in `/etc/au-card-reader` are preserved across updates.

Useful commands:

```bash
python3 -m au_card_reader --list-devices      # find the reader's event device
python3 -m au_card_reader --dump-roster       # verify AUID -> Canvas mapping
journalctl -u au-card-reader -f               # follow logs
systemctl restart au-card-reader
```

If the reader is not detected, check the device name and update
`input.device_name_regex`. If you get permission errors reading
`/dev/input/event*`, ensure the service user is in the `input` group
(`sudo usermod -aG input pi`, then reboot) or install
`deploy/99-au-card-reader.rules`.

**Network note:** the Pi must reach Canvas. It is on the AU guest network; if
guest access ever blocks the Canvas API, a different network or Ethernet may be
needed. Verify with `--dump-roster`.

## Lights (later phase)

Set `indicator.driver: gpio` and wire three LEDs to the BCM pins in
`indicator.gpio` (default red 17, yellow 27, green 22), each through a
resistor to ground. The `GpioIndicator` is implemented but not yet validated
on hardware.

## Calibration note

Parsing is confirmed against two real cards (see `tests/test_parsing.py`). If a
card ever fails to parse, adjust `parsing.patterns` / `trim_prefix` /
`trim_suffix`. Use `--discover` to confirm Canvas mapping.
