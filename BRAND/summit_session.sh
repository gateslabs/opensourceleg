#!/usr/bin/env bash
# summit_session.sh: run a Summit EMG session on the Pi without staying SSH'd in.
#
# It moves the Pi's WiFi onto the Summit's network, streams and records EMG for
# a set time, checks the recording, then moves the WiFi back to where it was.
# Everything is written to <name>.log next to this script.
#
#   cd ~/opensourceleg/BRAND
#   sudo -v                                        # type your password first
#   sudo nohup ./summit_session.sh 60 S01_T01 &    # 60 s session named S01_T01
#
# Your SSH (and any Claude session on the Pi) loses connection while the Pi is
# on the Summit's network. That's expected. When it's back (about duration + 30 s),
# reconnect and read the result:   cat S01_T01.log
#
# If the Pi has another way onto the internet (Ethernet or a second WiFi
# adapter), run it with KEEP_SUMMIT=1 to leave the WiFi on the Summit:
#   sudo KEEP_SUMMIT=1 nohup ./summit_session.sh 60 S01_T01 &

set -u
[ "$(id -u)" -eq 0 ] || { echo "run it with sudo; see the top of this file"; exit 1; }

DURATION="${1:-60}"
NAME="${2:-session_$(date +%Y%m%d_%H%M%S)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
PY="${PY:-/home/$RUN_USER/opensourceleg/.venv/bin/python}"
SSID="R04500-0094-WIFI"
SUMMIT_HOST="${SUMMIT_HOST:-192.168.43.1}"
SUMMIT_PASSWORD=""        # only needed the first time this Pi joins the Summit's network
IFACE="wlan0"
LOG="$DIR/$NAME.log"

echo "started; everything goes to $LOG"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T')  $NAME, $DURATION s ==="

[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -c "import numpy" 2>/dev/null || { echo "ERROR: $PY has no numpy; set PY= to your environment's python"; exit 1; }

ORIG="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v d="$IFACE" '$2==d {print $1}')"
echo "WiFi was on: ${ORIG:-nothing}"

restore() {
    if [ "${KEEP_SUMMIT:-0}" != "1" ] && [ -n "$ORIG" ] && [ "$ORIG" != "$SSID" ]; then
        echo "switching WiFi back to $ORIG"
        nmcli connection up "$ORIG" || echo "WARNING: could not rejoin $ORIG"
    fi
    chown "$RUN_USER": "$DIR/$NAME".* 2>/dev/null
    echo "=== finished $(date '+%F %T') ==="
}
trap restore EXIT

# 1. find the Summit's network
nmcli dev wifi rescan >/dev/null 2>&1
sleep 5
if ! nmcli -t -f SSID dev wifi list | grep -qx "$SSID"; then
    echo "ERROR: $SSID not visible. Is the Summit on, with WiFi started?"
    exit 1
fi

# 2. join it
if nmcli -t -f NAME connection show | grep -qx "$SSID"; then
    nmcli connection up "$SSID" || { echo "ERROR: could not join $SSID"; exit 1; }
elif [ -n "$SUMMIT_PASSWORD" ]; then
    nmcli dev wifi connect "$SSID" password "$SUMMIT_PASSWORD" ifname "$IFACE" \
        || { echo "ERROR: could not join $SSID"; exit 1; }
else
    echo "ERROR: this Pi hasn't joined $SSID before; set SUMMIT_PASSWORD at the top of this script"
    exit 1
fi

# 3. wait for the Summit to answer
for _ in $(seq 1 20); do
    ping -c1 -W1 "$SUMMIT_HOST" >/dev/null 2>&1 && break
    sleep 1
done
ping -c1 -W1 "$SUMMIT_HOST" >/dev/null 2>&1 || { echo "ERROR: joined, but $SUMMIT_HOST isn't answering"; exit 1; }
echo "on $SSID; Summit answering at $SUMMIT_HOST"

# 4. stream and record (prints behind / RMS / gaps once a second)
cd "$DIR"
if [ -e "$NAME.xipp" ]; then          # summit_stream.py appends, so don't mix two runs in one file
    OLD="$NAME.$(date -r "$NAME.xipp" +%Y%m%d_%H%M%S).xipp"
    mv "$NAME.xipp" "$OLD" && echo "moved the earlier $NAME.xipp to $OLD"
fi
timeout -s INT "$DURATION" "$PY" -u summit_stream.py --host "$SUMMIT_HOST" --record "$NAME.xipp"

# 5. check the recording
"$PY" check_recording.py "$NAME.xipp"
