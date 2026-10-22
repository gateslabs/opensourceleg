# Reading Summit EMG on a Raspberry Pi over WiFi

The Raspberry Pi reads live Hi-Res EMG straight from the Ripple Explorer Summit over the Summit's own WiFi. It needs no xipppy and no Ripple software on the Pi, and it runs while Trellis stays open on a laptop. This folder has the scripts and this guide, written October 5, 2026.

---

## Experiment-day quick start

1. **Summit:** power it on and check that its WiFi network `R04500-0094-WIFI` is visible. If it isn't, see _WiFi is off_ in Troubleshooting.
2. **Laptop:** join `R04500-0094-WIFI`. Open Trellis and set Network to **Wireless** and Mode to **TCP**. Turn on **Hi-Res only** (Raw, LFP and Spike off), then check the signals.
3. **Pi:** make sure it's on the Summit's network (see _Moving the Pi between networks_), then SSH in from the laptop:
   ```
   ssh admin@raspberrypi.local
   source ~/opensourceleg/.venv/bin/activate
   cd ~/opensourceleg/BRAND
   ```
4. **20-second check:**
   ```
   python3 summit_stream.py --record check.xipp
   ```
   Look for `behind` at a few ms, `gaps 0`, and RMS that jumps when the subject contracts. Stop with Ctrl-C.
5. **Run the controller:**
   ```
   python3 controller_template.py --record S01_T01.xipp
   ```
   This saves `S01_T01.xipp` (all EMG) and `S01_T01_log.csv` (every decode).
6. **Afterwards**, copy the files to the laptop and check them:
   ```
   scp admin@raspberrypi.local:~/opensourceleg/BRAND/S01_T01* .
   python3 check_recording.py S01_T01.xipp --npz
   ```

---

## What's in this folder

| File                     | Runs on      | What it does                                                                                                                                        |
| ------------------------ | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summit_stream.py`       | Pi           | The xipppy replacement. Receives the Summit's stream in the background; `latest(n)` returns the newest n samples. Run it directly for a live check. |
| `controller_template.py` | Pi           | A 20 Hz EMG controller loop with stale-data safety, recording and logging. Fill in your decoder.                                                    |
| `summit_record.py`       | Pi or laptop | Records the raw stream to a `.xipp` file. Also provides `load_xipp()` for loading recordings.                                                       |
| `check_recording.py`     | Pi or laptop | Checks `.xipp` files for gaps, length and stray streams, and converts them to `.npz` or `.mat`.                                                     |
| `summit_latency.py`      | Pi           | Optional. Measures the delay from the front end to the Pi using a GPIO loopback. **Bench only.**                                                    |
| `dmc.py`                 | Pi or laptop | Direct myoelectric control math (Posh et al. 2023): GAS/TA envelopes, calibration, volitional input u. Also replays `.xipp` files offline.          |
| `dmc_calibrate.py`       | Pi           | Guided 1-minute DMC calibration (rest, GAS max, TA max). Saves `<subject>_dmc_cal.json`.                                                            |
| `dmc_controller.py`      | Pi           | Live DMC: EMG -> u -> ankle equilibrium, sent to `OSL_brand_connection_impedance.py` at 100 Hz.                                                     |

They all need only Python 3 and numpy. `summit_latency.py` also needs `lgpio` (or `RPi.GPIO`), and `--mat` needs `scipy`. Keep the files in the same folder, because some import each other.

---

## Using it in your own code

```python
from summit_stream import SummitStream

s = SummitStream(host="192.168.43.1", record="run1.xipp")   # record is optional
s.start()

ts, X, behind = s.latest(100)   # was: xp.cont_hires(100, elecs)
# X:      newest 100 samples (50 ms) x 32 channels, in µV. X[:, 0] is channel 1.
# ts:     Summit timestamp of each sample (30 kHz clock ticks, step of 15 per sample)
# behind: how much later than normal the newest sample is, in seconds

if behind > 0.05:
    ...  # data is stale (WiFi stalled): hold a safe output
else:
    ...  # decode X

