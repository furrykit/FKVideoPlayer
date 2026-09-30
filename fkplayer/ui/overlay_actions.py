#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Overlay and canvas actions mixin for FKVideoPlayer.
Handles adding/deleting/duplicating overlays, z-ordering, drag & drop,
text overlay creation, and blank canvas generation.
"""

import os
import cv2
from PIL import Image
from PyQt5.QtCore import Qt, QPointF, QPoint, QRectF
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QColorDialog

from fkplayer.ui.canvas import OverlayObject, VideoCanvas
from fkplayer.ui.dialogs import TextOverlayDialog, NewCanvasDialog
from fkplayer.core.i18n import tr
from fkplayer.core.projects import ProjectManager
from fkplayer.core.logger import get_logger

logger = get_logger("OverlayActions")


class OverlayActionsMixin:
    """Mixin providing overlay manipulation, drag&drop, and canvas generation."""

    def delete_selected_overlay(self):
        if getattr(self.canvas, 'selected_overlays', None):
            self.canvas.remove_selected_overlays()
        elif self.canvas.selected_overlay:
            self.canvas.remove_overlay(self.canvas.selected_overlay)

    def _on_delete_shortcut(self):
        if self.canvas.active_tool == VideoCanvas.TOOL_SELECT and (
            getattr(self.canvas, 'selected_overlays', None) or self.canvas.selected_overlay
        ):
            self.delete_selected_overlay()
        else:
            self.canvas.clear_all_drawings()

    def _on_duplicate_shortcut(self):
        if self.canvas.active_tool == VideoCanvas.TOOL_SELECT and self.canvas.selected_overlay:
            self.duplicate_overlay(self.canvas.selected_overlay)

    def bring_overlay_to_front(self, ov):
        if ov and ov in self.canvas.overlays:
            old_order = list(self.canvas.overlays)
            self.canvas.overlays.remove(ov)
            self.canvas.overlays.append(ov)
            self.canvas.undo_stack.append(('reorder_overlays', old_order))
            self.canvas.update()
            self._refresh_overlay_tracks()

    def send_overlay_to_back(self, ov):
        if ov and ov in self.canvas.overlays:
            old_order = list(self.canvas.overlays)
            self.canvas.overlays.remove(ov)
            self.canvas.overlays.insert(0, ov)
            self.canvas.undo_stack.append(('reorder_overlays', old_order))
            self.canvas.update()
            self._refresh_overlay_tracks()

    def bring_overlay_forward(self, ov):
        if ov and ov in self.canvas.overlays:
            idx = self.canvas.overlays.index(ov)
            if idx < len(self.canvas.overlays) - 1:
                old_order = list(self.canvas.overlays)
                self.canvas.overlays[idx], self.canvas.overlays[idx + 1] = (
                    self.canvas.overlays[idx + 1], self.canvas.overlays[idx]
                )
                self.canvas.undo_stack.append(('reorder_overlays', old_order))
                self.canvas.update()
                self._refresh_overlay_tracks()

    def send_overlay_backward(self, ov):
        if ov and ov in self.canvas.overlays:
            idx = self.canvas.overlays.index(ov)
            if idx > 0:
                old_order = list(self.canvas.overlays)
                self.canvas.overlays[idx], self.canvas.overlays[idx - 1] = (
                    self.canvas.overlays[idx - 1], self.canvas.overlays[idx]
                )
                self.canvas.undo_stack.append(('reorder_overlays', old_order))
                self.canvas.update()
                self._refresh_overlay_tracks()

    def duplicate_overlay(self, ov):
        if not ov or ov not in self.canvas.overlays:
            return
        sid = self.recorder.allocate_stroke_id()
        cur_t = self.recorder.current_time() if self.recorder.is_active() else 0.0
        new_rect = QRectF(ov.rect)
        new_rect.translate(25.0, 25.0)
        new_ov = OverlayObject(sid, ov.file_path, new_rect, start_time=cur_t)
        self.canvas.overlays.append(new_ov)
        self.canvas.selected_overlay = new_ov
        self.canvas.undo_stack.append(('add_overlay', new_ov))
        if self.recorder.is_active():
            self.recorder.record_overlay_add(new_ov)
        self._refresh_overlay_tracks()
        self.canvas.drawing_changed.emit()
        self.canvas.update()

    def _on_overlay_anim_tick(self):
        if not self.is_playing and self.canvas.overlays:
            if any(ov.obj_type in (OverlayObject.TYPE_GIF, OverlayObject.TYPE_VIDEO) for ov in self.canvas.overlays):
                self.canvas.update()

    def add_overlay(self, file_path: str, pos: QPointF = None):
        if not file_path or not os.path.exists(file_path):
            logger.warning(f"add_overlay called with nonexistent file: {file_path}")
            return

        logger.info(f"Adding overlay from {file_path}")
        vw = self.canvas.video_width if self.canvas.video_width > 0 else 1280
        vh = self.canvas.video_height if self.canvas.video_height > 0 else 720

        ow = max(120.0, vw * 0.28)
        oh = ow * 0.5625
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif'):
            try:
                with Image.open(file_path) as im:
                    if im.width > 0 and im.height > 0:
                        oh = ow * (im.height / float(im.width))
            except Exception as e:
                logger.warning(f"Failed to read image dimensions for {file_path}: {e}")
        elif ext in ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v'):
            try:
                temp_cap = cv2.VideoCapture(file_path)
                if temp_cap.isOpened():
                    cw = temp_cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                    ch = temp_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    if cw > 0 and ch > 0:
                        oh = ow * (ch / float(cw))
                temp_cap.release()
            except Exception as e:
                logger.warning(f"Failed to read video dimensions for {file_path}: {e}")

        if isinstance(pos, (QPointF, QPoint)):
            ox = max(5.0, min(vw - 25.0, pos.x() - ow / 2.0))
            oy = max(5.0, min(vh - 25.0, pos.y() - oh / 2.0))
        else:
            idx_offset = (len(self.canvas.overlays) % 6) * 25.0
            ox = max(15.0, vw - ow - 25.0 - idx_offset)
            oy = 25.0 + idx_offset

        sid = self.recorder.allocate_stroke_id()
        cur_t = self.recorder.current_time() if self.recorder.is_active() else 0.0
        overlay = OverlayObject(sid, file_path, QRectF(ox, oy, ow, oh), start_time=cur_t)
        self.canvas.overlays.append(overlay)
        self.canvas.selected_overlay = overlay
        self.canvas.undo_stack.append(('add_overlay', overlay))

        if self.recorder.is_active():
            self.recorder.record_overlay_add(overlay)

        self._select_tool(VideoCanvas.TOOL_SELECT)
        self._refresh_overlay_tracks()
        self.canvas.drawing_changed.emit()
        self.canvas.update()
        logger.info(f"Overlay created: sid={sid}, rect=({ox:.1f}, {oy:.1f}, {ow:.1f}, {oh:.1f})")

    def add_overlay_dialog(self, pos: QPointF = None):
        if not isinstance(pos, (QPointF, QPoint)):
            pos = None
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image, GIF, or Video Overlay",
            "",
            "Media Files (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.mp4 *.avi *.mov *.mkv *.webm);;Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;Videos (*.mp4 *.avi *.mov *.mkv *.webm);;All Files (*.*)"
        )
        if file_path:
            self.add_overlay(file_path, pos=pos)

    def _refresh_overlay_tracks(self):
        if hasattr(self, 'overlay_tracks_container') and self.overlay_tracks_container is not None:
            self.overlay_tracks_container.refresh_tracks()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return

        first_path = urls[0].toLocalFile()
        if not first_path or not os.path.exists(first_path):
            return

        has_active_canvas = (self.canvas is not None and self.canvas.video_width > 0 and self.canvas.video_height > 0)
        if not has_active_canvas:
            self.load_video(first_path)
            return

        for i, u in enumerate(urls):
            fpath = u.toLocalFile()
            if not fpath or not os.path.exists(fpath):
                continue
            ext = os.path.splitext(fpath)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif',
                       '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v'):
                self.add_overlay(fpath)

    def set_volume(self, val):
        self.current_volume = max(0, min(100, val))
        self.slider_volume.blockSignals(True)
        self.slider_volume.setValue(self.current_volume)
        self.slider_volume.blockSignals(False)
        self.lbl_volume.setText(f"{self.current_volume}%")

        if not self.is_muted:
            self.audio_player.setVolume(self.current_volume)
            self._update_mute_icon()

    def adjust_volume(self, delta):
        if self.is_muted:
            self.toggle_mute()
        self.set_volume(self.current_volume + delta)

    def toggle_mute(self, *args):
        self.is_muted = not self.is_muted
        self.audio_player.setMuted(self.is_muted)
        self._update_mute_icon()

    def _update_mute_icon(self):
        if self.is_muted or self.current_volume == 0:
            self.btn_mute.setText("🔇")
        elif self.current_volume < 50:
            self.btn_mute.setText("🔉")
        else:
            self.btn_mute.setText("🔊")

    def _select_tool(self, tool_code):
        self.btn_tool_pen.setChecked(tool_code == VideoCanvas.TOOL_PEN)
        self.btn_tool_eraser.setChecked(tool_code == VideoCanvas.TOOL_ERASER)
        self.btn_tool_pan.setChecked(tool_code == VideoCanvas.TOOL_PAN)
        self.btn_tool_select.setChecked(tool_code == VideoCanvas.TOOL_SELECT)
        self.canvas.set_tool(tool_code)

        if hasattr(self, 'spin_width') and hasattr(self, 'lbl_tool_size'):
            self.spin_width.blockSignals(True)
            if tool_code == VideoCanvas.TOOL_ERASER:
                self.lbl_tool_size.setText("Eraser Size:")
                self.spin_width.setValue(int(round(self.canvas.eraser_radius)))
            else:
                self.lbl_tool_size.setText("Brush Size:")
                self.spin_width.setValue(int(round(self.canvas.pen_width)))
            self.spin_width.blockSignals(False)

    def _update_color_buttons_state(self, current_hex):
        current_hex = current_hex.upper()
        for hex_code, btn in self.preset_color_buttons.items():
            if hex_code == current_hex:
                btn.setStyleSheet(
                    f"QPushButton#ColorPresetBtn, QPushButton {{"
                    f"  background-color: {hex_code};"
                    f"  min-width: 16px; max-width: 16px;"
                    f"  min-height: 16px; max-height: 16px;"
                    f"  border-radius: 10px;"
                    f"  border: 2px solid #FFFFFF;"
                    f"  outline: none;"
                    f"  padding: 0px; margin: 0px;"
                    f"}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton#ColorPresetBtn, QPushButton {{"
                    f"  background-color: {hex_code};"
                    f"  min-width: 16px; max-width: 16px;"
                    f"  min-height: 16px; max-height: 16px;"
                    f"  border-radius: 10px;"
                    f"  border: 2px solid #484858;"
                    f"  outline: none;"
                    f"  padding: 0px; margin: 0px;"
                    f"}}"
                )

        self.color_indicator.setStyleSheet(
            f"QFrame#ColorIndicator, QFrame {{"
            f"  background-color: {current_hex};"
            f"  min-width: 14px; max-width: 14px;"
            f"  min-height: 14px; max-height: 14px;"
            f"  border: 2px solid #FFFFFF;"
            f"  border-radius: 4px;"
            f"  padding: 0px; margin: 0px;"
            f"}}"
        )

    def _set_brush_color(self, hex_color):
        self.canvas.set_pen_color(hex_color)
        self._update_color_buttons_state(hex_color)
        if self.canvas.active_tool != VideoCanvas.TOOL_PEN:
            self._select_tool(VideoCanvas.TOOL_PEN)

    def _pick_custom_color(self, *args):
        col = QColorDialog.getColor(self.canvas.pen_color, self, "Select Brush Color")
        if col.isValid():
            self._set_brush_color(col.name())

    def _on_pen_width_changed(self, val):
        if self.canvas.active_tool == VideoCanvas.TOOL_ERASER:
            self.canvas.set_eraser_radius(val)
        else:
            self.canvas.set_pen_width(val)

    def _on_canvas_zoom_changed(self, zoom):
        self.lbl_zoom.setText(f"{int(round(zoom * 100))}%")
        if hasattr(self, 'recorder'):
            self.recorder.record_frame()

    def _on_drawing_changed(self):
        pass


    def add_text_overlay(self, text_data: dict = None, pos: QPointF = None):
        if not text_data:
            dlg = TextOverlayDialog(parent=self)
            if dlg.exec_() != 1:
                return
            text_data = dlg.get_data()

        vw = self.canvas.video_width if self.canvas.video_width > 0 else 1920
        vh = self.canvas.video_height if self.canvas.video_height > 0 else 1080
        tw = max(180.0, vw * 0.35)
        th = max(60.0, vh * 0.12)

        if isinstance(pos, (QPointF, QPoint)):
            tx = max(0.0, min(pos.x(), vw - tw))
            ty = max(0.0, min(pos.y(), vh - th))
        else:
            tx = (vw - tw) * 0.5
            ty = (vh - th) * 0.5

        rect = QRectF(tx, ty, tw, th)
        sid = self.recorder.allocate_stroke_id()
        cur_t = self.recorder.current_time() if self.recorder.is_active() else 0.0
        ov = OverlayObject(sid, 'text', rect, start_time=cur_t, text_data=text_data)
        self.canvas.overlays.append(ov)
        self.canvas.selected_overlay = ov
        self.canvas.undo_stack.append(('add_overlay', ov))
        self.canvas.active_tool = VideoCanvas.TOOL_SELECT
        self._select_tool(VideoCanvas.TOOL_SELECT)
        if self.recorder.is_active():
            self.recorder.record_overlay_add(ov)
        self.canvas.drawing_changed.emit()
        self.canvas.update()
        self._refresh_overlay_tracks()

    def open_new_canvas_dialog(self, *args):
        dlg = NewCanvasDialog(parent=self)
        if dlg.exec_() == 1:
            w, h, bg_hex, fps = dlg.get_settings()
            self.create_blank_canvas(w, h, bg_hex, fps)

    def create_blank_canvas(self, width=1920, height=1080, bg_hex="#14141A", fps=30.0):
        if self.active_project and not self.active_project.is_empty():
            self.new_project_tab(name=f"Canvas {width}x{height}", project_type="blank")

        self._stop_window_capture()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.video_path = ""
        self.fps = fps
        self.total_frames = int(fps * 3600) # 1 hour virtual timeline
        self.current_frame_idx = 0
        tab_name = f"Canvas {width}x{height}"
        if self.active_project:
            self.active_project.name = tab_name
            self.active_project.project_type = 'blank'
        if hasattr(self, 'project_tabs'):
            cur_idx = self.project_tabs.currentIndex()
            if cur_idx >= 0:
                self.project_tabs.setTabText(cur_idx, tab_name)

        self.canvas.set_blank_canvas(width, height, bg_hex)
        self.timeline_slider.setRange(0, self.total_frames - 1)
        self.timeline_slider.setValue(0)
        self.setWindowTitle(f"FKVideoPlayer — {tab_name}")
        if hasattr(self, 'lbl_time_info'):
            self.lbl_time_info.setText(f"{width}x{height} | Blank Canvas | {fps:.0f} FPS")
        ProjectManager.instance().add_project('blank', tab_name, f'{width}x{height}', width, height)
        self._refresh_recent_projects_menu()

