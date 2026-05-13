#!/usr/bin/env python3
"""Energy Detectives - Raspberry Pi Zero 2 W server.

Reads the four station voltages from an MCP3008 ADC on the Pi's SPI bus
and serves the kids-energy.html dashboard to anyone on the same Wi-Fi.

Quick start (no hotspot, joins your home Wi-Fi):
    sudo raspi-config -> Interface Options -> SPI -> Enable
    sudo apt install -y python3-spidev
    python3 energy_detective.py
    # then open  http://<pi-ip>:8080  on any device on the same Wi-Fi

Full hotspot setup (kid-friendly, no router needed):
    sudo bash pi/setup-hotspot.sh

If spidev is missing the server still starts in simulation mode so the
page can be developed and demoed without the hardware attached.

Offline mode: if pi/vendor/{react,react-dom,babel}.js exist the server
rewrites the page's CDN URLs to point at /vendor/* so the dashboard
loads with no internet at all (required when the Pi is its own
hotspot). setup-hotspot.sh downloads these for you.
"""
from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_PORT = 8080
FALLBACK_PORT = 8080

HERE = Path(__file__).resolve().parent
HTML_CANDIDATES = [HERE / "kids-energy.html", HERE.parent / "kids-energy.html"]
VENDOR_DIR = HERE / "vendor"

VREF = 3.3
ADC_MAX = 1023.0
SAMPLE_INTERVAL = 0.3

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

# Captive-portal probe URLs from iOS / macOS / Android / Windows. We deliberately
# return the dashboard HTML for these so the OS pops the "sign-in" sheet and
# shows the page immediately when a phone joins the hotspot.
CAPTIVE_PORTAL_PATHS = {
    "/hotspot-detect.html",                       # iOS, macOS
    "/library/test/success.html",                 # iOS
    "/generate_204", "/gen_204",                  # Android
    "/connecttest.txt",                           # Windows
    "/ncsi.txt",                                  # Windows NCSI
    "/redirect",                                  # Windows fallback
    "/check_network_status.txt",                  # Firefox / Mozilla
    "/success.txt",                               # Various
}

_readings: dict[str, float] = {k: 0.0 for k in STATIONS}
_readings_lock = threading.Lock()

# Populated from CLI in main(); used by /api/wifi.
RUNTIME = {
    "ap_ssid": "EnergyDetectives",
    "ap_password": "lightning",
    "ap_ip": "10.42.0.1",
    "hostname": "energy.local",
    "captive": False,
}


def _open_spi():
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1_350_000
    spi.mode = 0
    return spi


def _read_mcp3008(spi, channel: int) -> int:
    if channel < 0 or channel > 7:
        raise ValueError(f"MCP3008 channel out of range: {channel}")
    resp = spi.xfer2([1, (8 + channel) << 4, 0])
    return ((resp[1] & 0x03) << 8) | resp[2]


def _resolve_html_path() -> Path | None:
    for p in HTML_CANDIDATES:
        if p.is_file():
            return p
    return None


def sampler_loop() -> None:
    if not HAVE_SPI:
        raise RuntimeError(
            "spidev is not installed. Install it with `sudo apt install python3-spidev` "
            "(this server runs in live mode only — no simulation)."
        )
    spi = _open_spi()
    while True:
        snapshot = {}
        for name, cfg in STATIONS.items():
            try:
                raw = _read_mcp3008(spi, cfg["channel"])
                volts = (raw / ADC_MAX) * VREF
            except OSError:
                volts = 0.0
            snapshot[name] = round(volts * cfg["scale"], 3)
        with _readings_lock:
            _readings.update(snapshot)
        time.sleep(SAMPLE_INTERVAL)


_html_cache: dict[str, object] = {"mtime": 0.0, "bytes": b""}

def render_html() -> bytes:
    path = _resolve_html_path()
    if path is None:
        return (b"<h1>kids-energy.html not found</h1>"
                b"<p>Place kids-energy.html next to energy_detective.py.</p>")
    mtime = path.stat().st_mtime
    if _html_cache["mtime"] == mtime:
        return _html_cache["bytes"]
    html = path.read_text(encoding="utf-8")
    vendor_map = {
        "https://unpkg.com/react@18.3.1/umd/react.production.min.js": "react.production.min.js",
        "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js": "react-dom.production.min.js",
        "https://unpkg.com/@babel/standalone@7.24.7/babel.min.js": "babel.min.js",
    }
    for cdn, fname in vendor_map.items():
        if (VENDOR_DIR / fname).is_file():
            html = html.replace(cdn, f"/vendor/{fname}")
    body = html.encode("utf-8")
    _html_cache["mtime"] = mtime
    _html_cache["bytes"] = body
    return body


VENDOR_FILES = {
    "react.production.min.js": "application/javascript; charset=utf-8",
    "react-dom.production.min.js": "application/javascript; charset=utf-8",
    "babel.min.js": "application/javascript; charset=utf-8",
}


