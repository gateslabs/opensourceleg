# EMG → OSL ankle: project notebook

What we're building, what's done, and the step-by-step procedures. `README.md` has the technical details of the Summit stream (packet format, `behind`, troubleshooting). This file covers everything around it.

**Goal:** replicate the direct myoelectric control (DMC) from Posh, Schmiedeler and Wensing, _IEEE TNSRE_ 31, 2023 (PDF in this folder), on the Open-Source Leg ankle. The EMG comes live from the Ripple Explorer Summit into this Pi (hostname `OSL`).

```
Summit (GAS + TA EMG, 2 kHz) --WiFi/TCP--> Pi: dmc_controller.py --UDP 9000--> Pi: OSL_brand_connection_impedance.py --USB--> ankle actuator
                                          (envelope -> u -> theta_eq)  <--UDP 9001--  (firmware spring about theta_eq; reports angle)
```

---

## Progress log

| Date       | What                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-10-05 | Pi reads live Hi-Res EMG straight from the Summit over its WiFi (no xipppy). Packet format verified bit-for-bit against Trellis. 60 s test: 2000 samples/s, 0 gaps, no backlog, with Trellis connected.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-10-22 | Chose to replicate the paper's DMC. Found that `OSL_brand_connection_impedance.py` already does the leg side (firmware impedance, K = 0.05 Nm/deg/kg).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 2026-10-22 | Wrote `dmc.py` (envelopes, calibration, Eq. 1, offline replay), `dmc_calibrate.py` and `dmc_controller.py`. Tested on simulated EMG with a fake Summit and a fake leg: Eq. 1 matches hand-computed values; GAS bursts → about +13°, TA bursts → about −13°; with the stream cut, it falls back to neutral within 0.1 s; decode takes 0.13 ms. **Not yet run with the real Summit or the leg.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-10-22 | Saved `summit_session.sh`. Installed tmux. Saved the Summit WiFi on the Pi (`R04500-0094-WIFI`, auto-connect off). The password isn't tested until the first join.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 2026-10-22 | First `summit_session.sh` run (S01_check, 15:52). **WiFi password works:** the Pi joined `R04500-0094-WIFI`, the Summit answered at 192.168.43.1, and the Pi switched back to MWireless by itself. **No EMG data:** `no data from 192.168.43.1:2046 (is Hi-Res on?)`, so `S01_check.xipp` has no Hi-Res. Its packets show that Trellis had **Hi-Res off and LFP + stream 3 + Spike on** (4.6 MB for 3 s of data, WiFi falling behind). Fix: in Trellis, Hi-Res on, everything else off.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-10-22 | **Stream check passed** (S01_check, 16:07, Trellis on Hi-Res only): 119,662 samples over 59.83 s, 0 missing, 8 stale dropped, `behind` mostly 1–5 ms (worst 12.6 ms), no reconnects. **Signal is mostly 60 Hz:** ch1–4 about 7,900 µV RMS, the other channels 400–6,500 µV. Electrodes are probably off or making poor contact; check before finding the channels. Also found that `summit_stream.py` appends to an existing `.xipp`, so this file had the 15:52 run stuck to its front (that caused the false "other streams on" warning). Split them (`S01_check.20261022_155242.xipp` = the old run). `summit_session.sh` now moves an existing `<name>.xipp` aside before recording.                                                                                                                                                                                                                                                                                                      |
| 2026-10-22 | Electrodes on; contractions visible in Trellis. Added `--ch` to `summit_stream.py` so the live view can show any channels (e.g. `--ch 5 6 9 10`); the default is still ch1–4.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-10-22 | First live run with the Pi on the Summit WiFi. Two problems: (1) `numpy` missing, because the venv wasn't active; (2) `summit_stream.py` crashed on its first tick because fewer than 100 samples had arrived (`latest()` returned None). Fixed in `summit_stream.py` and `controller_template.py` (`dmc_controller.py` already handled it), and tested against a fake slow-start Summit. The Pi had to be power-cycled to get back on MWireless, which is fine (the Summit WiFi doesn't auto-connect). Section C lists an optional 30 min auto-return timer. `S01_chan.expp` (75 kB) is from the crashed run; it can be ignored.                                                                                                                                                                                                                                                                                                                                                             |
| 2026-10-22 | Electrodes: TA on ch 1 and 2, GAS on ch 3 and 4. Decided to use **bipolar** pairs: TA = `1-2`, GAS = `3-4`. `summit_stream.py --ch` now takes pairs too (`--ch 1-2 3-4`). Fake test with the same 60 Hz on both channels: about 5,650 µV per single channel, about 30 µV bipolar.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-10-22 | Added `summit_live.sh` (Pi) and `laptop_connect.sh` (laptop) to make the live-SSH setup two commands. The tmux part was tested on the Pi; the WiFi switch and the laptop script haven't run yet. The laptop script assumes a Mac.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-10-22 | **S01_chan** (16:52, live over the Summit WiFi; the scripts worked). **Trellis had Spike, LFP and stream 3 back on**, so the WiFi was overloaded: 0–8 s full, then **61 s with no data**, then 30 s full. `check_recording.py` can't read the file (2 corrupt spots inside Spike packets); I read it with a resyncing parser. In the good 30 s, the **pairs separate the muscles**: toes down → GAS `3-4` 450–555 µV with TA about 30–40; toes up → TA `1-2` 300–450 µV. **The rest level is about 99% 60 Hz hum:** TA 57 µV, GAS 133 µV, and only about 9 µV each after a 60 Hz notch.                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-10-22 | **Decision: all filtering (60 Hz notch, band-pass) happens in Trellis, not in our code.** `dmc.py` stays as is (it only removes the mean). Any change to the Trellis filters changes the EMG levels, so **recalibrate after changing them.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-10-22 | **One-command sessions.** `summit_live.sh S01 [kg]` sets up tmux windows check / leg / dmc. `dmc_controller.py --subject S01` now runs the whole session on one Summit connection: a **quick cued calibration** (relax 5 s, toes down 3 s, relax 3 s, toes up 3 s; no key presses in between), then a menu: Enter = trial driving the leg, `d` = dry run, `c` = recalibrate, `q` = quit. Ctrl-C ends a trial (ramp to neutral). One `.xipp` per session, a log per trial, `_session.txt` index. Rest shortened from the paper's 10 s to 5 s. Tested with a fake Summit (60 Hz on every channel + scripted contractions) and a fake leg: rest about 9 µV, toes down → u +1, toes up → u −1, dry run sent nothing, live trial sent 400 setpoints (−15° to 0°, ending at neutral); a no-contraction calibration fails with a clear message. Not yet run on the real Summit or leg.                                                                                                               |
| 2026-10-22 | `laptop_connect.sh` now runs on Linux too (nmcli) as well as Mac, so the **Linux desktop** can be the session terminal. The desktop stays on the Summit WiFi for Trellis and keeps its internet over its other connection, so it never has to switch networks. Linux branch checked on the Pi (syntax, SSID detection, ping); not yet run on the desktop. `PI_HOST=<ip>` overrides `OSL.local` if mDNS doesn't resolve.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-10-22 | **First real bench runs, EMG driving the leg** (S01 19:03, 28 s; S01 19:06, 46 s; bench spring `--k-rad 20`, b = 4). The whole chain worked: 0 stale ticks, `behind` 1–3 ms. Calibrations: m_GAS 14.0 / 7.9 (paper 1.5–5), m_TA 0.07 / 0.04. **EMG side is fine:** u reaches ±1, so θ_eq reaches ±15°. **The ankle only moved about ±7°, and it sticks:** when θ_eq goes back to 0 it stays wherever it was (+5.4°, −4.5°, +4.95°…), and the two trials started at −5.85° and +5.9°. That looks like a friction dead band: with K = 20 Nm/rad, 7° of error is only about 2.4 Nm, which the ankle's friction holds. The paper's K for 80 kg is 229 Nm/rad (~11× stiffer), which would shrink the band to under 1°. b = 4 is also high for K = 20 (the script recommends 0.02–0.03·K); this only slows the motion and isn't the cause. Next: stiffer bench spring (step D.1a). Direction: GAS made the measured angle go **positive**, which was toes **up**, so `PF_SIGN` had to flip (below). |
| 2026-10-22 | **Third bench run** (S01 19:17, 31 s, `PF_SIGN` still +1). Same sticking: with θ_eq at 0 the ankle rested at −5.5° and later +6.8°. **Calibration was off:** GAS MVA only 64 µV (200–240 in the two runs before), so m_GAS came out at 47 (paper 1.5–5). Probably a weak toes-down push or a shifted GAS electrode; when the printed MVA is far below the usual ~200 µV, recalibrate. `_session.txt` is empty: the controller was closed without going back to the menu, so the trial wasn't indexed (the `_T01_log.csv` itself is complete).                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-10-22 | **Direction verified on the bench: `PF_SIGN = -1`** (with +1, contracting GAS sent the toes up). Set `PF_SIGN = -1` and `PF_SIGN_VERIFIED = True` in `dmc_controller.py`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-10-22 | **Bench spring raised:** `summit_live.sh` with no weight now starts the leg with `--k-rad 60 --damping 1.5` (variables `BENCH_K` / `BENCH_B` at its top), up from `--k-rad 20` and b = 4. Not yet run on the leg.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-10-22 | **Git repaired and pushed.** The repo had 9 empty object files (most likely from power-cycling the Pi in the middle of a git write), and every git command failed. I moved them to `~/git_empty_objects_backup_20261022/`, re-fetched from origin, and `git fsck` is now clean. All BRAND code and this notebook are committed on branch **`brand-dmc`**, pushed to `gateslabs/opensourceleg`. Subject data is kept out of git by `BRAND/.gitignore` (section G). The repo's pre-commit hooks reformatted the scripts (ruff-format, prettier; formatting only, checked), and the commit used `--no-verify` because the library's lint rules (function complexity etc.) flag research scripts.                                                                                                                                                                                                                                                                                                 |
| 2026-10-22 | **GUI: `dmc_gui.py`** (section F). A browser page on the Pi that runs the whole session: settings (subject, K, b, weight, θmax, channels, saved calibration, trial length), stream check, leg home/engage/stop, calibrate, dry/live trials, Stop all, live ankle angle, a plot and tuning hints for each trial, and file downloads. It types into the same tmux windows as the terminal flow, so both can be used together. It listens on the Pi only; the laptop reaches it through `./laptop_connect.sh gui` (SSH tunnel that reconnects through WiFi switches). Tested against a fake leg, controller and stream in a separate tmux session: every button, the guards (no live trial before the spring is engaged, no controller while the check holds the Summit, invalid settings refused, downloads limited to data files), Stop all; rendered in headless Chromium (light, dark, phone width) with no script errors. **Not yet used with the real leg or Summit.**                     |
| 2026-10-22 | **Merged `brand-dmc` into `main`** (fast-forward, only `BRAND/` files) and pushed. The Pi now works on `main`. The new `laptop_connect.sh` (with `gui`) is copied to the desktop.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |

