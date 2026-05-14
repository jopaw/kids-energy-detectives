# ⚡ Vidyuth's Energy Detectives

A kid-friendly STEM project that turns a **Raspberry Pi Zero 2 W** into a hands-on
energy explorer. Four stations — **Sun, Wind, Heat, Lemon** — each generate a
small voltage that the Pi reads through an MCP3008 ADC. The readings stream
live to a single-page web dashboard with graphs, a 4-station live slide, real-life
example animations, and an annotated wiring diagram.

The Pi also acts as its own Wi-Fi hotspot, so anyone in the room can join
"EnergyDetectives" Wi-Fi and the dashboard pops up automatically on their phone.

---

## What's in this repo

```
kids-energy.html                 # the entire single-page web app (React + Babel via CDN)
pi/
  energy_detective.py            # Python HTTP + SPI server for the Pi
  setup-hotspot.sh               # one-shot installer: AP + DNS + service + QR + mDNS
  energy-detective.service       # baseline systemd unit (overwritten by setup-hotspot.sh)
  vendor/                        # optional — vendored React/Babel for fully-offline use
README.md                        # you are here
```

---

## Hardware

| Part | What it does |
|---|---|
| Raspberry Pi Zero 2 W | The brain. Runs Python, hosts the dashboard, broadcasts the Wi-Fi. |
| MCP3008 ADC chip (DIP-16) | 8-channel analog-to-digital converter. Pi has no analog inputs, this fixes that. |
| Small half-size breadboard | Solder-free assembly. |
| 6 × jumper wires (6 colours) | Red, black, yellow, green, blue, purple — one per signal. |
| Solar panel (2–5 V) | Sun station — light → electricity. |
| Small DC hobby motor + propeller | Wind station — motion → electricity. |
| Peltier tile (TEC1-12706) | Heat station — temperature gap → electricity. |
| 1 lemon + zinc nail + copper coin | Lemon station — chemistry → electricity. |
| Resistors | 2 × 22 kΩ, 3 × 10 kΩ, 1 × 1 kΩ (¼ W). |
| Diodes | 4 × 1N4148 small signal. |
| NPN transistor | 1 × 2N3904 (Heat amplifier). |

No capacitors are required.

---

## Assumptions

Read these before you start — most "why doesn't it work" questions trace back here.

1. **Raspberry Pi OS Bookworm or newer** is installed. The hotspot setup uses
   `nmcli` (NetworkManager) which is standard on Bookworm; older releases work
   if you `sudo apt install network-manager` first.
2. **SPI is enabled** in `raspi-config` (Interface Options → SPI → Enable).
3. **`python3-spidev` is installed** (`sudo apt install python3-spidev`). The
   server requires it; if spidev is missing it exits immediately.
4. **`python3-qrcode` is installed** for the Wi-Fi QR code (the hotspot setup
   script installs it). Without it the QR endpoint returns a small placeholder
   SVG instead of a real code.
5. **You log into the Pi as a normal user**, not root. The systemd unit runs
   as `$SUDO_USER` (the user who ran `setup-hotspot.sh`).
6. **The Pi only has one Wi-Fi radio.** Switching it to AP mode disconnects
   any existing Wi-Fi connection. Run the installer over Ethernet or via USB
   gadget if you can't lose Wi-Fi.
7. **The 40-pin GPIO header sits along the long edge.** Pin 1 is the corner
   pin closest to the SD-card slot.
8. **The MCP3008 has its notch / dot at the top** when you wire it. With the
   notch up, **CH0–CH7 are on the LEFT** of the chip and
   **VDD / VREF / AGND / CLK / DOUT / DIN / CS / DGND are on the RIGHT.**
   The dashboard's wiring diagram is drawn in this orientation.
9. **The solar panel produces ≲ 5 V open-circuit.** Bigger panels need
   bigger divider resistors (see Debugging → Station 1 below).

---

## Wiring overview

