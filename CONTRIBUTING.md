# Contributing to FKVideoPlayer

Thank you for your interest in contributing to **FKVideoPlayer**! We welcome bug reports, feature suggestions, and pull requests.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/furrykit/FKVideoPlayer.git
   cd FKVideoPlayer
   ```

2. **Set up a virtual environment:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements-dev.txt
   ```

4. **Run the player from source:**
   ```bash
   python -m fkplayer
   # or
   python player.py
   ```

## Code Guidelines

- **Architecture:** Keep UI separated from business logic. Pure functions for transforms and rendering algorithms.
- **Localization Rule:** All user-facing strings must reside in `fkplayer/core/i18n.py`. Core source files (outside of translation dictionary files) must contain **0 Cyrillic characters**.
- **Exception Safety:** Always guard file operations, device accesses, and subprocess calls with explicit `try...finally` or error handling blocks.
- **Testing:** Add unit tests to `test_player.py` for any new features or bug fixes.
  ```bash
  python -m unittest test_player.py
  ```

## Building the Executable

To compile a standalone portable binary:
```bash
pyinstaller FKVideoPlayer.spec --clean -y
```
The output executable will be created at `dist/FKVideoPlayer.exe`.