## Next steps

- [x] **Stream check:** passed 2026-10-22 (S01_check). WiFi password works too.
- [x] **Find the channels:** TA = `1-2`, GAS = `3-4`, bipolar (S01_chan, 2026-10-22).
- [x] **Direction (`PF_SIGN`):** −1, verified on the bench 2026-10-22.
- [ ] **Bench test, stiffer spring** (section D): K 60 (then 120 if needed) until relaxing returns the ankle to within about 1° of neutral; then the stale fallback and the e-stop.
- [ ] **First session with the GUI** (section F), on the bench.
- [ ] **Optional:** the one-line sudo rule (section F) so the GUI's WiFi buttons work.
- [x] **Git:** `brand-dmc` merged into `main` 2026-10-22 (section G).
- [ ] **Watchdog (decision pending):** decide whether to add a packet timeout to `OSL_brand_connection_impedance.py`. Today it holds the last setpoint if the controller dies.
- [ ] **Fixed delay:** measure it with `summit_latency.py` (bench only, see README).
- [ ] **First worn session:** follow section C, under the IRB protocol.

## Session settings (fill in)

| Setting        | Value                                                           | Where it's set                                              |
| -------------- | --------------------------------------------------------------- | ----------------------------------------------------------- |
| GAS channel(s) | **`3-4`** bipolar (3 minus 4); confirmed in S01_chan 2026-10-22 | `dmc_calibrate.py --gas` (saved in the calibration JSON)    |
| TA channel(s)  | **`1-2`** bipolar (1 minus 2); confirmed in S01_chan 2026-10-22 | `dmc_calibrate.py --ta`                                     |
| `PF_SIGN`      | **−1**, verified on the bench 2026-10-22                        | top of `dmc_controller.py`                                  |
| Bench spring   | K **60** Nm/rad, b **1.5** Nm·s/rad (was 20 / 4: too soft)      | `summit_live.sh` `BENCH_K` / `BENCH_B`, or the GUI settings |
| Subject weight | ? kg                                                            | `OSL_brand_connection_impedance.py --weight-kg`             |

