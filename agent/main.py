"""ScreenSolve desktop agent.

Captures the screen on a global hotkey (default Ctrl+Alt+S) and uploads
the screenshot to the ScreenSolve server for analysis. Silent otherwise.

Run:  python3 agent/main.py        (config in agent/config.yaml)
"""

import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from PIL import ImageGrab

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    defaults = {
        "server_url": "https://screensolve-wwpbqn.drytis.dev",
        "mode": "telegram",  # telegram = capture only when 'c' sent in the bot chat
        "interval": 30,  # seconds between captures in periodic mode
        "hotkey": "ctrl+alt+s",
        "capture": "screen",  # "screen" (full primary) or "window" (active window)
        "poll_seconds": 0.7,  # how often to check for Telegram commands (lower = snappier)
    }
    if CONFIG_PATH.exists():
        defaults.update(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
    return defaults


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _grab_cli() -> bytes:
    """Fallback captures via native screenshot tools (Wayland etc.)."""
    import shutil
    import subprocess

    tools = [
        ("grim", ["grim", "-"]),  # wlroots / Sway / Hyprland (Wayland)
        ("gnome-screenshot", ["gnome-screenshot", "-f"]),  # GNOME
        ("spectacle", ["spectacle", "-b", "-n", "-o"]),  # KDE
        ("scrot", ["scrot", "-o", "-"]),  # X11
        ("import", ["import", "-window", "root", "-"]),  # ImageMagick (X11)
    ]
    for name, cmd in tools:
        if not shutil.which(name):
            continue
        out = "-" if cmd[-1] == "-" else "/tmp/screensolve_shot.png"
        try:
            result = subprocess.run(
                cmd[:-1] + [out], capture_output=True, timeout=15
            )
            data = (
                result.stdout
                if out == "-"
                else (open(out, "rb").read() if result.returncode == 0 else b"")
            )
            if data:
                return data
        except Exception:
            continue
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    hint = (
        "sudo apt install grim  (or use a GNOME session)"
        if session == "wayland"
        else "scrot (X11) — and make sure DISPLAY is set"
    )
    raise RuntimeError(
        f"No working screenshot tool. For your {session} session install: {hint}"
    )


def capture_screen(mode: str) -> bytes:
    if mode == "window":
        try:
            import pygetwindow  # optional

            win = pygetwindow.getActiveWindow()
            if win and win.box:
                shot = ImageGrab.grab(bbox=win.box)
                buf = io.BytesIO()
                shot.save(buf, "PNG")
                return buf.getvalue()
        except Exception:
            pass  # fall back to full screen
    try:
        shot = ImageGrab.grab()
        buf = io.BytesIO()
        shot.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return _grab_cli()


def upload(server_url: str, png: bytes) -> None:
    url = server_url.rstrip("/") + "/api/captures"
    try:
        r = httpx.post(
            url,
            files={"file": ("screen.png", png, "image/png")},
            timeout=60,
        )
        if r.status_code == 200:
            cid = r.json().get("id")
            log(f"accepted — view the solution at {server_url} (capture #{cid})")
        else:
            log(f"upload failed: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        log(f"upload failed: {e}")


def png_hash(png: bytes) -> str:
    import hashlib

    return hashlib.sha1(png).hexdigest()


def run_telegram_mode(cfg: dict) -> None:
    """Poll the server for 'capture' commands; upload as session part. No analysis here."""
    poll_s = max(0.3, float(cfg.get("poll_seconds", 0.7)))
    base = cfg["server_url"].rstrip("/")
    log("TELEGRAM MODE — captures happen ONLY when you send 'c' in the bot chat.")
    log("No automatic captures, no screen-change detection. Ctrl+C to quit.")
    while True:
        try:
            r = httpx.get(f"{base}/api/agent/poll", params={"agent_id": "default"}, timeout=10)
            if r.json().get("command") == "capture":
                log("command received — capturing NOW…")
                png = capture_screen(cfg.get("capture", "screen"))
                up = httpx.post(
                    f"{base}/api/agent/capture",
                    files={"file": ("part.png", png, "image/png")},
                    timeout=60,
                )
                if up.status_code == 200:
                    d = up.json()
                    log(f"part #{d['parts']} stored in session {d['session_id']}")
                else:
                    log(f"upload failed: HTTP {up.status_code}")
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"poll error: {e}")
        time.sleep(poll_s)


def run_periodic(cfg: dict) -> None:
    interval = max(5, int(cfg.get("interval", 30)))
    log(f"Periodic mode: capturing every {interval}s. Ctrl+C to quit.")
    last_hash = None
    while True:
        try:
            png = capture_screen(cfg.get("capture", "screen"))
            h = png_hash(png)
            if h == last_hash:
                log("screen unchanged — skipped")
            else:
                log("screen changed — uploading…")
                upload(cfg["server_url"], png)
                last_hash = h
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"capture failed: {e}")
        time.sleep(interval)


def main() -> int:
    cfg = load_config()
    mode = cfg.get("mode", "telegram")

    if mode == "telegram":
        try:
            run_telegram_mode(cfg)
        except KeyboardInterrupt:
            log("bye")
        return 0

    if mode == "periodic":
        try:
            run_periodic(cfg)
        except KeyboardInterrupt:
            log("bye")
        return 0

    try:
        import keyboard
    except ImportError:
        log("the 'keyboard' package is required for hotkey mode: pip install keyboard "
            "(or switch to mode: periodic in config.yaml)")
        return 1

    combo = cfg["hotkey"]
    log(f"ScreenSolve agent running — press {combo} to capture. Ctrl+C to quit.")

    def on_hotkey():
        try:
            log("capturing screen…")
            png = capture_screen(cfg.get("capture", "screen"))
            log("uploading…")
            upload(cfg["server_url"], png)
        except Exception as e:  # noqa: BLE001
            log(f"capture failed: {e}")

    keyboard.add_hotkey(combo, on_hotkey)
    try:
        keyboard.wait()  # forever
    except KeyboardInterrupt:
        log("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
