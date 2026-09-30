#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-project tab session state container for FKVideoPlayer.
Isolates playback position, cached frames, overlays, and canvas state per tab.
"""

import os
import collections
from PyQt5.QtCore import QObject

try:
    from fkplayer.ui.canvas import VideoCanvas
    from fkplayer.media.recorder import ActionRecorder
except (ImportError, ValueError):
    from ..ui.canvas import VideoCanvas
    from ..media.recorder import ActionRecorder


class ProjectSession(QObject):
    def __init__(self, player, name="Untitled", project_type="empty"):
        super().__init__(player)
        self.player = player
        self.name = name
        self.project_type = project_type

        self.cap = None
        self.video_path = ""
        self.fps = 24.0
        self.total_frames = 0
        self.current_frame_idx = 0
        self.is_playing = False
        self.is_looping = True
        self.playback_speed = 1.0
        self.frames_per_tick = 1
        self.has_audio = False
        self.temp_audio_path = None
        self.frame_cache = collections.OrderedDict()
        self._cap_pos = -1

        self.canvas = VideoCanvas(player)
        self.recorder = ActionRecorder(player)
        self.canvas.recorder = self.recorder

        self.window_capture_worker = None
        self.is_capturing_window = False
        self.captured_window_hwnd = None
        self.captured_window_title = ""
        self.temp_capture_writer = None
        self.temp_capture_writer_size = None
        self.temp_capture_video_path = None
        self._capture_frame_count = 0

    def is_empty(self) -> bool:
        if self.cap is not None or self.video_path:
            return False
        if self.is_capturing_window or self.window_capture_worker is not None:
            return False
        if len(self.canvas.strokes) > 0 or len(self.canvas.overlays) > 0:
            return False
        if self.recorder.is_active() or len(self.recorder.events) > 0:
            return False
        if self.canvas.video_width > 0 or self.canvas.video_height > 0:
            return False
        return True

    def has_unsaved_changes(self) -> bool:
        if len(self.canvas.strokes) > 0 or len(self.canvas.overlays) > 0:
            return True
        if self.recorder.is_active() or len(self.recorder.events) > 0:
            return True
        return False

    def close(self):
        self.is_playing = False
        if self.window_capture_worker:
            try:
                self.window_capture_worker.stop()
            except Exception:
                pass
            self.window_capture_worker = None
        self.is_capturing_window = False

        if self.temp_capture_writer is not None:
            try:
                self.temp_capture_writer.release()
            except Exception:
                pass
            self.temp_capture_writer = None

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

        if self.recorder and self.recorder.is_active():
            try:
                self.recorder.stop()
            except Exception:
                pass
        if self.recorder:
            self.recorder.events.clear()

        if self.temp_audio_path and os.path.exists(self.temp_audio_path):
            try:
                os.remove(self.temp_audio_path)
            except Exception:
                pass
            self.temp_audio_path = None

        self.frame_cache.clear()
        for ov in self.canvas.overlays:
            try:
                ov.close()
            except Exception:
                pass
        self.canvas.overlays.clear()
        self.canvas.strokes.clear()
        self.canvas.undo_stack.clear()
        self.canvas.current_qimage = None
        import gc
        gc.collect()