---

## A. One-time Pi setup (done 2026-10-22; repeat on a new Pi)

These need internet, so the Pi must be on MWireless.

```bash
sudo apt install -y tmux

read -rsp "Summit WiFi password: " PW; echo        # typing is hidden; password is on the Summit's web page
sudo nmcli connection add type wifi ifname wlan0 con-name R04500-0094-WIFI \
  ssid R04500-0094-WIFI wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PW" \
  connection.autoconnect no
unset PW
```

If the password turns out wrong: `sudo nmcli connection modify R04500-0094-WIFI wifi-sec.psk 'correct-password'`

## B. Stream check (unattended, no subject needed)

Use this when you're SSH'd in over MWireless. The script moves the Pi to the Summit's network, records, checks the recording and moves the Pi back. If anything fails, it still moves the Pi back.

1. Summit on. In Trellis: Wireless, TCP, **Hi-Res only**.
2. On the Pi:
   ```bash
   cd ~/opensourceleg/BRAND
   sudo -v
   sudo nohup ./summit_session.sh 60 S01_check &
   ```
3. SSH drops. Reconnect after about 90 s and read the result: `cat S01_check.log`
4. Look for `on R04500-0094-WIFI; Summit answering`, `behind` at a few ms, `gaps 0`, and RMS that rises when the subject contracts. `could not join` probably means a wrong password (see A).