def render_wifi_qr_svg() -> bytes:
    """Return an SVG QR code that joins the AP when scanned by a phone camera.

    Encodes the standard WIFI:T:WPA;S:<ssid>;P:<password>;; payload that iOS,
    Android and recent macOS recognise as a Wi-Fi join request.
    """
    payload = (
        f"WIFI:T:WPA;S:{RUNTIME['ap_ssid']};P:{RUNTIME['ap_password']};;"
    )
    try:
        import qrcode
        import qrcode.image.svg
        import io
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(payload, image_factory=factory, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue()
    except ImportError:
        # Fallback if python3-qrcode isn't installed yet.
        msg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">'
            '<rect width="200" height="200" fill="#fafaf2" stroke="#888"/>'
            '<text x="100" y="92" font-family="sans-serif" font-size="11" text-anchor="middle" fill="#444">QR library not installed</text>'
            '<text x="100" y="112" font-family="sans-serif" font-size="10" text-anchor="middle" fill="#666">sudo apt install python3-qrcode</text>'
            '</svg>'
        )
        return msg.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "EnergyDetective/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        self._send(200, render_html(), "text/html; charset=utf-8")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/readings":
            with _readings_lock:
                payload = dict(_readings)
            payload["ts"] = time.time()
            payload["source"] = "spi"
            return self._send(200, json.dumps(payload).encode("utf-8"),
                              "application/json")

        if path == "/api/wifi":
            payload = {
                "ssid": RUNTIME["ap_ssid"],
                "password": RUNTIME["ap_password"],
                "ip": RUNTIME["ap_ip"],
                "hostname": RUNTIME["hostname"],
                "captive": RUNTIME["captive"],
            }
            return self._send(200, json.dumps(payload).encode("utf-8"),
                              "application/json")

        if path == "/api/health":
            return self._send(200, b'{"ok":true}', "application/json")

        if path == "/api/wifi-qr.svg":
            return self._send(200, render_wifi_qr_svg(), "image/svg+xml; charset=utf-8")

        if path.startswith("/vendor/"):
            name = path[len("/vendor/"):]
            if name in VENDOR_FILES and (VENDOR_DIR / name).is_file():
                body = (VENDOR_DIR / name).read_bytes()
                return self._send(200, body, VENDOR_FILES[name])
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        # Anything else (root, /kids-energy.html, captive-portal probes, or
        # any random URL that the DNS catch-all redirected here) gets the
        # dashboard. Returning HTML rather than the expected "success"
        # response is exactly what causes the OS to pop the captive-portal
        # sheet with our page inside.
        return self._serve_html()


def _bind(port: int) -> tuple[ThreadingHTTPServer, int]:
    """Try to bind to `port`; fall back to FALLBACK_PORT on permission or
    address-in-use errors so the script never silently dies."""
    try:
        return ThreadingHTTPServer((HOST, port), Handler), port
    except PermissionError:
        if port == FALLBACK_PORT:
            raise
        print(f"[warn] no permission to bind port {port}; trying {FALLBACK_PORT}.")
        print(f"       (run with sudo or grant the capability:")
        print(f"        sudo setcap 'cap_net_bind_service=+ep' $(realpath $(which python3)))")
        return ThreadingHTTPServer((HOST, FALLBACK_PORT), Handler), FALLBACK_PORT
    except OSError as e:
        if e.errno in (98, 99) and port != FALLBACK_PORT:  # in use / cannot assign
            print(f"[warn] port {port} unavailable ({e}); trying {FALLBACK_PORT}.")
            return ThreadingHTTPServer((HOST, FALLBACK_PORT), Handler), FALLBACK_PORT
        raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Energy Detectives Pi server")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="TCP port to listen on (default: 8080; use 80 with sudo / capabilities)")
    p.add_argument("--ap-ssid", default=os.environ.get("AP_SSID", "EnergyDetectives"),
                   help="Wi-Fi SSID shown by the dashboard's 'Connect your friends' card")
    p.add_argument("--ap-password", default=os.environ.get("AP_PASSWORD", "lightning"),
                   help="Wi-Fi password shown on the dashboard")
    p.add_argument("--ap-ip", default=os.environ.get("AP_IP", "10.42.0.1"),
                   help="Pi's IP when running as a hotspot (used in the friendly URL)")
    p.add_argument("--hostname", default=os.environ.get("HOSTNAME_LOCAL", "energy.local"),
                   help="mDNS hostname the dashboard suggests in instructions")
    p.add_argument("--captive", action="store_true",
                   help="Mark this server as a captive-portal AP (informational; flips a flag in /api/wifi)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    RUNTIME["ap_ssid"]     = args.ap_ssid
    RUNTIME["ap_password"] = args.ap_password
    RUNTIME["ap_ip"]       = args.ap_ip
    RUNTIME["hostname"]    = args.hostname
    RUNTIME["captive"]     = args.captive

    if not HAVE_SPI:
        print("[error] spidev is not installed; this server is live-mode only.")
        print("        Install it with:  sudo apt install python3-spidev")
        sys.exit(1)
    threading.Thread(target=sampler_loop, daemon=True).start()

    srv, bound_port = _bind(args.port)
    html_path = _resolve_html_path()
    print(f"Energy Detective server: http://{HOST}:{bound_port}")
    print(f"  Mode:   live (MCP3008 over SPI)")
    print(f"  HTML:   {html_path if html_path else 'NOT FOUND - drop kids-energy.html beside this script'}")
    print(f"  Hotspot Wi-Fi: SSID={RUNTIME['ap_ssid']!r}  password={RUNTIME['ap_password']!r}  ip={RUNTIME['ap_ip']}")
    if VENDOR_DIR.is_dir():
        print(f"  Vendor JS: {VENDOR_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        srv.server_close()


if __name__ == "__main__":
    main()
