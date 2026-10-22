#!/usr/bin/env bash
# summit_live.sh: get the Pi ready for a DMC session over the Summit's WiFi.
#
#   ./summit_live.sh S01           # bench: leg gets the bench spring (BENCH_K / BENCH_B below)
#   ./summit_live.sh S01 72        # worn: K from the subject's weight in kg
#   ./summit_live.sh gui           # only start the GUI (dmc_gui.py); WiFi untouched
#   ./summit_live.sh back          # put the Pi back on MWireless
#
# It starts a tmux session "emg" with three windows, each with the venv on, in
# this folder, and its command already typed (not run):
#   0 check  live RMS of TA (1-2) and GAS (3-4)        Enter, look, Ctrl-C
#   1 leg    OSL_brand_connection_impedance.py         Enter: homes, then Enter again
#   2 dmc    dmc_controller.py: calibrate, then trials  Enter
# and the GUI (dmc_gui.py) in its own tmux session "gui", then moves the Pi's
# WiFi onto the Summit. Your SSH drops. On the laptop, run laptop_connect.sh
# (terminal) or laptop_connect.sh gui (browser). Switch windows with Ctrl-b 0 / 1 / 2.
#
# To put the Pi back on the internet afterwards, from inside tmux:
#   ./summit_live.sh back       # or just power-cycle the Pi

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
SSID="R04500-0094-WIFI"
HOME_WIFI="MWireless"
VENV="source $HOME/opensourceleg/.venv/bin/activate"
# Bench spring (leg off the body). K = 20 was too soft for the ankle's friction
# (2026-10-22: only ±7° of ±15°, and it stuck off neutral). Damping ~0.02-0.03*K.
BENCH_K=60
BENCH_B=1.5

start_gui() {
    tmux has-session -t gui 2>/dev/null && return
    tmux new-session -d -s gui -c "$DIR" "$HOME/opensourceleg/.venv/bin/python3 dmc_gui.py"
    echo "GUI running (tmux session 'gui', http://localhost:8080 on the Pi)."
}

if [ "${1:-}" = "back" ]; then
    sudo nmcli connection up "$HOME_WIFI"
    exit
fi
if [ "${1:-}" = "gui" ]; then
    start_gui
    exit
fi

SUBJECT="${1:-S01}"
if [ -n "${2:-}" ]; then
    SPRING="--weight-kg $2"
else
    SPRING="--k-rad $BENCH_K --damping $BENCH_B"     # bench default; pass the weight for a worn session
fi
sudo -v || exit 1

tmux kill-session -t emg 2>/dev/null
tmux new-session -d -s emg -n check -c "$DIR"
tmux new-window -t emg:1 -n leg -c "$DIR"
tmux new-window -t emg:2 -n dmc -c "$DIR"
for w in 0 1 2; do tmux send-keys -t "emg:$w" "$VENV" Enter; done
tmux send-keys -t emg:0 "python3 summit_stream.py --ch 1-2 3-4"                        # typed, not run
tmux send-keys -t emg:1 "python3 OSL_brand_connection_impedance.py --mode ankle $SPRING"
tmux send-keys -t emg:2 "python3 dmc_controller.py --subject $SUBJECT"
tmux select-window -t emg:0
start_gui

echo "tmux session 'emg' ready (subject $SUBJECT, leg $SPRING)."
echo "Switching WiFi to $SSID; this SSH will drop. On the laptop: ./laptop_connect.sh  (or ./laptop_connect.sh gui)"
sleep 1
sudo nohup nmcli connection up "$SSID" >/dev/null 2>&1 &
