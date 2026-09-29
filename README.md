# 🎬 FKVideoPlayer

<p align="center">
  <img src="icon.png" width="128" height="128" alt="FKVideoPlayer Logo" />
</p>

<p align="center">
  <b>Next-Gen High Performance Desktop Video Player, Screen Editor, Live Annotation Tool & Actions Recorder.</b><br>
  <i>Создано furrykit / Created by furrykit</i>
</p>

<p align="center">
  <a href="https://github.com/furrykit/FKVideoPlayer/releases"><img src="https://img.shields.io/github/v/release/furrykit/FKVideoPlayer?color=007AFF&style=for-the-badge" alt="Latest Release"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011%20x64-blue?style=for-the-badge" alt="Platform Windows">
  <img src="https://img.shields.io/badge/Python-3.11%20--%203.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python Version">
  <img src="https://img.shields.io/badge/GUI-PyQt5%20%2F%20Fluent%20Dark-181822?style=for-the-badge" alt="GUI PyQt5">
  <a href="https://t.me/furrykit"><img src="https://img.shields.io/badge/Telegram-%40furrykit-229ED9?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram"></a>
</p>

---

## 🌟 Key Features / Ключевые возможности

### 🎞️ Advanced Playback & Scrubbing
- **Hardware-Accelerated Decoding:** Powered by `PyAV` (FFmpeg bindings) and `OpenCV` with asynchronous frame pre-buffering and intelligent LRU frame caching.
- **Micro-Stepping & Frame Precision:** Exact frame-by-frame forward/backward stepping (`Left` / `Right`, `,` / `.`), multi-speed shuttle playback (from 0.05x ultra slow-motion up to 10.0x hyper-speed).
- **Infinite Canvas & Zoom:** Smooth zoom (10% to 500%), pan, 1:1 pixel-perfect inspection, and fit-to-window modes.

### 🎚️ Multi-Track Overlay Timeline
- **Stacked Track Management:** Every added video, GIF, or picture overlay gets its own independent track strip placed right above the master timeline.
- **Dedicated Track Controls:** Play/pause, track-specific scrubber, frame index counter, aspect-ratio lock, visibility toggle (`👁`), settings dialog, and quick delete (`✖`).
- **Aspect Ratio Preservation:** Resizing corner handles (`tl`, `tr`, `bl`, `br`) keep native proportions locked by default or smoothly unlocked.

### 🔲 Multi-Selection & Canvas Interaction
- **Rubber-Band Selection:** Click and drag a marquee selection box across canvas space to grab multiple overlay objects simultaneously.
- **Batch Transformations:** Move, reposition, layer-reorder, and delete multi-selected items in one action.
- **Right-Click (RMB) Context Menu:** Fast menu at cursor to add Video, GIF, Image, or Text overlays, adjust layer depth (Bring to Front, Send to Back), duplicate, or modify properties.
- **True Drag & Drop:** Drop files directly onto an open canvas to insert them as overlays under your cursor without opening redundant tabs.

### 🖥️ Live Window Capture & Audio Recording
- **Real-Time Window Streaming:** Hook into any running desktop window and stream live frames directly onto the canvas.
- **WASAPI System Audio Loopback:** Direct loopback recording of sound coming from the captured window or desktop speakers using `soundcard` and `soundfile`.
- **Microphone Commentary:** Optional voice recording with real-time VU meter monitor.
- **Automatic Audio Muxing:** Both streams are mixed via bundled FFmpeg (`amix`) and muxed into the final video output.

### ✏️ Annotation & Drawing Studio
- **Vector Drawing Tools:** Brush, Highlighter, Arrows, Rectangles, Circles, and Text overlays.
- **Action Timeline Recording:** Record cursor motion, annotations, clicks, and drawing events with accurate sub-millisecond timestamps.

### 💾 Advanced Export & Custom Presets
- Custom resolution scaling (1080p, 720p, 4K, or custom width x height).
- Flexible codecs: `H.264 (libx264)`, `HEVC / H.265`, `VP9`, `MPEG-4`.
- **Rate Control (CBR / VBR):** Select Constant Bitrate (CBR) for strict streaming compliance or Variable Bitrate (VBR) for optimized file size and quality.
- **Custom Bitrate Controls:** Enter arbitrary custom bitrate (0.1 to 300 Mbps) with real-time preset synchronization and custom preset persistence.
- **Custom FPS & Resolution:** Full custom width, height, and frame rate (15 to 120 FPS).

### 🔄 Multi-Project Tabs & Native Auto-Updater
- **Tabbed Workspace:** Open and edit multiple independent video projects simultaneously with isolated timelines, undo/redo stacks, and canvases.
- **Unsaved Changes Guard:** Prompts to save work before closing project tabs or quitting.
- **Built-in GitHub Auto-Updater:** Checks releases from `furrykit/FKVideoPlayer`, notifies on new versions, and provides instant download links.
- **Bilingual Interface:** Real-time dynamic language switching (English / Русский) across the entire UI.
- **Bundled FFmpeg:** Automatic discovery and bundling of FFmpeg — no external installations or manual setup required!

---

## 📥 Download / Скачать

Download the latest standalone executable from **[GitHub Releases](https://github.com/furrykit/FKVideoPlayer/releases)**:

- 🚀 **`FKVideoPlayer.exe`**: Complete all-in-one standalone executable (includes bundled FFmpeg, Python runtime, and all codecs). No installation, Python, or extra files required — just download and run!

---

## 🚀 Running from Source / Запуск из исходников

### Prerequisites
- Windows 10 or 11 (64-bit)
- Python 3.10, 3.11, 3.12, or 3.13

### Installation
```bash
# Clone the repository
git clone https://github.com/furrykit/FKVideoPlayer.git
cd FKVideoPlayer

# Install dependencies
pip install PyQt5 opencv-python av numpy soundcard soundfile Pillow
```

### Run
```bash
python player.py
```

### Build Standalone Executable
```bash
pyinstaller FKVideoPlayer.spec --noconfirm
```
The compiled binary will be placed in `dist/FKVideoPlayer.exe`.

---

## ⌨️ Shortcuts / Горячие клавиши

| Shortcut | Action |
|:---------|:-------|
| `Space` | Play / Pause |
| `Left` / `Right` | Step -1 / +1 Frame |
| `Shift + Left` / `Right` | Seek -5s / +5s |
| `Ctrl + Left` / `Right` | Seek -1s / +1s |
| `Home` / `End` | Jump to Start / End |
| `Ctrl + N` | New Blank Canvas |
| `Ctrl + O` | Open Video |
| `Ctrl + E` | Export Rendered Video |
| `Ctrl + Z` | Undo Drawing |
| `Delete` / `Backspace` | Delete Selected Overlays |
| `F` | Fit to Window |
| `1` | 1:1 Pixel Scale |
| `Ctrl + T` | New Project Tab |
| `Ctrl + W` | Close Active Tab |
| `Ctrl + Tab` | Next Project Tab |

---

## 💖 Support & Donations / Поддержать автора

FKVideoPlayer is completely free and open source. If you find the project useful and want to support its ongoing development, donations are gratefully welcomed:

- 💸 **DonatePay:** [https://new.donatepay.ru/donate/ttvfurrykit](https://new.donatepay.ru/donate/ttvfurrykit)
- ☕ **DonationAlerts:** [https://www.donationalerts.com/r/ttvfurrykit](https://www.donationalerts.com/r/ttvfurrykit)

---

## 📬 Contact / Контакты

- **Author:** furrykit
- **Telegram:** [@furrykit](https://t.me/furrykit)
- **GitHub:** [https://github.com/furrykit](https://github.com/furrykit)
