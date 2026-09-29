# FKVideoPlayer

<p align="center">
  <img src="assets/branding/icon.png" width="100" height="100" alt="FKVideoPlayer Logo" />
</p>

<p align="center">
  <b>High-performance desktop video player, screen editor, live annotation tool, and action recorder.</b><br>
  Built with Python, PyQt5, OpenCV, and PyAV for Windows 10/11.
</p>

<p align="center">
  <a href="https://github.com/furrykit/FKVideoPlayer/releases"><img src="https://img.shields.io/github/v/release/furrykit/FKVideoPlayer?color=007AFF&style=flat-square" alt="Latest Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011%20x64-blue?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <a href="https://t.me/furrykit"><img src="https://img.shields.io/badge/Telegram-%40furrykit-229ED9?style=flat-square&logo=telegram&logoColor=white" alt="Telegram"></a>
</p>

<p align="center">
  <a href="README_RU.md">🇷🇺 Читать документацию на русском языке</a>
</p>

---

## Why FKVideoPlayer?

Most video players fall into two extremes:
- **Media players (VLC, MPC-HC, mpv)** are great for watching movies, but they don't let you draw arrows on a frame, zoom into individual pixels, layer reference clips, or record annotations.
- **Video editors (Premiere, DaVinci, After Effects)** take minutes to launch, weigh gigabytes, and require creating a full project just to scrub a clip frame-by-frame, mark a bug, and export a 5-second video.
- **Screen recorders (OBS Studio)** are heavy to configure when you simply want to stream one specific window, record system loopback audio, draw notes over it, and export an MP4.

**FKVideoPlayer** fills that gap. It is a lightweight, single-binary desktop utility for game developers, QA testers, video analysts, and creators who need fast, precise video inspection and on-screen drawing without the bloat.

---

## Highlights

### 1. Frame-Accurate Playback & Subpixel Zoom
- **Hardware-Assisted Decoding:** Fast PyAV (FFmpeg) and OpenCV decoders with asynchronous frame caching and LRU memory management.
- **Micro-Stepping:** Move frame-by-frame forward or backward (`Left` / `Right`, `,` / `.`).
- **Variable Shuttle Speed:** Smooth playback from **0.05x** ultra slow-motion up to **10.0x** high-speed.
- **Deep Zoom & Pan:** Inspect small details with zoom levels from 10% to 50x, with 1:1 pixel mode (`1`) and fit-to-view (`F`).

### 2. Multi-Track Overlay Timeline
- **Stacked Clip Tracks:** Drop additional videos, GIFs, images, or text overlays onto your timeline. Each overlay gets an independent track with its own scrubber, visibility toggle, aspect-ratio lock, and deletion handle.
- **Picture-in-Picture (PIP):** Drag, resize, and position secondary video feeds on top of the main canvas.
- **Rubber-Band Multi-Selection:** Click and drag a marquee selection to transform or delete multiple overlays at once.

### 3. Photoshop-Style Partial Eraser & Vector Annotations
- **True Carving Eraser:** The eraser cuts holes and removes parts of drawn lines with adjustable radius (`[` and `]`), matching Photoshop and MS Paint behavior instead of deleting the entire stroke.
- **Drawing Tools:** Freehand brush, highlighter, straight lines, rectangles, circles, and rich text overlays.
- **Action Recorder:** Records every cursor gesture, brush stroke, and overlay transformation with millisecond timestamps for instant export or JSON saving.

### 4. Live Window Capture & WASAPI Audio Loopback
- **Direct Window Streaming:** Hook directly into any active Windows application (games, browsers, emulators) and stream its frames live onto the canvas using optimized Win32 GDI capture.
- **WASAPI System Loopback Audio:** Records crystal-clear desktop or game sound directly from your audio endpoint without virtual cable drivers.
- **Voiceover Commentary:** Record your microphone alongside window audio with live volume metering.

### 5. Multi-Project Tabs
- Work on multiple videos, blank drawing canvases, or live window streams in parallel with isolated timelines, undo/redo stacks, and export settings.

---

## Download

Get the latest pre-compiled standalone release for Windows:

### 📦 [Download FKVideoPlayer.exe (GitHub Releases)](https://github.com/furrykit/FKVideoPlayer/releases/latest)

> **No installation required.** Download the single `.exe` file, run it, and start working. Bundled with internal FFmpeg and media decoders.

---

## Running from Source

### Requirements
- Windows 10 or Windows 11 (64-bit)
- Python 3.10 – 3.13

### Quick Start
```bash
# 1. Clone the repository
git clone https://github.com/furrykit/FKVideoPlayer.git
cd FKVideoPlayer

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch application
python -m fkplayer
# or
python player.py
```

### Running Tests
All 47 unit and integration tests run offline without external dependencies:
```bash
python -m unittest test_player.py
```

### Building Portable Executable
```bash
pyinstaller FKVideoPlayer.spec --clean -y
```
The compiled single-file binary will appear in `dist/FKVideoPlayer.exe`.

---

## Keyboard Shortcuts

| Shortcut | Action |
|:---|:---|
| `Space` | Play / Pause |
| `Left` / `Right` | Step -1 / +1 Frame |
| `Ctrl + Left` / `Right` | Seek -1.0s / +1.0s |
| `Shift + Left` / `Right` | Seek -5.0s / +5.0s |
| `Home` / `End` | Jump to First / Last Frame |
| `F` | Fit Canvas to Window |
| `1` | 1:1 Pixel Scale Mode |
| `Ctrl + Z` | Undo Last Annotation |
| `C` | Clear All Annotations |
| `[` / `]` | Decrease / Increase Brush or Eraser Size |
| `Delete` / `Backspace` | Delete Selected Overlays |
| `Ctrl + T` | New Project Tab |
| `Ctrl + W` | Close Current Tab |
| `Ctrl + N` | New Blank Canvas |
| `Ctrl + O` | Open Video File |
| `Ctrl + E` | Export Video / Recording |

---

## Project Structure

```text
FKVideoPlayer/
├── assets/
│   └── branding/              # Application icons and branding assets
├── fkplayer/                  # Core package
│   ├── app.py                 # Application bootstrap and dark titlebar
│   ├── core/                  # Engine logic (i18n, logger, projects, recorder)
│   ├── media/                 # Video decoding, Win32 capture, WASAPI audio
│   └── ui/                    # Qt widgets, canvas, timeline, dialogs
├── tests/
│   └── test_player.py         # 47 unit and integration tests
├── FKVideoPlayer.spec         # PyInstaller standalone build specification
├── pyproject.toml             # PEP 517/621 build configuration
├── requirements.txt           # Production dependencies
├── LICENSE                    # MIT License
├── README.md                  # Documentation (English)
└── README_RU.md               # Documentation (Russian)
```

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

## Author & Support

- **Created by:** [furrykit](https://github.com/furrykit)
- **Telegram:** [@furrykit](https://t.me/furrykit)
- **Donations:**
  - [DonatePay](https://new.donatepay.ru/donate/ttvfurrykit)
  - [DonationAlerts](https://www.donationalerts.com/r/ttvfurrykit)