s.stop()
```

**What `behind` means.** Normally samples reach the Pi a few ms after the Summit records them, and `latest()` is effectively "now." If WiFi hiccups, TCP pauses delivery until the lost piece is resent, so the newest data on the Pi is old, and when delivery resumes it arrives in a burst. `behind` catches both cases. It compares each sample's Summit timestamp with when it reached the Pi, takes the best value from the last 10 s as normal, and reports how far the newest sample is past that. It does **not** include the fixed delay every sample has (front end, filtering, WiFi); `summit_latency.py` measures that.

`SummitStream` reconnects on its own if the connection drops. While it's disconnected, `latest()` reports `behind` as infinity, so the safety check trips.

---

## Loading recordings

```python
from summit_record import load_xipp
ts, X = load_xipp("S01_T01.xipp")     # ts: 30 kHz ticks; X: samples x 32, µV
t = (ts - ts[0]) / 30000               # seconds from the first sample
emg = X[:, [0, 1, 2, 3]]               # channels 1-4
```

Or convert once with `python3 check_recording.py S01_T01.xipp --npz` (or `--mat` for MATLAB). That gives `ts`, `t`, `X` and `fs = 2000`.

**Lining data up:** use the Summit timestamps (`ts`), not the Pi's clock. The two clocks drift about 70 parts per million apart, and the Pi has no internet on the Summit's network to correct its clock. The controller log stores `summit_ts_newest` for every decode, so decodes line up with the EMG exactly. Trellis `.nf3` recordings store the Summit timestamp of their first sample in header bytes 282–285 (little-endian uint32, in the file we checked), so Trellis and Pi recordings can be lined up too.

---

## How it works (what we found)

**Network.**

- The Summit makes its own WiFi network, `R04500-0094-WIFI`. It's 5 GHz (channel 149) and uses WPA1 security. The password is on the Summit's web page.
- On WiFi the Summit is at `192.168.43.1`. Wired, it's at `192.168.42.1`, through the Ethernet port on the power supply, so wired needs mains power.
- The Summit's network has no internet.

**TCP mode is required for WiFi.** The `TCP` box on the Summit's Explorer Config web page must be ticked. In UDP mode the Summit broadcasts data only over Ethernet (to `192.168.42.255`, port 2046). We confirmed that nothing goes out over WiFi in UDP mode.

**No handshake.** In TCP mode the data comes on **port 2046**. Open a TCP connection and the Summit starts streaming right away, with nothing to send first. Trellis also uses port 2047 for control and status messages; that port isn't needed.

**Packet format.** We decoded this from a capture and checked it bit-for-bit against Trellis's own `.nf3` recording of the same data: 46,292 samples × 32 channels, all identical.

| Bytes | Content                                                               |
| ----- | --------------------------------------------------------------------- |
| 0     | Packet size in 4-byte words, header included (Hi-Res: 35 → 140 bytes) |
| 1     | Processor (always 1)                                                  |
| 2     | Module (2 = the front end in this setup)                              |
| 3     | Stream (2 = Hi-Res)                                                   |
| 4–7   | Timestamp, uint32, 30 kHz ticks since the Summit started              |
| 8–11  | uint32, always 1                                                      |
| 12–   | One float32 per channel in µV, channel 1 first                        |

- **Hi-Res** is one packet per sample: 2,000 packets/s, each with all 32 channels, already filtered on the Summit by the Hi-Res filter set in Trellis. The default low-pass is 500 Hz, and there's also a 15–375 Hz EMG preset.
- **Other packets** in the stream that we don't use: a 12-byte packet every 1 ms (module 0, stream 1). When other streams were on we also saw a 1 kHz 16-bit stream (stream 1, probably LFP), a 2 kHz 16-bit stream (stream 3), and Spike snippets (streams 4–35, one per channel). The scripts skip all of these.

**Channels.** Hi-Res is turned on per front end, not per channel, so every packet always carries all 32 channels. Choose the channels you want on the Pi, e.g. `X[:, [0, 1, 2, 3]]`.

**Bandwidth.**

- Hi-Res only is about 292 kB/s per connected client.
- **Keep Raw, LFP and Spike off.** With them on, the Summit produced about 1.66 MB/s, WiFi couldn't keep up, and in about 10 s only 2.5 s of data arrived. TCP doesn't drop data; it falls behind instead.
- Trellis and the Pi can be connected at the same time. Each gets its own copy, and two Hi-Res copies (about 584 kB/s) fit fine.

**Stale packets.** The Summit sometimes re-sends one identical old Hi-Res packet, about every 8 s. The scripts drop any packet whose timestamp isn't newer than the last one kept, and count them as `stale`.

---

## Test results (October 5, 2026)

| Test                                                      | Result                                                        |
| --------------------------------------------------------- | ------------------------------------------------------------- |
| Wired TCP capture vs Trellis `.nf3`                       | Decoded data identical to Trellis, all 32 channels            |
| UDP mode over WiFi                                        | No data over WiFi; UDP only reaches Ethernet                  |
| WiFi, Hi-Res + LFP + Spike                                | WiFi fell about 4× behind; only 2.5 s of data from a 10 s run |
| WiFi, Hi-Res only, Mac (several runs)                     | Full rate, 0 missing samples                                  |
| WiFi, Hi-Res only, Trellis also connected over WiFi, 60 s | 119,974 samples covering 59.99 s, 0 missing                   |
| **Pi**, WiFi, Hi-Res only, Trellis open, 60 s             | 2,000 samples/s every second, 0 gaps, no backlog building     |

---

## One-time setup

**Summit.** Open the web page: `192.168.43.1` on its WiFi, or `192.168.42.1` wired. On Explorer Config, `TCP` must be **True**. Leave everything else alone, and don't press _Defaults_.

**Pi.**

- Copy this folder to `~/opensourceleg/BRAND/`.
- numpy is already in the `opensourceleg` environment (`source ~/opensourceleg/.venv/bin/activate`).
- Install anything else, such as `pip install lgpio` for the latency test, while the Pi is on MWireless; the Summit's network has no internet.

**Laptop internet while on the Summit's WiFi.** Plug an iPhone into the laptop over USB and turn on Personal Hotspot. If the laptop still has no internet, move iPhone USB above Wi-Fi in Network settings > ⋯ > Set Service Order.

**Mac only:** if Python on the Mac fails with "No route to host" while `ping` works, macOS is blocking it from the local network. Go to System Settings > Privacy & Security > Local Network and turn on Terminal (and Chrome, to reach the web page in Chrome). The Pi doesn't have this issue.

---

## Moving the Pi between networks

The Pi has one WiFi radio, so it's on either MWireless or the Summit's network, not both.

```
# to the Summit's network (first time: creates a saved connection)
sudo nmcli dev wifi connect "R04500-0094-WIFI" password "<password from the Summit's web page>"
# later times
sudo nmcli connection up "R04500-0094-WIFI"

