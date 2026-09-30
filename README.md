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

Most video tools are built for either passive viewing or heavy production:
- **Media players (VLC, MPC-HC, mpv)** are tuned for watching movies. They don't let you draw arrows directly on a frame, zoom into raw pixels, layer reference clips, or record synchronized annotations.
- **NLE editors (Premiere, DaVinci, After Effects)** take minutes to launch, consume gigabytes of RAM, and require creating a full project just to scrub a clip frame-by-frame, highlight a bug, and export a 5-second video.
- **Screen recorders (OBS Studio)** are complex to configure when you just need to capture a specific window or monitor, record WASAPI audio, draw notes over it in real time, and export an MP4.

**FKVideoPlayer** bridges this gap: a fast, single-binary desktop utility for game developers, QA engineers, video analysts, and content creators who need instant frame scrubbing, on-screen drawing, and live capture without overhead.

---

## Key Features

### 1. Hardware-Accelerated Playback & Precision Scrubbing
- **Hardware Decoding (NVDEC / CUDA):** Automatic GPU acceleration (`hevc_cuvid`, `h264_cuvid`, `vp9_cuvid`, `av1_cuvid`) with dynamic downscaling for smooth 60 FPS playback on 4K and 8K footage.
- **Software Fallback:** Multi-threaded PyAV / FFmpeg software engine for hardware-agnostic compatibility.
- **Frame-by-Frame Stepping:** Micro-step forward and backward (`Left` / `Right`, `,` / `.`).
- **Variable Shuttle Speed:** Smooth playback from **0.05x** slow-motion up to **10.0x** high-speed with audio sync.
- **Subpixel Zoom & Pan:** Inspect fine details from 10% to 50x zoom, 1:1 pixel mode (`1`), and fit-to-view (`F`).

### 2. Interactive 360° Transformations & Multi-Track Overlays
- **360-Degree Rotation:** Free-angle rotation handle for brush drawings, text overlays, images, and video clips. Hold `Shift` while dragging to snap in 15° steps.
- **Stacked Clip Tracks:** Drag and drop videos, GIFs, images, and text onto the canvas. Each overlay gets an independent track with its own scrubber, visibility toggle, aspect-ratio lock, and deletion handle.
- **Picture-in-Picture (PIP):** Freely move, scale, crop, and layer secondary video streams over the main canvas.
- **Rubber-Band Multi-Selection:** Select multiple overlays and drawings with a marquee box to move, rotate, or delete them in batch.
- **Unified Undo/Redo:** `Ctrl + Z` tracks transforms, rotations, deletions, and drawings in one cohesive stack.

### 3. Photoshop-Style Vector Eraser & Drawing Tools
- **Physical Carving Eraser:** The eraser cuts through vector strokes and removes only intersecting segments (with adjustable radius via `[` and `]`), matching Photoshop and Paint instead of wiping the entire line.
- **Single-Step Undo:** Erasing continuously over multiple strokes rolls back cleanly in a single `Ctrl + Z`.
- **Drawing Tools:** Freehand brush, highlighter, straight lines, rectangles, ellipses, and rich text overlays.
- **Action Recorder:** Records every cursor gesture, brush stroke, and overlay transformation with millisecond timestamps for instant export or JSON saving.

### 4. Full Monitor & Window Capture with WASAPI Audio
- **Dual Capture Modes:** Capture any active application window or grab the entire monitor / virtual desktop spanning multiple displays.
- **Low-Latency Streaming:** Optimized Win32 GDI BitBlt capture engine (~2 ms grab latency, up to 60 FPS) with zero handle leaks.
- **WASAPI Loopback Audio:** Records desktop and application audio directly from the Windows audio pipeline without virtual audio cables.
- **Mic Voiceover:** Record live microphone commentary alongside desktop audio with real-time volume metering.

### 5. Multi-Project Tabs & Zero-Install Binary
- **Isolated Workspaces:** Work on multiple videos, blank drawing canvases, or live screen streams simultaneously in tabs with separate timelines and undo stacks.
- **Portable Executable:** Single standalone `.exe` bundle with zero external dependencies.

---

## Download

Get the latest pre-compiled standalone release for Windows:

### 📦 [Download FKVideoPlayer.exe (GitHub Releases)](https://github.com/furrykit/FKVideoPlayer/releases/latest)

> **No installation required.** Download the single `.exe` file and run it. Bundled with internal FFmpeg, hardware decoders, and all dependencies.

---

## Running from Source

### Requirements
- Windows 10 or Windows 11 (64-bit)
- Python 3.10 – 3.13

### Quick Start
```bash
# 1. Clone repository
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
All 57 unit and integration tests run offline without external dependencies:
```bash
python -m unittest discover tests
# or
python -m unittest tests/test_player.py
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
| `,` / `.` | Step -1 / +1 Frame |
| `Ctrl + Left` / `Right` | Seek -1.0s / +1.0s |
| `Shift + Left` / `Right` | Seek -5.0s / +5.0s |
| `Home` / `End` | Jump to First / Last Frame |
| `F` | Fit Canvas to Window |
| `1` | 1:1 Pixel Scale Mode |
| `Ctrl + Z` | Undo Last Action (Drawing, Eraser, Move, Rotate, Delete) |
| `C` | Clear All Annotations |
| `[` / `]` | Decrease / Increase Brush or Eraser Size |
| `Shift + Drag Rotate` | Snap Rotation to 15° Increments |
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
│   ├── media/                 # Video decoding (NVDEC/PyAV), Win32 capture, WASAPI audio
│   └── ui/                    # Qt widgets, canvas, timeline, dialogs
├── tests/
│   └── test_player.py         # 57 unit and integration tests
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
