#!/usr/bin/env bash
# laptop_connect.sh: on the laptop (Mac) or desktop (Linux), join the Summit's
# WiFi and attach to the Pi's tmux session that summit_live.sh set up. If the
# machine is already on the Summit WiFi (e.g. the desktop, which keeps its
# internet over another connection), it skips straight to attaching.
#
#   ./laptop_connect.sh                        # terminal: attach to tmux "emg"
#   ./laptop_connect.sh gui                    # browser: tunnel to the Pi's GUI, opens http://localhost:8080
#   PI_HOST=192.168.43.x ./laptop_connect.sh   # if OSL.local doesn't resolve
#   GUI_PORT=8081 ./laptop_connect.sh gui      # if 8080 is taken on this machine
#
# The GUI listens only on the Pi itself, so the browser reaches it through an
# SSH tunnel. The tunnel reconnects by itself when the Pi changes WiFi; keep
# this terminal open while you use the page (Ctrl-C closes the tunnel).
#
# Copy it over once, while both are on MWireless:
#   scp jkanetis@OSL.local:opensourceleg/BRAND/laptop_connect.sh . && chmod +x laptop_connect.sh

SSID="R04500-0094-WIFI"
PI_HOST="${PI_HOST:-OSL.local}"
PI="jkanetis@$PI_HOST"

if [ "$(uname)" = "Darwin" ]; then
    IFACE="$(networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/ {getline; print $2; exit}')"
    if [ "$(networksetup -getairportnetwork "$IFACE" | sed 's/^.*: //')" != "$SSID" ]; then
        echo "joining $SSID on $IFACE"
        networksetup -setairportnetwork "$IFACE" "$SSID"   # add the password as a 3rd argument if it isn't saved
    fi
    PING="ping -c1 -t1"
else
    if [ "$(nmcli -t -f active,ssid device wifi | sed -n 's/^yes://p')" != "$SSID" ]; then
        echo "joining $SSID"
        # saved connection if there is one, otherwise connect and ask for the password once
        nmcli connection up "$SSID" 2>/dev/null || nmcli --ask device wifi connect "$SSID" || exit 1
    fi
    PING="ping -c1 -W1"
fi

echo -n "waiting for the Pi ($PI_HOST)"
for _ in $(seq 1 60); do
    $PING "$PI_HOST" >/dev/null 2>&1 && break
    echo -n "."; sleep 1
done
echo

if [ "${1:-}" = "gui" ]; then
    PORT="${GUI_PORT:-8080}"
    URL="http://localhost:$PORT"
    OPEN="xdg-open"; [ "$(uname)" = "Darwin" ] && OPEN="open"
    ( sleep 3; $OPEN "$URL" >/dev/null 2>&1 ) &
    echo "GUI: $URL  (keep this terminal open; Ctrl-C closes the tunnel)"
    while true; do
        ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=2 -o ServerAliveCountMax=3 \
            -L "$PORT:localhost:8080" "$PI"
        echo "tunnel dropped (Pi changing WiFi?); reconnecting in 2 s"
        sleep 2
    done
fi

ssh -t "$PI" tmux attach -t emg
