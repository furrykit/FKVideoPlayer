#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Playback engine mixin for FKVideoPlayer.
Handles video decoding, frame caching, seeking, audio-video synchronization,
audio playback rate, volume, and playback speed control.
"""

import os
import time
import tempfile
import wave
import av
import cv2
import numpy as np

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QFileDialog, QMessageBox
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

from fkplayer.core.projects import ProjectManager
from fkplayer.core.i18n import tr
from fkplayer.core.logger import get_logger
from fkplayer.media.reader import FastVideoReader

logger = get_logger("Playback")


class PlaybackMixin:
    """Mixin providing complete video and audio playback capabilities."""

    def open_file_dialog(self, *args):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.flv *.m4v);;All Files (*.*)"
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path, in_new_tab=None):
        try:
            if in_new_tab or (in_new_tab is None and self.active_project and not self.active_project.is_empty()):
                self.new_project_tab(name=os.path.basename(file_path), project_type="video")

            if self.cap is not None:
                self.cap.release()
                self.pause()

            self.cap = FastVideoReader(file_path)
            if not self.cap.isOpened():
                logger.error(f"Failed to open video file: {file_path}")
                QMessageBox.critical(self, "Error", f"Failed to open video file:\n{file_path}")
                return

            self.video_path = file_path
            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
            if self.fps <= 1.0 or np.isnan(self.fps):
                self.fps = 25.0
            vw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vh = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            budget_bytes = getattr(self, 'MAX_CACHE_MEMORY_BYTES', 120 * 1024 * 1024)
            if vw > 0 and vh > 0:
                frame_bytes = vw * vh * 4
                self.MAX_CACHE_FRAMES = max(1, min(60, int(budget_bytes // frame_bytes)))
            else:
                self.MAX_CACHE_FRAMES = 30
            logger.info(f"Loaded video: {file_path} ({vw}x{vh}, frames={self.total_frames}, fps={self.fps:.2f}, cache_limit={self.MAX_CACHE_FRAMES} frames)")

            self.frame_cache.clear()
            import gc
            gc.collect()
            self._cap_pos = 0

            self._cleanup_temp_audio()
            self.has_audio = False
            try:
                container = av.open(file_path)
                try:
                    if len(container.streams.audio) > 0:
                        temp_wav = os.path.join(tempfile.gettempdir(), f"fk_snd_{os.getpid()}_{int(cv2.getTickCount())}.wav")
                        resampler = av.AudioResampler(format='s16', layout='stereo', rate=44100)
                        with wave.open(temp_wav, 'wb') as wav_file:
                            wav_file.setnchannels(2)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(44100)
                            for frame in container.decode(audio=0):
                                for r in resampler.resample(frame):
                                    wav_file.writeframes(r.to_ndarray().tobytes())
                            for r in resampler.resample(None):
                                wav_file.writeframes(r.to_ndarray().tobytes())
                        self.temp_audio_path = temp_wav
                        self.has_audio = True
                finally:
                    container.close()
            except Exception:
                self.has_audio = False

            if getattr(self, 'has_audio', False) and getattr(self, 'temp_audio_path', None) and os.path.exists(self.temp_audio_path):
                media_url = QUrl.fromLocalFile(self.temp_audio_path)
                self.audio_player.setMedia(QMediaContent(media_url))
                self.audio_player.setVolume(self.current_volume if not self.is_muted else 0)
                self.audio_player.setPosition(0)
            else:
                self.audio_player.setMedia(QMediaContent())

            self.timeline_slider.fps = self.fps
            self.timeline_slider.setRange(0, max(0, self.total_frames - 1))
            self.timeline_slider.setValue(0)
            self.current_frame_idx = 0

            self.canvas.strokes.clear()
            self.canvas.undo_stack.clear()

            tab_name = os.path.basename(file_path)
            if self.active_project:
                self.active_project.name = tab_name
                self.active_project.project_type = 'video'
            if hasattr(self, 'project_tabs'):
                cur_idx = self.project_tabs.currentIndex()
                if cur_idx >= 0:
                    self.project_tabs.setTabText(cur_idx, tab_name)

            self._seek_to_frame(0)
            self.setWindowTitle(f"FKVideoPlayer — {tab_name}")
            self._stop_window_capture()
            if self.cap:
                ProjectManager.instance().add_project(
                    'video', tab_name, file_path,
                    int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                )
                self._refresh_recent_projects_menu()
        except Exception as e:
            logger.error(f"Error loading video '{file_path}': {e}")
            QMessageBox.critical(self, "Error", f"Failed to load video file:\n{e}")

    def _cleanup_temp_audio(self):
        if getattr(self, 'temp_audio_path', None) and os.path.exists(self.temp_audio_path):
            try:
                self.audio_player.setMedia(QMediaContent())
                os.remove(self.temp_audio_path)
            except Exception:
                pass
            self.temp_audio_path = None

    def _get_frame_cached(self, frame_idx, fast=False):
        if self.cap is None or not self.cap.isOpened():
            return None

        if frame_idx in self.frame_cache:
            self.frame_cache.move_to_end(frame_idx)
            return self.frame_cache[frame_idx]

        cur_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        diff = frame_idx - cur_pos

        if diff == 0:
            pass
        elif 0 < diff <= 12 and not fast:
            # Fast-forward sequentially without costly keyframe seek
            for _ in range(diff):
                self.cap.grab()
        else:
            if hasattr(self.cap, 'seek'):
                self.cap.seek(frame_idx, exact=not fast)
            else:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        if hasattr(self.cap, 'read_qimage'):
            ret, qimg = self.cap.read_qimage()
            if not ret or qimg is None:
                return None
            w, h = qimg.width(), qimg.height()
        else:
            ret, frame_bgr = self.cap.read()
            if not ret or frame_bgr is None:
                return None
            h, w = frame_bgr.shape[:2]
            bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
            qimg = QImage(bgra.data, w, h, w * 4, QImage.Format_RGB32).copy()
            del frame_bgr, bgra

        self._cap_pos = frame_idx + 1

        if self.is_playing or fast:
            return qimg

        cached_qimg = qimg.copy()
        frame_bytes = max(1, w * h * 4)
        budget_bytes = getattr(self, 'MAX_CACHE_MEMORY_BYTES', 120 * 1024 * 1024)
        dynamic_limit = max(1, min(60, int(budget_bytes // frame_bytes)))
        max_allowed = min(getattr(self, 'MAX_CACHE_FRAMES', 60), dynamic_limit)

        while len(self.frame_cache) >= max_allowed:
            self.frame_cache.popitem(last=False)
        self.frame_cache[frame_idx] = cached_qimg

        return cached_qimg

    def _seek_to_frame(self, frame_idx, fast=False):
        if self.cap is None or not self.cap.isOpened() or self.total_frames <= 0:
            return

        frame_idx = max(0, min(self.total_frames - 1, frame_idx))
        qimg = self._get_frame_cached(frame_idx, fast=fast)
        if qimg is not None:
            self.current_frame_idx = frame_idx
            self.canvas.set_frame(qimg)
            self._update_time_label()
            if hasattr(self, 'recorder'):
                self.recorder.record_frame()

            if not self.timeline_slider.is_dragging:
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(frame_idx)
                self.timeline_slider.blockSignals(False)

    def _sync_audio_position(self):
        if not self.is_playing and self.cap is not None:
            safe_fps = max(1.0, self.fps)
            target_ms = int((self.current_frame_idx / safe_fps) * 1000)
            self.audio_player.setPosition(target_ms)

    def toggle_play_pause(self, *args):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.cap is None or not self.cap.isOpened():
            return
        if self.current_frame_idx >= self.total_frames - 1:
            self._seek_to_frame(0)

        self._update_timer_interval()
        self.play_timer.start()
        self.is_playing = True
        self.btn_play_pause.setText("❚❚ Pause")

        safe_fps = max(1.0, self.fps)
        target_ms = int((self.current_frame_idx / safe_fps) * 1000)
        self._play_start_time = time.monotonic()
        self._play_start_frame = self.current_frame_idx
        self._play_start_audio_pos = target_ms
        self._last_seen_audio_pos = -1
        self._last_audio_advance_time = time.monotonic()

        if getattr(self, 'has_audio', False) and getattr(self, 'temp_audio_path', None) and os.path.exists(self.temp_audio_path):
            self.audio_player.setPosition(target_ms)
            self._update_audio_rate()
            self.audio_player.play()

    def pause(self):
        self.play_timer.stop()
        self.is_playing = False
        self.btn_play_pause.setText("▶ Play")
        if getattr(self, 'has_audio', False):
            self.audio_player.pause()

    def _update_audio_rate(self):
        if not getattr(self, 'has_audio', False):
            return

        if 0.5 <= self.playback_speed <= 2.0:
            self.audio_player.setPlaybackRate(self.playback_speed)
            if not self.is_muted:
                self.audio_player.setMuted(False)
        else:
            self.audio_player.setPlaybackRate(1.0)
            self.audio_player.setMuted(True)

    def _update_timer_interval(self):
        target_fps = max(0.1, self.fps * self.playback_speed)
        if target_fps <= 60.0:
            interval = max(1, int(1000.0 / target_fps))
            self.frames_per_tick = 1
        else:
            interval = 16
            self.frames_per_tick = max(1, int(round(target_fps / 60.0)))

        self.play_timer.setInterval(max(1, interval))
        self._update_audio_rate()

    def _on_play_tick(self):
        if getattr(self, '_tick_in_progress', False):
            return
        if self.cap is None or not self.cap.isOpened() or self.total_frames <= 0:
            self.pause()
            return

        self._tick_in_progress = True
        try:
            has_active_audio = (
                getattr(self, 'has_audio', False)
                and getattr(self, 'temp_audio_path', None)
                and os.path.exists(self.temp_audio_path)
                and 0.5 <= self.playback_speed <= 2.0
                and self.audio_player.state() == QMediaPlayer.PlayingState
            )

            if has_active_audio:
                if self.audio_player.mediaStatus() == QMediaPlayer.EndOfMedia:
                    if self.is_looping:
                        self._seek_to_frame(0)
                        self._play_start_time = time.monotonic()
                        self._play_start_audio_pos = 0
                        self.audio_player.setPosition(0)
                        self.audio_player.play()
                        return
                    else:
                        self._seek_to_frame(self.total_frames - 1)
                        self.pause()
                        return

                audio_pos = self.audio_player.position()
                now = time.monotonic()

                # Guard against pre-roll delay right after starting playback or seek:
                if now - getattr(self, '_play_start_time', 0.0) < 0.35:
                    if audio_pos < getattr(self, '_play_start_audio_pos', 0) - 200:
                        audio_pos = getattr(self, '_play_start_audio_pos', 0)

                # Watchdog: if audio hardware position stalls for > 0.8s, step frame via timer fallback
                if audio_pos != getattr(self, '_last_seen_audio_pos', -1):
                    self._last_seen_audio_pos = audio_pos
                    self._last_audio_advance_time = now
                elif now - getattr(self, '_last_audio_advance_time', now) > 0.8:
                    audio_pos = int(((self.current_frame_idx + 1) / max(1.0, self.fps)) * 1000)

                target_frame = int(round((audio_pos / 1000.0) * self.fps))
                target_frame = max(0, min(self.total_frames - 1, target_frame))

                if target_frame >= self.total_frames - 1:
                    if self.is_looping:
                        self._seek_to_frame(0)
                        self._play_start_time = now
                        self._play_start_audio_pos = 0
                        self.audio_player.setPosition(0)
                        self.audio_player.play()
                        return
                    else:
                        self._seek_to_frame(self.total_frames - 1)
                        self.pause()
                        return

                if target_frame > self.current_frame_idx:
                    diff = target_frame - self.current_frame_idx
                    if diff <= 12:
                        qimg = self._get_frame_cached(target_frame)
                        if qimg is not None:
                            self.current_frame_idx = target_frame
                            self.canvas.set_frame(qimg)
                            self._update_time_label()
                            if hasattr(self, 'recorder'):
                                self.recorder.record_frame()
                            if not self.timeline_slider.is_dragging:
                                self.timeline_slider.blockSignals(True)
                                self.timeline_slider.setValue(target_frame)
                                self.timeline_slider.blockSignals(False)
                        else:
                            self._seek_to_frame(target_frame)
                    else:
                        self._seek_to_frame(target_frame)
                return

            # Monotonic master clock sync for video without audio, muted audio, or fast/slow rates
            now = time.monotonic()
            elapsed = now - getattr(self, '_play_start_time', now)
            clock_target = int(round(getattr(self, '_play_start_frame', 0) + elapsed * self.fps * self.playback_speed))
            step = getattr(self, 'frames_per_tick', 1)
            target_frame = max(self.current_frame_idx + step, clock_target)
            target_frame = max(0, min(self.total_frames - 1, target_frame))

            if target_frame >= self.total_frames - 1:
                if self.is_looping:
                    self._seek_to_frame(0)
                    self._play_start_time = now
                    self._play_start_frame = 0
                    if self.is_playing and getattr(self, 'has_audio', False):
                        self.audio_player.setPosition(0)
                        self.audio_player.play()
                    return
                else:
                    self._seek_to_frame(self.total_frames - 1)
                    self.pause()
                    return

            if target_frame != self.current_frame_idx:
                diff = target_frame - self.current_frame_idx
                if 0 < diff <= 12:
                    qimg = self._get_frame_cached(target_frame)
                    if qimg is not None:
                        self.current_frame_idx = target_frame
                        self.canvas.set_frame(qimg)
                        self._update_time_label()
                        if hasattr(self, 'recorder'):
                            self.recorder.record_frame()

                        if not self.timeline_slider.is_dragging:
                            self.timeline_slider.blockSignals(True)
                            self.timeline_slider.setValue(target_frame)
                            self.timeline_slider.blockSignals(False)
                    else:
                        self._seek_to_frame(target_frame)
                else:
                    self._seek_to_frame(target_frame)
        finally:
            self._tick_in_progress = False

    def step_frame(self, delta):
        if self.is_playing:
            self.pause()

        if self.cap is None or not self.cap.isOpened() or self.total_frames <= 0:
            return

        target = max(0, min(self.total_frames - 1, self.current_frame_idx + delta))
        self.current_frame_idx = target
        self._update_time_label()
        if hasattr(self, 'recorder'):
            self.recorder.record_frame()

        if not self.timeline_slider.is_dragging:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(target)
            self.timeline_slider.blockSignals(False)

        if target in self.frame_cache:
            self.canvas.set_frame(self.frame_cache[target])
            self._audio_sync_timer.start(150)
            return

        if not self._step_timer.isActive():
            self._step_timer.start(10)

    def _on_step_timer(self):
        qimg = self._get_frame_cached(self.current_frame_idx)
        if qimg is not None:
            self.canvas.set_frame(qimg)
        self._audio_sync_timer.start(150)

    def seek_seconds(self, delta_sec):
        delta_frames = int(delta_sec * self.fps)
        if self.is_playing:
            target = max(0, min(self.total_frames - 1, self.current_frame_idx + delta_frames))
            self._seek_to_frame(target)
            safe_fps = max(1.0, self.fps)
            target_ms = int((target / safe_fps) * 1000)
            self._play_start_time = time.monotonic()
            self._play_start_frame = target
            self._play_start_audio_pos = target_ms
            self._last_seen_audio_pos = -1
            self._last_audio_advance_time = time.monotonic()
            if getattr(self, 'has_audio', False):
                self.audio_player.setPosition(target_ms)
        else:
            self.step_frame(delta_frames)

    def _toggle_loop(self, *args):
        self.is_looping = self.btn_loop.isChecked()

    def _on_speed_changed(self, text):
        speed_str = text.replace("x", "")
        try:
            self.playback_speed = float(speed_str)
            self._update_timer_interval()
        except ValueError:
            pass

    def increase_speed(self):
        idx = self.combo_speed.currentIndex()
        if idx < self.combo_speed.count() - 1:
            self.combo_speed.setCurrentIndex(idx + 1)

    def decrease_speed(self):
        idx = self.combo_speed.currentIndex()
        if idx > 0:
            self.combo_speed.setCurrentIndex(idx - 1)

    def reset_speed(self):
        idx = self.combo_speed.findText("1.0x")
        if idx >= 0:
            self.combo_speed.setCurrentIndex(idx)

    def _on_slider_pressed(self):
        self._was_playing_before_scrub = self.is_playing
        self.pause()

    def _on_slider_moved(self, val):
        self._pending_seek_frame = val
        if getattr(self, '_seek_in_progress', False):
            return
        if not self._seek_timer.isActive():
            self._seek_timer.start(16)
        if hasattr(self, '_scrub_settle_timer'):
            self._scrub_settle_timer.start(80)

    def _process_pending_seek(self):
        if self._pending_seek_frame is None or getattr(self, '_seek_in_progress', False):
            return
        self._seek_in_progress = True
        try:
            target = self._pending_seek_frame
            self._pending_seek_frame = None
            is_fast = getattr(self.timeline_slider, 'is_dragging', False)
            self._seek_to_frame(target, fast=is_fast)
        finally:
            self._seek_in_progress = False
            if self._pending_seek_frame is not None:
                self._seek_timer.start(1)

    def _on_scrub_settle(self):
        if getattr(self.timeline_slider, 'is_dragging', False):
            self._seek_to_frame(self.current_frame_idx, fast=False)

    def _on_slider_released(self):
        self._seek_timer.stop()
        if hasattr(self, '_scrub_settle_timer'):
            self._scrub_settle_timer.stop()
        if self._pending_seek_frame is not None:
            val = self._pending_seek_frame
            self._pending_seek_frame = None
        else:
            val = self.timeline_slider.value()

        self._seek_to_frame(val, fast=False)
        if getattr(self, '_was_playing_before_scrub', False):
            self.play()

    def _on_slider_wheel(self, delta):
        target = self.current_frame_idx + delta
        self._seek_to_frame(target)

    def _format_time(self, seconds):
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins:02d}:{secs:05.2f}"

    def _update_time_label(self):
        safe_fps = max(1.0, self.fps)
        if self.fps > 0 and self.total_frames > 0:
            cur_sec = self.current_frame_idx / safe_fps
            tot_sec = self.total_frames / safe_fps
            self.lbl_time_info.setText(
                f"{self._format_time(cur_sec)} / {self._format_time(tot_sec)}  |  Frame: {self.current_frame_idx + 1} / {self.total_frames}  ({self.fps:.1f} FPS)"
            )
        else:
            cur_sec = 0.0
            self.lbl_time_info.setText("00:00.00 / 00:00.00  |  Frame: 0 / 0  (0.0 FPS)")

        if hasattr(self, 'overlay_tracks_container') and self.overlay_tracks_container is not None:
            if self.overlay_tracks_container.isVisible():
                self.overlay_tracks_container.update_positions(cur_sec)

    def _format_rec_time(self, sec: float) -> str:
        mins = int(sec // 60)
        secs = sec % 60
        return f"{mins:02d}:{secs:04.1f}"

