#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tab and project lifecycle management mixin for FKVideoPlayer.
Handles multi-project tab switching, state isolation, tab creation, and closing.
"""

import os
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtMultimedia import QMediaContent
from fkplayer.core.i18n import tr
from fkplayer.core.session import ProjectSession
from fkplayer.core.logger import get_logger

logger = get_logger("Tabs")


class TabManagerMixin:
    """Mixin providing multi-project tab and lifecycle management."""

    def new_project_tab(self, name=None, project_type="empty") -> ProjectSession:
        if not name:
            count = len(self.projects) + 1
            name = f"Project {count}"

        proj = ProjectSession(self, name=name, project_type=project_type)
        self.projects.append(proj)
        proj.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
        proj.canvas.drawing_changed.connect(self._on_drawing_changed)

        idx = self.project_tabs.addTab(proj.canvas, name)
        self.project_tabs.setCurrentIndex(idx)
        return proj

    def close_current_tab(self):
        idx = self.project_tabs.currentIndex()
        if idx >= 0:
            self._on_tab_close_requested(idx)

    def next_project_tab(self):
        if not hasattr(self, 'project_tabs') or self.project_tabs.count() <= 1:
            return
        idx = (self.project_tabs.currentIndex() + 1) % self.project_tabs.count()
        self.project_tabs.setCurrentIndex(idx)

    def prev_project_tab(self):
        if not hasattr(self, 'project_tabs') or self.project_tabs.count() <= 1:
            return
        idx = (self.project_tabs.currentIndex() - 1) % self.project_tabs.count()
        self.project_tabs.setCurrentIndex(idx)

    def _on_tab_changed(self, index: int):
        if not (0 <= index < len(self.projects)):
            return

        proj = self.projects[index]

        if self.play_timer.isActive():
            self.play_timer.stop()

        self.timeline_slider.blockSignals(True)
        self.timeline_slider.fps = proj.fps
        self.timeline_slider.setRange(0, max(0, proj.total_frames - 1))
        self.timeline_slider.setValue(proj.current_frame_idx)
        self.timeline_slider.blockSignals(False)

        if proj.has_audio and proj.temp_audio_path and os.path.exists(proj.temp_audio_path):
            media_url = QUrl.fromLocalFile(proj.temp_audio_path)
            self.audio_player.setMedia(QMediaContent(media_url))
            self.audio_player.setVolume(self.current_volume if not self.is_muted else 0)
            target_ms = int((proj.current_frame_idx / max(1.0, proj.fps)) * 1000)
            self.audio_player.setPosition(target_ms)
            if proj.is_playing:
                self.audio_player.play()
            else:
                self.audio_player.pause()
        else:
            self.audio_player.setMedia(QMediaContent())

        if proj.is_playing:
            self.btn_play_pause.setText("❚❚ Pause")
            self._update_timer_interval()
            self.play_timer.start()
        else:
            self.btn_play_pause.setText("▶ Play")

        self.btn_loop.setChecked(proj.is_looping)
        self.combo_speed.blockSignals(True)
        spd_str = f"{proj.playback_speed}x"
        spd_idx = self.combo_speed.findText(spd_str)
        if spd_idx >= 0:
            self.combo_speed.setCurrentIndex(spd_idx)
        else:
            self.combo_speed.setCurrentText(spd_str)
        self.combo_speed.blockSignals(False)

        self._select_tool(proj.canvas.active_tool)
        self.spin_width.setValue(int(proj.canvas.pen_width))
        self._update_color_buttons_state(proj.canvas.pen_color.name())

        if proj.recorder.is_recording():
            self.lbl_rec_status.setText("● REC")
            self.lbl_rec_status.setStyleSheet("color: #FF3B30; font-weight: bold; font-size: 11px;")
            self.btn_rec_start.setEnabled(False)
            self.btn_rec_pause.setEnabled(True)
            self.btn_rec_stop.setEnabled(True)
        elif proj.recorder.is_paused():
            self.lbl_rec_status.setText("❚❚ PAUSED")
            self.lbl_rec_status.setStyleSheet("color: #FFCC00; font-weight: bold; font-size: 11px;")
            self.btn_rec_start.setEnabled(False)
            self.btn_rec_pause.setEnabled(True)
            self.btn_rec_stop.setEnabled(True)
        else:
            self.lbl_rec_status.setText("● Ready")
            self.lbl_rec_status.setStyleSheet("color: #7E7E94; font-size: 11px;")
            self.btn_rec_start.setEnabled(True)
            self.btn_rec_pause.setEnabled(False)
            self.btn_rec_stop.setEnabled(False)

        has_events = len(proj.recorder.events) > 0 and proj.recorder.elapsed_time > 0.05
        self.btn_rec_export.setEnabled(has_events)
        self.btn_rec_save.setEnabled(has_events)

        self._update_time_label()
        self.setWindowTitle(f"FKVideoPlayer — {proj.name}")
        self._refresh_overlay_tracks()
        proj.canvas.update()

    def _on_tab_close_requested(self, index: int):
        if not (0 <= index < len(self.projects)):
            return

        proj = self.projects[index]
        if not self.prompt_unsaved_changes(project=proj):
            return

        if proj.is_playing:
            proj.is_playing = False
            if self.active_project == proj:
                self.play_timer.stop()
                self.audio_player.pause()
                self.btn_play_pause.setText("▶ Play")

        proj.close()
        self.projects.pop(index)
        self.project_tabs.removeTab(index)

        if len(self.projects) == 0:
            self.new_project_tab(name="Project 1", project_type="empty")


    def has_unsaved_changes(self) -> bool:
        if self.active_project:
            return self.active_project.has_unsaved_changes()
        return False

    def prompt_unsaved_changes(self, project=None) -> bool:
        target = project if project is not None else self.active_project
        if target is None or not target.has_unsaved_changes():
            return True
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or getattr(self, '_suppress_unsaved_prompt', False):
            return True

        msg = QMessageBox(self)
        msg.setWindowTitle(tr('prompt_unsaved_title'))
        msg.setText(f"{tr('prompt_unsaved_text')}\n({target.name})")
        msg.setIcon(QMessageBox.Question)
        btn_save = msg.addButton(tr('btn_export_save'), QMessageBox.AcceptRole)
        btn_save.setStyleSheet("background-color: #007AFF; color: #FFFFFF; font-weight: bold;")
        btn_discard = msg.addButton(tr('btn_discard'), QMessageBox.DestructiveRole)
        btn_cancel = msg.addButton(QMessageBox.Cancel)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == btn_save:
            self.export_recorded_video()
            return True
        elif clicked == btn_discard:
            return True
        else:
            return False

    def close_project(self):
        idx = self.project_tabs.currentIndex()
        if idx >= 0:
            self._on_tab_close_requested(idx)

