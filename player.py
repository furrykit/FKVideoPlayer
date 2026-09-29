#!/usr/bin/env python3
"""
FKVideoPlayer launcher.
Runs FKVideoPlayer from the fkplayer modular package.
"""
import sys
import os

# Ensure repository root is on sys.path
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from fkplayer.player import (  # noqa: F401
    FKVideoPlayer, VideoPlayerWindow, VideoCanvas,
    Stroke, OverlayObject, OverlayTrackRow, OverlayTrackContainer,
    ActionRecorder, ClickableSlider, dist_to_segment_sq,
    ExportVideoWorker, ProjectSession, set_dark_titlebar,
    resource_path, get_ffmpeg_path, APP_VERSION, main
)

if __name__ == "__main__":
    main()
