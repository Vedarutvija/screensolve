# ScreenSolve — Desktop Agent

Captures your screen when you press the hotkey and uploads it to your ScreenSolve
server, which detects the Python coding question (and any code you were writing)
and shows an optimized step-by-step solution on the mobile dashboard.

## Setup (on your computer, one time)

```bash
pip install -r agent/requirements.txt
```

> **macOS** needs Screen Recording + Accessibility permissions for your terminal
> in System Settings → Privacy & Security. **Windows** and **Linux (X11)** work
> out of the box.

## Run

```bash
python3 agent/main.py
```

### Periodic mode (default — recommended)

The agent captures your screen every **30 seconds** automatically and uploads it
only when the screen **changed** (identical screenshots are skipped, so a static
screen costs nothing). If the hotkey doesn't work on your system, this mode is
the reliable option — it needs no special permissions beyond screen capture.

Tune it in `agent/config.yaml`:

```yaml
mode: periodic
interval: 20   # capture every 20 seconds
```

### Hotkey mode

```yaml
mode: hotkey
hotkey: ctrl+alt+s
```

Then press **Ctrl+Alt+S** whenever a coding question is on screen.

> `keyboard` must be installed (it is, via requirements) and on Linux/macOS may
> require running as root / granting Accessibility permission. If the hotkey
> never triggers, just stay on `mode: periodic`.

You'll see:

```
[17:30:00] Periodic mode: capturing every 30s. Ctrl+C to quit.
```

Within a few seconds of a capture, the solution appears on the dashboard (open
the project preview URL on your phone).

### "capture failed: X connection failed: error 5"

Your desktop uses **Wayland** (or has no X server). The agent automatically
falls back to native screenshot tools — install the one for your desktop:

| Session / desktop | Install |
|---|---|
| Wayland — GNOME | `sudo apt install gnome-screenshot` |
| Wayland — Sway / Hyprland / wlroots | `sudo apt install grim` |
| KDE (Wayland or X11) | `sudo apt install spectacle` |
| X11 (any desktop) | `sudo apt install scrot` |

Check your session type with `echo $XDG_SESSION_TYPE`. After installing, restart
the agent — nothing else to change.