All values quoted are in the wiring section of the dashboard's `🔌 Build` tab.

**Pi → MCP3008 (six wires + two short jumpers):**

| Pi pin | Function | Wire colour | MCP pin | Function |
|---|---|---|---|---|
| 1  | 3V3       | red    | 16 | VDD |
| 16 ↔ 15 jumper (3V3 → VREF, red) |
| 6  | GND       | black  | 9  | DGND |
| 9  ↔ 14 jumper (DGND → AGND, black) |
| 23 | SCLK / GPIO11 | yellow | 13 | CLK |
| 21 | MISO / GPIO9  | green  | 12 | DOUT |
| 19 | MOSI / GPIO10 | blue   | 11 | DIN |
| 24 | CE0  / GPIO8  | purple | 10 | CS  |

**Stations → MCP3008 (per-station signal-conditioning circuits):**

| Station | Channel | Conditioning |
|---|---|---|
| ☀️ Sun  | CH0 (pin 1) | R1 22 kΩ + R2 10 kΩ divider, D1 1N4148 to CH0 |
| 💨 Wind | CH1 (pin 2) | D2 1N4148 rectifier, R3 22 kΩ + R4 10 kΩ divider |
| 🔥 Heat | CH2 (pin 3) | D3 1N4148, R5 10 kΩ base resistor, Q1 2N3904 amp, R6 1 kΩ pull-up to 3V3 |
| 🍋 Lemon | CH3 (pin 4) | D4 1N4148 protection only |

---

## Setup (3 paths)

### Path A — full kid-friendly hotspot (recommended once everything works)

On the Pi, while it still has internet:

```bash
sudo raspi-config           # Interface Options → SPI → Enable → reboot
sudo apt install -y python3-spidev git
git clone <your repo URL> ~/energy-detective
cd ~/energy-detective
sudo bash pi/setup-hotspot.sh
```

The script:

1. Downloads React + Babel into `pi/vendor/` so the dashboard works fully offline.
2. Creates a NetworkManager AP profile (`SSID=EnergyDetectives`,
   `password=lightning`, 2.4 GHz, IP `10.42.0.1/24`).
3. Drops `/etc/NetworkManager/dnsmasq-shared.d/energy-detective.conf` with
   `address=/#/10.42.0.1` so every DNS query on the hotspot returns the Pi
   (captive-portal behaviour).
4. Installs `avahi-daemon` (mDNS) and `python3-qrcode`, sets hostname to
   `energy`.
5. Installs the systemd service to listen on **port 80** with
   `CAP_NET_BIND_SERVICE`.

When it's done, kids join the `EnergyDetectives` Wi-Fi and the dashboard pops
up automatically on most phones. If it doesn't, point a browser at
`http://energy.local` or `http://10.42.0.1`.

To undo everything: `sudo bash pi/setup-hotspot.sh --uninstall`.

### Path B — Pi on your home Wi-Fi (good for development)

```bash
sudo apt install -y python3-spidev
python3 pi/energy_detective.py
```