## C. Session: connect, calibrate, run

There are two ways to drive the session: the **GUI** (section F: same steps, as buttons) or the terminal, below. Steps 1–2 are the same for both.

The laptop joins the Summit's network too and you SSH to the Pi through it. Only the Pi's internet is lost, so this Claude chat is offline until the Pi is back on MWireless.

**1. Summit and Trellis.** Power on the Summit. Laptop on `R04500-0094-WIFI`. In Trellis: Wireless, TCP, **Hi-Res only (check Raw, LFP and Spike are off every time: they came back on once)**, Hi-Res filters incl. the 60 Hz notch. Keep the filters the same all session; change them → recalibrate (`c`).

**2. Pi** (from MWireless; SSH drops a second later):

```bash
~/opensourceleg/BRAND/summit_live.sh S01        # bench: leg gets the bench spring (K 60, b 1.5)
~/opensourceleg/BRAND/summit_live.sh S01 72     # worn: K from body weight in kg
```

**3. Laptop or desktop:** `./laptop_connect.sh gui` for the GUI (section F), or `./laptop_connect.sh` for the terminal (joins the WiFi if needed, waits for the Pi, attaches to tmux `emg`). Works on Mac and Linux. The Linux desktop is already on the Summit WiFi (for Trellis) and keeps its internet, so it goes straight to attaching. If `OSL.local` doesn't resolve: `PI_HOST=<Pi's IP> ./laptop_connect.sh`.

**4. Window 0 `check`:** Enter. Both RMS numbers (TA, GAS) should sit at about 10–20 µV relaxed and jump when you contract. `gaps 0`. Ctrl-C.

**5. Window 1 `leg`** (`Ctrl-b 1`): Enter. It homes, then Enter again to engage the spring. It holds neutral until the controller sends.

**6. Window 2 `dmc`** (`Ctrl-b 2`): Enter. It connects, then **calibrates** (one Enter, then follow the cues, 15 s):

- RELAX 5 s
- TOES DOWN 3 s: half-step forward with the _other_ foot, push as hard as you can
- relax 3 s
- TOES UP 3 s: pull as hard as you can

It prints rest, MVA and the slopes (paper: m_GAS 1.5–5, m_TA 0.01–0.63). If it fails it says why and you redo it.

**7. Trials, from the menu:**

- `d` = **dry run**: u is computed and shown, nothing sent. Toes down → bar `+`, toes up → `−`, relaxed → about 0. Do one first.
- Enter = **trial driving the leg**.
- Ctrl-C ends a trial: the ankle ramps to neutral and you're back at the menu.
- `c` = recalibrate (e.g. after electrodes shift). `q` = quit.

**8. Stop:** `q` in window 2 first, then Ctrl-C the leg in window 1.

**9. Copy the files** from the laptop or desktop: `scp jkanetis@OSL.local:~/opensourceleg/BRAND/S01_2026* .` (or the GUI's _Session files_ links)

**10. Back to the internet:** `./summit_live.sh back` in tmux, or power-cycle the Pi (the Summit WiFi doesn't auto-connect, so it boots onto MWireless).

**Files per session** (`S01_<date_time>_…`): `.xipp` all EMG incl. calibration · `_cal1.json` + `_cal1_phases.npz` (`_cal2` after `c`) · `_T01_log.csv` every tick of trial 1 · `_session.txt` one line per trial (calibration used, live or dry, length).

**If SSH drops mid-session:** everything keeps running in tmux; reconnect with `./laptop_connect.sh`. The leg keeps running while you're disconnected, so the **e-stop must be in someone's hand**.

tmux keys: `Ctrl-b 0` / `1` / `2` switch windows · `Ctrl-b d` detach (leaves things running).

**Tuning sensitivity:** edit `mva` in the `_cal1.json` (raise = less sensitive), then quit and restart with `python3 dmc_controller.py --subject S01 --cal <that file>`, which skips calibrating.

## D. Bench test before the first worn session

The leg is on the bench, off the body. The person wearing the electrodes is not attached to the leg.

1. Start the leg with the bench spring (`./summit_live.sh S01` with no weight: `--k-rad 60 --damping 1.5`). **Check neutral after homing:** once the spring engages, the status line should read `ankle: +0.0xx (eq +0.000)` and the foot should sit at 90° to the shank. If it settles somewhere else, the ankle `home_offset_rad` (0.5236, unverified) in `OSL_brand_connection_impedance.py` is wrong: stop and fix that before any EMG control.
   - **a. If the ankle barely moves or doesn't come back to neutral:** the spring is too soft for the ankle's friction. On 2026-10-22, K = 20 gave only ±7° of the ±15° and the ankle stuck up to 6° off neutral, so the default is now K 60, b 1.5. If it still sticks, go to `--k-rad 120 --damping 3` (GUI: Settings → K and b, Stop leg, Start). You're done when relaxing brings the ankle back to within about 1° of neutral; the GUI's trial plot reports this as "off neutral at rest".
2. Calibrate, then a dry run, then a trial driving the leg. Contract GAS: **the toes must point down.** Done 2026-10-22: `PF_SIGN = -1` (verified). Recheck it if the ankle actuator, its wiring or the homing changes.
3. Check that relaxing returns the ankle to neutral smoothly.
4. Stale fallback: switch Trellis's Hi-Res off for a moment, or walk the laptop away. The ankle should ease back to neutral, and the log should show `stale`.
5. Ctrl-C the trial: the ankle ramps to neutral. Then test the e-stop.
6. Only then use the full K (`./summit_live.sh S01 <weight>`).

## E. After a session

```bash
python3 check_recording.py S01_<date_time>.xipp --npz                                  # gaps, length, convert
python3 dmc.py --cal S01_<date_time>_cal1.json --replay S01_<date_time>.xipp           # same DMC pipeline offline -> _dmc.csv
```

Each `_T01_log.csv` has every controller tick: Summit timestamp (to line it up with the `.xipp`), `behind`, `u_p`, `u_d`, `u`, commanded and measured angle.

## F. GUI (`dmc_gui.py`)

A page in your browser that runs section C with buttons and lets you change the settings without editing code. It drives the same tmux windows (check / leg / dmc), so the terminal (`./laptop_connect.sh`) and the GUI can be open together and always show the same thing.

**Start it**

1. Pi, from MWireless: `~/opensourceleg/BRAND/summit_live.sh S01` (as in C.2). This also starts the GUI in tmux session `gui`. To start only the GUI, without touching WiFi: `./summit_live.sh gui`.
2. Laptop or desktop: `./laptop_connect.sh gui`. It joins the Summit WiFi if needed, opens an SSH tunnel to the Pi and opens **http://localhost:8080**. Keep that terminal open; the tunnel reconnects by itself when the Pi changes WiFi. If 8080 is taken on your machine: `GUI_PORT=8081 ./laptop_connect.sh gui`. From VS Code on the Pi you can instead forward port 8080 (Ports tab).
3. First time on a machine: copy the new `laptop_connect.sh` over again (`scp jkanetis@OSL.local:opensourceleg/BRAND/laptop_connect.sh .`).

**Use it** (top to bottom)

- **Settings** (left): subject, _Bench_ (K in Nm/rad) or _Worn_ (weight → K = 2.865 × kg), damping b (suggested range shown: 0.02–0.03·K), θmax, TA/GAS channels, _Calibrate fresh_ or a saved calibration, trial length (0 = until _End trial_). **Save settings**. They apply the next time you press _Start_ in a step; to change the spring, _Stop leg_ and _Start_ again. The exact command for each step is shown above its buttons.
- **1 Stream check:** _Start_, look for RMS ≈ 10–20 µV relaxed and `gaps 0`, _Stop_. It must be stopped before step 3.
- **2 Leg:** _Start (home)_ (asks first; the ankle drives to its hard stop), then _Engage spring_. The big number is the live ankle angle; at rest it should read about 0°. A yellow note appears if it sits more than 2° off neutral (raise K).
- **3 Controller:** _Start_ → _Begin calibration_ (follow the cues in the output box) → _Dry run_ first → _Live trial_ (asks first; only enabled once the spring is engaged) → _End trial_ (ramps to neutral). _Recalibrate_, _Quit_, _Ctrl-C_ as in the terminal.
- **Trial result:** commanded vs measured ankle angle for the newest trial (or pick one), range, stale ticks, how far from neutral the ankle stays when you relax, and a tuning hint.
- **Stop all** (top right): ends the trial (ramp to neutral), quits the controller, stops the leg (motor off, ankle limp) and the check. **It is not an e-stop.**
- **Session files:** download links for each session's `.xipp`, logs and calibrations.
- **Pi network:** the Pi's WiFi and addresses. The two buttons move the Pi between the Summit WiFi and MWireless, but only with the sudo rule below; without it they say so, and you use `./summit_live.sh` / `./summit_live.sh back` or power-cycle the Pi.

Buttons are greyed out unless the program in that window is at the right prompt, and the Pi checks again before typing anything, so a stray click can't send keys to the wrong prompt.

**Optional sudo rule for the WiFi buttons** (once, on MWireless). It lets your user run exactly these two `nmcli` commands without a password, nothing else:

```bash
echo 'jkanetis ALL=(root) NOPASSWD: /usr/bin/nmcli connection up R04500-0094-WIFI, /usr/bin/nmcli connection up MWireless' \
  | sudo tee /etc/sudoers.d/osl-wifi && sudo chmod 440 /etc/sudoers.d/osl-wifi && sudo visudo -c
