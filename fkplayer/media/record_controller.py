#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Action recording, window capture, and session export mixin for FKVideoPlayer.
Coordinates ActionRecorder, microphone audio capture, window capture worker,
JSON session persistence, and ExportVideoWorker dialogs.
"""

import os
import subprocess
import json
import time
import tempfile
import cv2
import numpy as np

from PyQt5.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
from PyQt5.QtCore import Qt

from fkplayer.media.export import ExportVideoWorker
from fkplayer.media.capture import WindowCaptureWorker
from fkplayer.ui.dialogs import ExportDialog, WindowCaptureDialog
from fkplayer.core.geometry import get_ffmpeg_path
from fkplayer.core.projects import ProjectManager
from fkplayer.core.i18n import tr
from fkplayer.core.logger import get_logger

logger = get_logger("RecordController")


class RecordingMixin:
    """Mixin providing action recording, microphone capture, window capture, and video export."""

    def _format_rec_time(self, sec: float) -> str:
        mins = int(sec // 60)
        secs = sec % 60
        return f"{mins:02d}:{secs:04.1f}"

    def _toggle_microphone(self, *args):
        self.is_mic_enabled = not self.is_mic_enabled
        if self.is_mic_enabled:
            self.btn_mic_toggle.setText("🎤 Mic: ON")
            self.btn_mic_toggle.setStyleSheet("background-color: #1A3824; color: #34C759; border: 1px solid #34C759;")
        else:
            self.btn_mic_toggle.setText("🎤 Mic: OFF")
            self.btn_mic_toggle.setStyleSheet("background-color: #381A1A; color: #FF3B30; border: 1px solid #FF3B30;")

    def start_actions_record(self, *args):
        if self.recorder.is_active():
            return
        if self.recorder.events:
            reply = QMessageBox.question(
                self,
                "New Recording",
                "Previous action recording will be cleared. Start a new recording?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Start microphone recording if enabled
        temp_dir = tempfile.gettempdir()
        if self.is_mic_enabled:
            self.temp_mic_wav_path = os.path.join(temp_dir, f"temp_mic_{int(time.time())}_{os.getpid()}.wav")
            self.mic_recorder.start_recording(self.temp_mic_wav_path, self.selected_mic_device)

        # Window capture frame and system audio recording
        if self.is_capturing_window:
            self.temp_capture_video_path = os.path.join(temp_dir, f"temp_capture_{int(time.time())}_{os.getpid()}.mp4")
            self.temp_capture_writer = None
            self.temp_capture_writer_size = None
            self._capture_frame_count = 0
            self.current_frame_idx = 0
            self.total_frames = 0
            self.video_path = self.temp_capture_video_path
            self.temp_sys_wav_path = os.path.join(temp_dir, f"temp_sys_{int(time.time())}_{os.getpid()}.wav")
            if hasattr(self, 'sys_audio_recorder'):
                self.sys_audio_recorder.start_recording(self.temp_sys_wav_path)

        self.recorder.start()
        self.btn_rec_start.setEnabled(False)
        self.btn_rec_pause.setEnabled(True)
        self.btn_rec_pause.setText("❚❚ Pause")
        self.btn_rec_stop.setEnabled(True)
        self.btn_rec_export.setEnabled(False)
        self.btn_rec_save.setEnabled(False)
        self.lbl_rec_status.setStyleSheet("color: #FF3B30; font-family: Consolas, monospace; font-weight: bold;")
        self.lbl_rec_status.setText("● REC 00:00.0 (1 actions)")
        self.rec_update_timer.start(100)

    def pause_actions_record(self, *args):
        if not self.recorder.is_active():
            return
        self.recorder.pause()
        if self.recorder.is_paused():
            self.btn_rec_pause.setText("▶ Resume")
            self.lbl_rec_status.setStyleSheet("color: #FFCC00; font-family: Consolas, monospace; font-weight: bold;")
            sec = self.recorder.current_time()
            cnt = len(self.recorder.events)
            self.lbl_rec_status.setText(f"❚❚ PAUSED {self._format_rec_time(sec)} ({cnt} actions)")
        else:
            self.btn_rec_pause.setText("❚❚ Pause")
            self.lbl_rec_status.setStyleSheet("color: #FF3B30; font-family: Consolas, monospace; font-weight: bold;")

    def stop_actions_record(self, *args):
        if not self.recorder.is_active():
            return
        self.recorder.stop()
        self.rec_update_timer.stop()

        # Stop microphone
        if self.is_mic_enabled and self.mic_recorder.is_recording:
            self.last_mic_wav = self.mic_recorder.stop_recording()

        # Stop system audio loopback
        if hasattr(self, 'sys_audio_recorder') and self.sys_audio_recorder.is_recording:
            self.last_sys_wav = self.sys_audio_recorder.stop_recording()

        # Stop capture writer
        if self.temp_capture_writer is not None:
            try:
                self.temp_capture_writer.release()
            except Exception:
                pass
            self.temp_capture_writer = None
            if self.is_capturing_window:
                self.total_frames = self._capture_frame_count
            else:
                self.total_frames = max(self.total_frames, self._capture_frame_count)
            if self.temp_capture_video_path and os.path.exists(self.temp_capture_video_path):
                self.video_path = self.temp_capture_video_path

        self.btn_rec_start.setEnabled(True)
        self.btn_rec_pause.setEnabled(False)
        self.btn_rec_pause.setText("❚❚ Pause")
        self.btn_rec_stop.setEnabled(False)

        has_events = len(self.recorder.events) > 0 and self.recorder.elapsed_time > 0.05
        self.btn_rec_export.setEnabled(has_events)
        self.btn_rec_save.setEnabled(has_events)

        sec = self.recorder.elapsed_time
        cnt = len(self.recorder.events)
        self.lbl_rec_status.setStyleSheet("color: #34C759; font-family: Consolas, monospace;")
        self.lbl_rec_status.setText(f"⏹ Finished: {self._format_rec_time(sec)} ({cnt} events)")

        if has_events:
            reply = QMessageBox.question(
                self,
                "Recording Finished",
                f"Actions recording completed ({self._format_rec_time(sec)}, {cnt} events).\nExport edited video now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.export_recorded_video()

    def _on_rec_update_timer(self):
        if not self.recorder.is_active():
            self.rec_update_timer.stop()
            return
        if self.recorder.is_recording():
            sec = self.recorder.current_time()
            cnt = len(self.recorder.events)
            self.lbl_rec_status.setText(f"● REC {self._format_rec_time(sec)} ({cnt} actions)")

    def _shortcut_toggle_record(self):
        if self.recorder.is_active():
            self.stop_actions_record()
        else:
            self.start_actions_record()

    def export_recorded_video(self, *args):
        if not self.recorder.events or self.recorder.elapsed_time <= 0.05:
            QMessageBox.information(self, "Export Video", "No recorded actions to export.")
            return

        out_w = self.canvas.video_width if self.canvas.video_width > 0 else 1920
        out_h = self.canvas.video_height if self.canvas.video_height > 0 else 1080
        has_mic = bool(self.last_mic_wav and os.path.exists(self.last_mic_wav))
        has_sys = bool(self.last_sys_wav and os.path.exists(self.last_sys_wav))
        has_audio = has_mic or has_sys

        dlg = ExportDialog(default_w=out_w, default_h=out_h, has_audio=has_audio, parent=self)
        if dlg.exec_() != 1:
            return

        cfg = dlg.get_export_config()
        save_path = cfg['output_path']
        if not save_path:
            return

        progress_dialog = QProgressDialog("Preparing video export...", "Cancel", 0, 100, self)
        progress_dialog.setWindowTitle("Export Video")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        v_path = (self.temp_capture_video_path if self.temp_capture_video_path and os.path.exists(self.temp_capture_video_path) else None) or self.video_path or getattr(self.recorder.player, 'video_path', '')

        audio_target = None
        if cfg['include_audio']:
            if has_mic and has_sys:
                import subprocess
                ffmpeg_exe = get_ffmpeg_path()
                if ffmpeg_exe and os.path.exists(ffmpeg_exe):
                    mixed_wav = os.path.join(tempfile.gettempdir(), f"temp_mixed_{int(time.time())}_{os.getpid()}.wav")
                    cmd = [
                        ffmpeg_exe, "-y",
                        "-i", self.last_sys_wav,
                        "-i", self.last_mic_wav,
                        "-filter_complex", "amix=inputs=2:duration=longest:dropout_transition=0",
                        mixed_wav
                    ]
                    flags = 0x08000000 if os.name == 'nt' else 0
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                    if res.returncode == 0 and os.path.exists(mixed_wav) and os.path.getsize(mixed_wav) > 100:
                        audio_target = mixed_wav
                    else:
                        audio_target = self.last_sys_wav
                else:
                    audio_target = self.last_sys_wav
            elif has_sys:
                audio_target = self.last_sys_wav
            elif has_mic:
                audio_target = self.last_mic_wav

        self.export_worker = ExportVideoWorker(
            video_path=v_path,
            events=list(self.recorder.events),
            total_duration=self.recorder.elapsed_time,
            output_path=save_path,
            fps=cfg['fps'],
            out_size=(cfg['width'], cfg['height']),
            codec=cfg['codec'],
            bitrate=cfg.get('bitrate', '10M'),
            rate_control=cfg.get('rate_control', 'vbr'),
            audio_path=audio_target,
            hw_accel=cfg.get('hw_accel', True)
        )

        def on_progress(cur, total, msg):
            pct = int((cur / max(1, total)) * 100)
            progress_dialog.setValue(pct)
            progress_dialog.setLabelText(f"{msg} ({pct}%)")

        def on_finished(success, msg):
            progress_dialog.close()
            if success:
                QMessageBox.information(self, "Export Completed", f"Edited video successfully exported to:\n{msg}")
            else:
                QMessageBox.warning(self, "Export Error", f"Failed to export video:\n{msg}")

        progress_dialog.canceled.connect(self.export_worker.cancel)
        self.export_worker.progress.connect(on_progress)
        self.export_worker.finished.connect(on_finished)
        self.export_worker.start()


    def open_window_capture_dialog(self, *args):
        dlg = WindowCaptureDialog(parent=self)
        if dlg.exec_() == 1 and (dlg.selected_hwnd or getattr(dlg, 'selected_rect', None)):
            self.start_window_capture(dlg.selected_hwnd, dlg.selected_title, monitor_rect=getattr(dlg, 'selected_rect', None))

    def start_window_capture(self, hwnd: int, title: str, monitor_rect: tuple = None):
        is_monitor = (monitor_rect is not None) or (isinstance(hwnd, int) and hwnd < 0)
        prefix = "Screen" if is_monitor else "Stream"
        if self.active_project and not self.active_project.is_empty():
            self.new_project_tab(name=f"{prefix}: {title[:16]}", project_type="screen" if is_monitor else "window")

        self._stop_window_capture()
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        self.is_capturing_window = True
        self.captured_window_hwnd = hwnd
        self.captured_window_title = title
        self.captured_monitor_rect = monitor_rect
        self.total_frames = 0
        self.current_frame_idx = 0
        self.fps = 30.0
        self.video_path = ""
        tab_name = f"{prefix}: {title[:16]}"
        if self.active_project:
            self.active_project.name = tab_name
            self.active_project.project_type = 'screen' if is_monitor else 'window'
        if hasattr(self, 'project_tabs'):
            cur_idx = self.project_tabs.currentIndex()
            if cur_idx >= 0:
                self.project_tabs.setTabText(cur_idx, tab_name)

        label_type = "Live Screen" if is_monitor else "Live Window"
        self.setWindowTitle(f"FKVideoPlayer — {label_type}: {title}")
        if hasattr(self, 'lbl_time_info'):
            self.lbl_time_info.setText(f"{label_type}: {title}")

        self.window_capture_worker = WindowCaptureWorker(hwnd=hwnd, monitor_rect=monitor_rect, target_fps=30.0, parent=self)
        self.window_capture_worker.frame_captured.connect(self._on_captured_window_frame)
        self.window_capture_worker.start()

        ProjectManager.instance().add_project('screen' if is_monitor else 'window', f'{prefix}: {title}', str(hwnd))
        self._refresh_recent_projects_menu()

    def _stop_window_capture(self):
        if self.window_capture_worker:
            self.window_capture_worker.stop()
            self.window_capture_worker = None
        self.is_capturing_window = False

    def _on_captured_window_frame(self, frame_bgr, timestamp):
        if not self.is_capturing_window or frame_bgr is None:
            return
        self.canvas.set_bgr_frame(frame_bgr)

        if self.recorder.is_recording():
            h, w = frame_bgr.shape[:2]
            if w <= 0 or h <= 0:
                return

            if self.temp_capture_writer is None:
                target_w = (w // 2) * 2
                target_h = (h // 2) * 2
                self.temp_capture_writer_size = (target_w, target_h)
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                self.temp_capture_writer = cv2.VideoWriter(
                    self.temp_capture_video_path, fourcc, 30.0, (target_w, target_h)
                )
                self.canvas.video_width = target_w
                self.canvas.video_height = target_h
                self.video_path = self.temp_capture_video_path

            write_frame = frame_bgr
            if (w, h) != self.temp_capture_writer_size:
                write_frame = cv2.resize(frame_bgr, self.temp_capture_writer_size)

            self.temp_capture_writer.write(write_frame)
            self._capture_frame_count += 1
            self.current_frame_idx = self._capture_frame_count - 1
            self.total_frames = max(self.total_frames, self._capture_frame_count)
            self.recorder.record_frame(force=True)


    def save_actions_json(self, *args):
        if not self.recorder.events:
            QMessageBox.information(self, "Save Actions", "No recorded actions to save.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Actions (JSON)",
            "actions_recording.json",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if save_path:
            try:
                self.recorder.save_json(save_path)
                QMessageBox.information(self, "Saved", f"Actions saved to:\n{save_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def load_actions_json(self, *args):
        load_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Actions (JSON)",
            "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if load_path:
            try:
                self.recorder.load_json(load_path)
                has_events = len(self.recorder.events) > 0 and self.recorder.elapsed_time > 0.05
                self.btn_rec_export.setEnabled(has_events)
                self.btn_rec_save.setEnabled(has_events)
                sec = self.recorder.elapsed_time
                cnt = len(self.recorder.events)
                self.lbl_rec_status.setStyleSheet("color: #34C759; font-family: Consolas, monospace;")
                self.lbl_rec_status.setText(f"📂 Loaded: {self._format_rec_time(sec)} ({cnt} events)")
                QMessageBox.information(self, "Loaded", f"Loaded {cnt} events ({self._format_rec_time(sec)}).\nYou can now export to video.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to read file:\n{e}")