Then open `http://<pi-ip>:8080` on a phone or laptop on the same Wi-Fi (find
the Pi's IP with `hostname -I` on the Pi).

### Path C — first-boot, no monitor, no Wi-Fi (USB gadget)

Useful when you don't have a screen for the Pi yet.

1. Flash Raspberry Pi OS Lite with Imager, **use OS customization** to set
   hostname, username, password, SSH on, and skip Wi-Fi.
2. Before ejecting the SD card, in the boot partition (`bootfs`):
   - In `config.txt`, append:
     ```
     [all]
     dtoverlay=dwc2
     ```
   - In `cmdline.txt` (one long line) insert `modules-load=dwc2,g_ether`
     right after `rootwait`.
3. Plug the Pi into your computer using the **middle (data)** micro-USB port,
   not the outer `PWR IN`.
4. Wait ~90 s, then `ssh <user>@raspberrypi.local` (or `ssh <user>@<usb-gadget-ip>`
   if mDNS doesn't work — find the IP with `arp -a`).

---

## How it works

### Server

`pi/energy_detective.py` is a stdlib `http.server` HTTP listener with a
background thread that reads the MCP3008 over SPI every 300 ms.

**Endpoints:**

| Path | Returns |
|---|---|
| `/` (or anything not below) | The dashboard HTML (with React/Babel CDN URLs rewritten to `/vendor/*` when those files exist). Catch-all behaviour is what makes captive-portal probes from iOS / Android / Windows pop the dashboard automatically. |
| `/api/readings` | JSON `{solar, wind, heat, lemon, ts, source: "spi"}`. Sampled every ~0.3 s; polled by the dashboard every 500 ms. |
| `/api/wifi`  | JSON `{ssid, password, ip, hostname, captive}`. Used by the dashboard to render the "Show your friends how to join" card and the QR code's caption. |
| `/api/wifi-qr.svg` | SVG QR code encoding `WIFI:T:WPA;S:<ssid>;P:<password>;;` — your phone joins automatically when it scans it. |
| `/api/health` | `{"ok":true}` |
| `/vendor/<file>` | React, ReactDOM, Babel-standalone if they've been vendored. |

**ADC scaling.** Each station's raw 10-bit ADC reading (`0..1023` → `0..3.3 V`)
is multiplied by a per-station factor:

```python
"solar": {"channel": 0, "scale": 3.2,  "unit": "V"},   # un-divide R1+R2 / R2 = 32/10
"wind":  {"channel": 1, "scale": 3.2,  "unit": "V"},
"heat":  {"channel": 2, "scale": 1000, "unit": "mV"},
"lemon": {"channel": 3, "scale": 1.0,  "unit": "V"},
```

If you change the wiring (different divider, no divider) update this dict and
restart the service.

### Dashboard

`kids-energy.html` is a single-file React app served at `/`. Top-to-bottom
sections (with a sticky anchor nav):

- **📶 Join** — Wi-Fi SSID, password, QR code.
- **🕵️ Mission** — short intro card.
- **📊 Dashboard** — 4 ring gauges + energy-flow tiles + live status badge.
- **⚡ Stations** — the 4 overview cards, then four detailed sections (one
  per station) each with a live reading, a 30-second voltage graph, "how to
  use this station" bullets, and 3 real-life example animations.
- **🔌 Build** — annotated wiring diagram (Pi 40-pin header on the right,
  MCP3008 in physical orientation in the middle, station inputs on the
  left), per-port description list, 4 station signal-conditioning sub-circuits,
  wire colour key, extra parts list, 11-step build guide, safety rules.
- **🧰 Parts** — the headline parts list.

The dashboard polls `/api/readings` every 500 ms and keeps a rolling 60-sample
history per station, which feeds the time-series graph component.

---

## Debugging

### "All four channels read 0.0 V on the dashboard"

This is the classic "chip can't talk to Pi" symptom. From an SSH session on the Pi:

```bash
ls /dev/spidev*       # must show /dev/spidev0.0 — if missing, enable SPI
curl http://localhost/api/readings   # check JSON has source: "spi"
python3 -c "
import spidev
s = spidev.SpiDev(); s.open(0,0); s.max_speed_hz = 1350000
for ch in range(4):
    r = s.xfer2([1,(8+ch)<<4,0])
    print(f'CH{ch} bytes={r}')
"
```

- `bytes=[0,0,0]` for every channel → MISO is the problem. Most common causes
  (in order):
  1. Chip is plugged in with notch DOWN. Rotate 180° (and move all wires).
  2. Pi wires went onto the chip's CH side instead of VDD/SPI side. Verify
     against the wiring diagram in the Build tab.
  3. Missing VREF jumper (pin 16 → pin 15).
  4. Missing AGND jumper (pin 9 → pin 14).
  5. MISO wire in the wrong hole (Pi pin 21 → MCP pin 12).
- `bytes=[255,255,255]` everywhere → MISO floating. Same fix as above but
  the line is open instead of shorted.

### "Only Station 1 (Sun) reads 0"

The panel is producing voltage but the chip's pin doesn't see it. Probe each
node in the solar circuit with a multimeter (black probe on GND):

| Test point | Expected |
|---|---|
| Panel + terminal | ≥ 1.5 V in good light |
| R1's panel-side leg | same as above |
| Junction of R1, R2, D1 | about ⅓ of panel voltage |
| D1 cathode (stripe side) | junction − 0.2 V |
| MCP pin 1 (CH0) | same as D1 cathode |

If junction collapses to 0.4 V or so when R2 is connected, your panel is too
small to drive the 32 kΩ divider. Either:

- Replace R1, R2 with bigger values (e.g. R1 = 220 kΩ, R2 = 100 kΩ — same
  ratio, 10× less loading), OR
- Skip the divider entirely (panel + → D1 → CH0) and set `"scale": 1.0` for
  solar in `energy_detective.py`. Safe if the panel's open-circuit voltage
  never exceeds 3.3 V.

### "The dashboard shows 'no Pi connection — retrying…'"

- The page can't reach `/api/readings`. Check:
- Are you connected to the Pi's Wi-Fi (or the same Wi-Fi as the Pi)?
- Is the server running? `sudo systemctl status energy-detective`.
- Is anything else hogging port 80? `sudo ss -lntp | grep :80`.
- Logs: `sudo journalctl -fu energy-detective`.

### "The captive portal popup doesn't appear"

Some phones (older Android, sometimes iOS in airplane mode) ignore captive
portal probes. Have the kid manually open `http://energy.local` or
`http://10.42.0.1`. If even those don't resolve:

- `http://energy.local` requires mDNS — confirm `avahi-daemon` is running
  on the Pi.
- `http://10.42.0.1` should always work as long as the phone got a DHCP lease.

### "I forgot the Pi password"

Pop the SD card into your computer and either:

- Re-flash with Imager's customization screen (clean slate), or
- Boot to single-user mode: append `init=/bin/sh` to `cmdline.txt`, boot,
  `mount -o remount,rw / && passwd <user>`, then remove the edit and reboot.

### "Bonjour Print Services won't install on Windows"

You don't need it. Skip mDNS and connect by IP:

```cmd
ipconfig            # find the new "USB Ethernet / RNDIS Gadget" adapter
arp -a              # look for a MAC starting with b8-27-eb, dc-a6-32, 2c-cf-67 or e4-5f-01
ssh <user>@<that-ip>
```

### "USB gadget mode isn't working"

- Cable in the **middle** micro-USB port, not the outer `PWR IN`.
- `config.txt` has `dtoverlay=dwc2` under a section that applies to the
  Pi Zero 2 W (e.g. `[all]`). The default `[cm5]` section does NOT apply
  to Pi Zero 2 W.
- `cmdline.txt` has `modules-load=dwc2,g_ether` on the same single line
  as everything else.
- Wait ≥ 90 s after plugging in for the first boot. Subsequent boots are
  ~25 s.

### "I want to develop the page without the Pi"

The server requires real hardware. For pure HTML/CSS iteration, open
`kids-energy.html` directly in a browser via raw.githack or similar — the
dashboard will show "WAITING" and "no Pi connection — retrying…" but
everything except the live readings still renders correctly.

---

## Useful commands once it's running

```bash
sudo systemctl status  energy-detective       # is it running?
sudo journalctl -fu    energy-detective       # follow logs
sudo systemctl restart energy-detective       # after editing the Python
nmcli con show                                # list Wi-Fi profiles
nmcli con up EnergyHotspot                    # bring the AP back up
sudo bash pi/setup-hotspot.sh --uninstall     # remove everything
```

---

## Credits

Made with ⚡ and ❤️ for curious kids · A Raspberry Pi Zero 2 W STEM project.
