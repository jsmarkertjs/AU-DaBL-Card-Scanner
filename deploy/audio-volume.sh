#!/bin/sh
# Set the USB audio card's mixer to max. USB audio cards reset their mixer on
# replug/reboot, so the service runs this first. Best effort: no-op if there is
# no USB audio card, so it never blocks startup.
card=$(awk '/USB-Audio/{print $1; exit}' /proc/asound/cards 2>/dev/null)
[ -n "$card" ] || exit 0
amixer -c "$card" sset PCM 100% unmute >/dev/null 2>&1 || true
exit 0