# back to MWireless
sudo nmcli connection up MWireless
```

If you're SSH'd in over the network you're leaving, the session freezes. Press Enter, then `~.` to close it. Put the laptop on the same network as the Pi and reconnect with `ssh admin@raspberrypi.local`. If that name isn't found, scan for the Pi's address:

```
for i in $(seq 130 150); do ping -c1 -t1 192.168.43.$i >/dev/null && echo 192.168.43.$i; done
```

Optional: to make the Pi join the Summit's network automatically whenever it's on, run `sudo nmcli connection modify "R04500-0094-WIFI" connection.autoconnect-priority 10`.

---

## Measuring the delay (optional, bench only)

`summit_latency.py` measures the delay from a signal at the front end to the sample arriving on the Pi: front end, Hi-Res filter, the Summit's packet bundling, WiFi, and the Pi receiving it. That's everything before your decoder. **Nothing may be connected to a person during this test.**

```
Pi GPIO17 (pin 11) ──[1 MΩ]──┬── spare channel socket (e.g. 32), DIP switch ON
                             │
Pi GND    (pin 9)  ──[1 kΩ]──┘
Pi GND also to the adapter's REF and GND, tied together
```

```
python3 -u summit_latency.py --chan 32 --trials 500 | tee latency.log
```

It reports min, median, p95, p99 and max in ms. Compare p99 and max with the 50 ms decoder window. If GPIO access is denied, run it with `sudo ~/opensourceleg/.venv/bin/python ...`.

---

## Troubleshooting

| Symptom                                        | Likely cause and fix                                                                                                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `R04500-0094-WIFI` not visible (WiFi is off)   | WiFi has to be started from the web page. Connect the Summit's power supply to mains, plug Ethernet into the supply, open `192.168.42.1`, click Start WiFi, then unplug. |
| Pi can't see the network                       | Run `sudo nmcli dev wifi rescan`, wait 5 s, then `nmcli dev wifi list \| grep R04500`.                                                                                   |
| `could not connect` / `no data from ...`       | The Pi isn't on the Summit's network, TCP isn't ticked, or the Summit is still restarting.                                                                               |
| Connected but `samples` stays 0 or `no data`   | Hi-Res is off. Turn it on in Trellis.                                                                                                                                    |
| `behind` keeps climbing / data falls behind    | Too much data for WiFi. Turn off Raw, LFP and Spike in Trellis. Also check how far away the Trellis laptop is.                                                           |
| `other-stream packets` in `check_recording.py` | Streams besides Hi-Res were on during that recording.                                                                                                                    |
| `No module named numpy`                        | Activate the environment: `source ~/opensourceleg/.venv/bin/activate`.                                                                                                   |
| Frozen SSH session                             | Press Enter, then `~.`                                                                                                                                                   |

---

## Direct myoelectric control of the ankle

This replicates the DMC of Posh, Schmiedeler and Wensing, _IEEE TNSRE_ 31, 2023 (PDF in this folder), Sec. II-B:

- **EMG:** GAS and TA, rectified and averaged over 200 ms. Each is normalized to 0 at rest and 1 at maximum voluntary activation (MVA), giving u_p (GAS) and u_d (TA). Activity within 2 SD of rest is ignored.
- **Volitional input (Eq. 1):** u goes from −1 (full dorsiflexion) to +1 (full plantarflexion). It comes from the size of (u_p, u_d) and where that point lies between the two calibrated co-contraction lines, m_GAS and m_TA.
- **Torque:** τ = −K(θ − θ_max·u), with K = 0.05 Nm/deg per kg and θ_max = 15° (Table I). This is an impedance spring whose equilibrium follows u. `OSL_brand_connection_impedance.py` renders it in the Dephy firmware. `dmc_controller.py` sends it the equilibrium θ_max·u over UDP.

```
python3 dmc_calibrate.py --subject S01 --gas 1 --ta 2          # channels: 1,2 averaged; 1-2 bipolar
python3 dmc_controller.py --cal S01_dmc_cal.json --dry-run     # leg not driven; watch u
# then, in two terminals:
python3 OSL_brand_connection_impedance.py --mode ankle --weight-kg 72
python3 dmc_controller.py --cal S01_dmc_cal.json --record S01_T01.xipp
```

When u = 0 (relaxed), the ankle is a passive spring about neutral. If the EMG is stale, the controller does the same. The equilibrium is rate-limited to 150°/s, and on exit it ramps back to neutral. To tune sensitivity the way the paper did, edit `mva` in the JSON: raise it for less sensitive control, lower it for more. To run a recording through the same code offline: `python3 dmc.py --cal S01_dmc_cal.json --replay S01_T01.xipp`.

**Before the first powered run:**

- Check `PF_SIGN` in `dmc_controller.py`: with the leg off the body, a GAS contraction must point the toes down.
- `OSL_brand_connection_impedance.py` holds its last setpoint if packets stop. If `dmc_controller.py` is killed hard, the ankle stays wherever it was, so keep the e-stop in reach.

Where the paper leaves a choice open, `dmc.py` makes one and marks it with `Paper:`. These are: the m0 formula (implemented as the geometric bisector), clipping Eq. 1 beyond the calibration lines, MVA as the best 1 s average, and total-least-squares slope fits.

---

## Still to check before relying on it in experiments

- **Fixed delay.** Not measured yet; run `summit_latency.py`.
- **Long session in real conditions.** Run 30–60 min with the Pi in its mounted position and the Trellis laptop at its real distance. Check whether a weak Trellis link slows the Pi (watch `behind`).
- **After a Summit power cycle.** Check whether WiFi comes back on its own and whether Hi-Res-only stays set. If not, make both part of the startup routine; Trellis can reset the streams.
- **Decoder on the Pi.** Watch `decode` time and `overruns` in `controller_template.py`; decoding has to finish well within 50 ms.
- **Output side.** This setup covers EMG _into_ the Pi only. Whatever the controller drives is a separate link to test on its own. Stimulation through the Summit has no path from the Pi here, and anything that stimulates a person needs the usual protocol review first.
- **Undocumented settings.** The Explorer Config page shows `LOW_LATENCY_MODE = False` with no checkbox. It's not documented in the manuals we have.
