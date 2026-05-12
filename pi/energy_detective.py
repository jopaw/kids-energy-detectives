#!/usr/bin/env python3
"""Energy Detectives - Raspberry Pi Zero 2 W server.

Reads the four station voltages from an MCP3008 ADC on the Pi's SPI bus
and serves the kids-energy.html dashboard to anyone on the same Wi-Fi.

Setup on a Pi Zero 2 W (Raspberry Pi OS, 32- or 64-bit):
    1. sudo raspi-config  ->  Interface Options  ->  SPI  ->  Enable
    2. sudo apt update && sudo apt install -y python3-spidev
    3. Copy kids-energy.html next to this script (or one level up).
    4. python3 energy_detective.py
    5. From any phone / laptop on the same Wi-Fi, open
           http://<pi-ip>:8080
       Find the Pi's IP with `hostname -I` on the Pi.

If spidev is missing the server still starts in simulation mode so the
page can be developed and demoed without the hardware attached.

Offline use: drop the three vendored files
    vendor/react.production.min.js
    vendor/react-dom.production.min.js
    vendor/babel.min.js
next to this script and the server will rewrite the CDN URLs in the
served HTML to point at /vendor/* so the page works without internet.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import spidev
    HAVE_SPI = True
except ImportError:
    HAVE_SPI = False

HOST = "0.0.0.0"
PORT = 8080
HERE = Path(__file__).resolve().parent

HTML_CANDIDATES = [HERE / "kids-energy.html", HERE.parent / "kids-energy.html"]
VENDOR_DIR = HERE / "vendor"

VREF = 3.3
ADC_MAX = 1023.0
SAMPLE_INTERVAL = 0.3  # seconds between ADC reads

# Channel + display scaling for each station. The scale factors un-do the
# voltage dividers in the wiring diagram so the page can show the real
# panel / motor / Peltier / lemon voltage instead of the divided value.
#   Solar:  R1=22k, R2=10k  =>  V_real = V_adc * (R1+R2)/R2 = 3.2 x
#   Wind:   R3=22k, R4=10k  =>  same 3.2 x
#   Heat:   reported in mV at the transistor collector
#   Lemon:  direct, no divider
STATIONS = {
    "solar": {"channel": 0, "scale": 3.2,  "unit": "V"},
    "wind":  {"channel": 1, "scale": 3.2,  "unit": "V"},
    "heat":  {"channel": 2, "scale": 1000, "unit": "mV"},
    "lemon": {"channel": 3, "scale": 1.0,  "unit": "V"},
}

_readings: dict[str, float] = {k: 0.0 for k in STATIONS}
_readings_lock = threading.Lock()


def _open_spi():
    spi = spidev.SpiDev()
    spi.open(0, 0)              # bus 0, CE0 (matches the wiring diagram)
    spi.max_speed_hz = 1_350_000
    spi.mode = 0
    return spi


def _read_mcp3008(spi, channel: int) -> int:
    if channel < 0 or channel > 7:
        raise ValueError(f"MCP3008 channel out of range: {channel}")
    # Start bit, single-ended | channel, padding.
    resp = spi.xfer2([1, (8 + channel) << 4, 0])
    return ((resp[1] & 0x03) << 8) | resp[2]


def _resolve_html_path() -> Path | None:
    for p in HTML_CANDIDATES:
        if p.is_file():
            return p
    return None


def sampler_loop() -> None:
    spi = _open_spi() if HAVE_SPI else None
    while True:
        snapshot = {}
        for name, cfg in STATIONS.items():
            if spi is not None:
                try:
                    raw = _read_mcp3008(spi, cfg["channel"])
                    volts = (raw / ADC_MAX) * VREF
                except OSError:
                    volts = 0.0
            else:
                # Gentle wobble around mid-rail so the page still animates.
                t = time.monotonic() + cfg["channel"] * 0.7
                volts = 1.0 + 0.4 * ((t % 2.0) - 1.0)
            snapshot[name] = round(volts * cfg["scale"], 3)
        with _readings_lock:
            _readings.update(snapshot)
        time.sleep(SAMPLE_INTERVAL)


def render_html() -> bytes:
    path = _resolve_html_path()
    if path is None:
        msg = (
            "<h1>kids-energy.html not found</h1>"
            "<p>Place kids-energy.html next to energy_detective.py "
            "(or in the parent directory).</p>"
        )
        return msg.encode("utf-8")

    html = path.read_text(encoding="utf-8")
    vendor_map = {
        "https://unpkg.com/react@18.3.1/umd/react.production.min.js": "react.production.min.js",
        "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js": "react-dom.production.min.js",
        "https://unpkg.com/@babel/standalone@7.24.7/babel.min.js": "babel.min.js",
    }
    for cdn, fname in vendor_map.items():
        if (VENDOR_DIR / fname).is_file():
            html = html.replace(cdn, f"/vendor/{fname}")
    return html.encode("utf-8")


VENDOR_FILES = {
    "react.production.min.js": "application/javascript; charset=utf-8",
    "react-dom.production.min.js": "application/javascript; charset=utf-8",
    "babel.min.js": "application/javascript; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "EnergyDetective/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path in ("/", "/kids-energy.html"):
            return self._send(200, render_html(), "text/html; charset=utf-8")

        if self.path == "/api/readings":
            with _readings_lock:
                payload = dict(_readings)
            payload["ts"] = time.time()
            payload["source"] = "spi" if HAVE_SPI else "simulated"
            body = json.dumps(payload).encode("utf-8")
            return self._send(200, body, "application/json")

        if self.path == "/api/health":
            return self._send(200, b'{"ok":true}', "application/json")

        if self.path.startswith("/vendor/"):
            name = self.path[len("/vendor/"):]
            if name in VENDOR_FILES and (VENDOR_DIR / name).is_file():
                body = (VENDOR_DIR / name).read_bytes()
                return self._send(200, body, VENDOR_FILES[name])

        return self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> None:
    if not HAVE_SPI:
        print("[warn] spidev not installed - running in simulation mode "
              "(sudo apt install python3-spidev to enable real readings)")
    threading.Thread(target=sampler_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    html_path = _resolve_html_path()
    print(f"Energy Detective server: http://{HOST}:{PORT}")
    print(f"  Mode:  {'SPI (real Pi)' if HAVE_SPI else 'simulation'}")
    print(f"  HTML:  {html_path if html_path else 'NOT FOUND - drop kids-energy.html beside this script'}")
    if VENDOR_DIR.is_dir():
        print(f"  Vendor JS: {VENDOR_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        srv.server_close()


if __name__ == "__main__":
    main()