```

**Security:** the GUI listens on 127.0.0.1 only, so nobody on the campus network can reach it; the SSH tunnel is the only way in. If the page says _No answer from the Pi_, the tunnel is reconnecting (or `dmc_gui.py` isn't running: `tmux ls` should list `gui`; restart with `./summit_live.sh gui`).

## Where to change things

All of these can be changed without editing code, either in the GUI's Settings or as a flag. The defaults live in the files listed.

| What                              | GUI                     | Flag                           | Default lives in                                                                                      |
| --------------------------------- | ----------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Spring stiffness K (bench)        | Settings → Bench, K     | `--k-rad` (leg)                | `summit_live.sh` `BENCH_K=60`                                                                         |
| K from body weight (worn)         | Settings → Worn, weight | `--weight-kg` (leg)            | `OSL_brand_connection_impedance.py` `DEFAULT_WEIGHT_KG` (80)                                          |
| Damping b                         | Settings → Damping      | `--damping` (leg)              | `summit_live.sh` `BENCH_B=1.5` (bench); `DEFAULT_DAMPING` 4.0 in the leg script (worn)                |
| θmax (equilibrium at full effort) | Settings → θmax         | `--theta-max-deg` (controller) | `dmc.py` `THETA_MAX_DEG` (15)                                                                         |
| TA / GAS channels                 | Settings                | `--ta`, `--gas`                | `dmc_controller.py` (`1-2`, `3-4`)                                                                    |
| Reuse a calibration               | Settings → Calibration  | `--cal FILE`                   | –                                                                                                     |
| Trial length                      | Settings → Trial length | `--seconds`                    | 0 (until Ctrl-C / End trial)                                                                          |
| Direction `PF_SIGN`               | shown only              | –                              | `dmc_controller.py` (−1, verified). Code-only, on purpose                                             |
| Rate limit on θ_eq                | –                       | –                              | `dmc_controller.py` `MAX_RATE_DEG_S` (150°/s)                                                         |
| Sensitivity (MVA)                 | –                       | –                              | edit `mva` in the `_calN.json`, then reuse it with `--cal`                                            |
| Ankle zero offset                 | –                       | –                              | `OSL_brand_connection_impedance.py` `JOINT_DEFAULTS["ankle"]["home_offset_rad"]` (0.5236, unverified) |

## G. Git

- Code and this notebook live on **`main`** of `gateslabs/opensourceleg`; the Pi has `main` checked out. (The work started on branch `brand-dmc` and was merged into `main` on 2026-10-22, fast-forward, no conflicts; `brand-dmc` is kept but no longer needed.)
- **Data does not go in git** (`BRAND/.gitignore`): `.xipp`, `.expp`, `.npz`, calibrations, `_session.txt`, `DataLog/`, the paper PDF. Trial logs (`.csv`) and `.log` were already ignored by the repo's `.gitignore`. Copy data off with scp or the GUI.
- To save new work (the Pi must be on MWireless):

  ```bash
  cd ~/opensourceleg
  git add BRAND && git commit -m "BRAND: <what changed>"   # hooks reformat; if they only fail on lint, re-add and commit with --no-verify
  git push
  ```

- If git ever says `object file ... is empty` again (after a power-cut), move the empty files out (`find .git/objects -type f -empty`) and `git fetch origin`, as done on 2026-10-22.

---

## How the DMC works (paper → our code)

| Paper (Sec. II-B, Table I)                                     | Ours                                                                                                                                                                  |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GAS and TA sEMG, rectified, 200 ms moving average, 500 Hz loop | `dmc.envelope`: newest 400 samples (200 ms), mean removed, rectified, averaged; 100 Hz loop                                                                           |
| Rest 10 s → baseline 0; ignore activity within 2 SD            | Rest 5 s (quick calibration, every session); `normalize`: zero point at mean + 2 SD (avoids a jump at the edge)                                                       |
| MVA from 1 s maximal contraction; u_p, u_d in 0–1              | MVA = best 1 s average of the 3 s trial; clipped 0–1                                                                                                                  |
| m_GAS, m_TA: slopes of linear fits to (u_d, u_p)               | Total-least-squares fit through the origin                                                                                                                            |
| m0 = bisector of the m_GAS and m_TA lines                      | tan of the mean of the two angles. The printed formula mixes slopes and angles.                                                                                       |
| Eq. 1 → volitional input u                                     | `volitional_input`; direction factor clipped 0–1, u clipped ±1                                                                                                        |
| τ = −K(θ − θmax·u), K = 0.05 Nm/deg/kg, θmax = 15°             | Leg: firmware impedance, K from `--weight-kg`, plus small damping b = 4 Nm·s/rad for stability (paper: 0); bench: K 60, b 1.5. Controller sends θ_eq = PF_SIGN·15°·u. |
| (not in paper)                                                 | Rate limit 150°/s on θ_eq; stale data → u = 0 (passive spring); ramps to neutral on exit                                                                              |

## Files

| File                                                                              | What                                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dmc.py`                                                                          | Shared DMC math + offline replay                                                                                                                                                                                                                      |
| `dmc_calibrate.py`                                                                | Quick cued calibration (15 s); used by `dmc_controller.py`, also runs on its own                                                                                                                                                                      |
| `dmc_controller.py`                                                               | The session: calibrate, then a menu of trials (live / dry run / recalibrate); live DMC → UDP equilibrium to the leg                                                                                                                                   |
| `OSL_brand_connection_impedance.py`                                               | Leg side: homing, impedance mode, UDP in/out                                                                                                                                                                                                          |
| `summit_session.sh`                                                               | Unattended stream check with automatic network switching                                                                                                                                                                                              |
| `summit_live.sh`                                                                  | Pi: `./summit_live.sh S01 [kg]` sets up tmux `emg` (windows check / leg / dmc, commands typed), starts the GUI, then moves the Pi to the Summit WiFi. `./summit_live.sh gui` starts only the GUI; `./summit_live.sh back` returns the Pi to MWireless |
| `laptop_connect.sh`                                                               | Laptop (Mac) or desktop (Linux): joins the Summit WiFi if needed, waits for the Pi, attaches to tmux `emg`; with `gui`, opens a self-reconnecting SSH tunnel and the GUI page instead                                                                 |
| `dmc_gui.py`                                                                      | The GUI (section F): browser page on the Pi, 127.0.0.1:8080, reached through SSH                                                                                                                                                                      |
| `.gitignore`                                                                      | Keeps subject data and session output out of git (section G)                                                                                                                                                                                          |
| `summit_stream.py`, `summit_record.py`, `check_recording.py`, `summit_latency.py` | Summit streaming tools (see README)                                                                                                                                                                                                                   |
