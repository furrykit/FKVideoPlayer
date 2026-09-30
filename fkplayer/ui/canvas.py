#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video canvas, drawing overlay, stroke models, and overlay objects for FKVideoPlayer.
"""

import os
import time
import math
import collections
import cv2
import numpy as np
from PIL import Image, ImageSequence

from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal, QTimer
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QBrush, QCursor, QFont,
    QPainterPath
)
from PyQt5.QtWidgets import (
    QWidget, QMenu
)

from fkplayer.core.logger import get_logger
from fkplayer.core.geometry import dist_to_segment_sq, erase_stroke_subsegments, simplify_points
from fkplayer.ui.dialogs import VideoOverlaySettingsDialog, TextOverlayDialog

logger = get_logger("Canvas")


class Stroke:
    def __init__(self, color, width, points=None, stroke_id=None):
        self.stroke_id = stroke_id
        self.color = QColor(color)
        self.width = float(width)
        self.points = []
        self.path = QPainterPath()
        if points:
            for pt in points:
                self.add_point(pt)

    def add_point(self, pt: QPointF):
        p = QPointF(pt.x(), pt.y())
        if not self.points:
            self.path.moveTo(p)
        else:
            self.path.lineTo(p)
        self.points.append(p)

    def copy(self):
        return Stroke(self.color, self.width, [QPointF(p.x(), p.y()) for p in self.points], stroke_id=self.stroke_id)


class OverlayObject:
    TYPE_IMAGE = "image"
    TYPE_GIF = "gif"
    TYPE_VIDEO = "video"
    TYPE_TEXT = "text"
    MAX_VIDEO_CACHE = 45

    def __init__(self, obj_id: int, file_path: str, rect: QRectF, start_time: float = 0.0, text_data: dict = None):
        self.obj_id = obj_id
        self.file_path = file_path
        self.rect = QRectF(rect)
        self.start_time = float(start_time)
        self.text_data = text_data

        if text_data or file_path == 'text':
            self.obj_type = self.TYPE_TEXT
            self.text = text_data.get('text', 'Sample Text') if text_data else 'Sample Text'
            self.font_family = text_data.get('font_family', 'Segoe UI') if text_data else 'Segoe UI'
            self.font_size = text_data.get('font_size', 36) if text_data else 36
            self.font_bold = text_data.get('bold', True) if text_data else True
            self.font_italic = text_data.get('italic', False) if text_data else False
            self.text_color = text_data.get('color', '#FFFFFF') if text_data else '#FFFFFF'
            self.bg_color = text_data.get('bg_color', 'transparent') if text_data else 'transparent'
        else:
            self.obj_type = self._detect_type(file_path)
            self.text = ""
            self.font_family = "Segoe UI"
            self.font_size = 36
            self.font_bold = False
            self.font_italic = False
            self.text_color = "#FFFFFF"
            self.bg_color = "transparent"

        self.static_image = None
        self.gif_frames = []
        self.gif_durations = []
        self.gif_total_dur = 0.0
        self.video_cap = None
        self.video_fps = 25.0
        self.video_total_frames = 0
        self.video_cached_frame = None
        self.video_cached_idx = -1
        self.video_last_idx = -1
        self.video_cache = collections.OrderedDict()

        self.is_playing = True
        self.loop = True
        self.playback_speed = 1.0
        self.start_offset = 0.0
        self.sync_with_timeline = True
        self.opacity = 1.0
        self.keep_aspect_ratio = True
        self.orig_aspect_ratio = None
        self.is_visible = True
        self.internal_clock = time.perf_counter()

        if self.obj_type != self.TYPE_TEXT:
            self._load_media()
        else:
            if self.rect and self.rect.height() > 0:
                self.orig_aspect_ratio = self.rect.width() / float(self.rect.height())

    def _detect_type(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext == '.gif':
            return self.TYPE_GIF
        elif ext in ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v'):
            return self.TYPE_VIDEO
        return self.TYPE_IMAGE

    def _load_media(self):
        if not self.file_path or not os.path.exists(self.file_path):
            return
        if self.obj_type == self.TYPE_IMAGE:
            img = QImage(self.file_path)
            if not img.isNull():
                self.static_image = img
            else:
                try:
                    with Image.open(self.file_path) as im:
                        rgba = im.convert('RGBA')
                        data = rgba.tobytes('raw', 'RGBA')
                        self.static_image = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888).copy()
                except Exception:
                    pass
            if self.static_image and not self.static_image.isNull() and self.static_image.height() > 0:
                self.orig_aspect_ratio = self.static_image.width() / float(self.static_image.height())
        elif self.obj_type == self.TYPE_GIF:
            try:
                with Image.open(self.file_path) as im:
                    for frame in ImageSequence.Iterator(im):
                        dur_ms = frame.info.get('duration', 100)
                        if dur_ms <= 0:
                            dur_ms = 100
                        self.gif_durations.append(dur_ms / 1000.0)
                        rgba = frame.convert('RGBA')
                        data = rgba.tobytes('raw', 'RGBA')
                        qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888).copy()
                        self.gif_frames.append(qimg)
                    self.gif_total_dur = sum(self.gif_durations) if self.gif_durations else 1.0
                    if self.gif_frames and self.gif_frames[0].height() > 0:
                        self.orig_aspect_ratio = self.gif_frames[0].width() / float(self.gif_frames[0].height())
            except Exception:
                self.obj_type = self.TYPE_IMAGE
                self.static_image = QImage(self.file_path)
                if self.static_image and not self.static_image.isNull() and self.static_image.height() > 0:
                    self.orig_aspect_ratio = self.static_image.width() / float(self.static_image.height())
        elif self.obj_type == self.TYPE_VIDEO:
            self.video_cap = cv2.VideoCapture(self.file_path)
            if self.video_cap.isOpened():
                self.video_fps = float(self.video_cap.get(cv2.CAP_PROP_FPS))
                if self.video_fps <= 0 or np.isnan(self.video_fps):
                    self.video_fps = 25.0
                self.video_total_frames = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                vw = float(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                vh = float(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if vw > 0 and vh > 0:
                    self.orig_aspect_ratio = vw / vh
            else:
                self.video_cap = None

        if self.orig_aspect_ratio is None and self.rect and self.rect.height() > 0:
            self.orig_aspect_ratio = self.rect.width() / float(self.rect.height())

    def get_duration(self) -> float:
        if self.obj_type == self.TYPE_VIDEO:
            return self.video_total_frames / max(1.0, self.video_fps)
        elif self.obj_type == self.TYPE_GIF:
            return getattr(self, 'gif_total_dur', 1.0)
        return 0.0

    def get_current_time(self, elapsed_sec: float = 0.0) -> float:
        if self.obj_type != self.TYPE_VIDEO or self.video_total_frames <= 0:
            return 0.0
        dur = self.get_duration()
        spd = max(0.1, getattr(self, 'playback_speed', 1.0))
        offset = getattr(self, 'start_offset', 0.0)

        if getattr(self, 'sync_with_timeline', True):
            if getattr(self, 'is_playing', True):
                v_time = max(0.0, (float(elapsed_sec) - self.start_time) * spd + offset)
            else:
                v_time = max(0.0, (float(elapsed_sec) - self.start_time) + offset)
        else:
            if getattr(self, 'is_playing', True):
                now = time.perf_counter()
                v_time = max(0.0, (now - getattr(self, 'internal_clock', now)) * spd + offset)
            else:
                v_time = max(0.0, offset)

        if getattr(self, 'loop', True) and dur > 0:
            v_time = v_time % dur
        else:
            v_time = min(dur, v_time)
        return v_time

    def seek_to_seconds(self, sec: float):
        if self.obj_type != self.TYPE_VIDEO or self.video_cap is None or self.video_total_frames <= 0:
            return
        dur = self.get_duration()
        if getattr(self, 'loop', True) and dur > 0:
            sec = sec % dur
        else:
            sec = max(0.0, min(dur, sec))
        self.start_offset = sec
        self.internal_clock = time.perf_counter()
        target_f = max(0, min(self.video_total_frames - 1, int(round(sec * self.video_fps))))
        self._load_and_cache_frame(target_f)

    def seek_to_frame(self, frame_idx: int):
        if self.obj_type != self.TYPE_VIDEO or self.video_cap is None or self.video_total_frames <= 0:
            return
        target_f = max(0, min(self.video_total_frames - 1, frame_idx))
        self.start_offset = target_f / max(1.0, self.video_fps)
        self.internal_clock = time.perf_counter()
        self._load_and_cache_frame(target_f)

    def step_frames(self, delta: int):
        cur_f = getattr(self, 'video_cached_idx', 0)
        self.seek_to_frame(cur_f + delta)

    def _load_and_cache_frame(self, target_f: int):
        if self.video_cap is None or not self.video_cap.isOpened():
            return None
        if target_f in self.video_cache:
            self.video_cached_frame = self.video_cache[target_f]
            self.video_cached_idx = target_f
            return self.video_cached_frame

        if target_f == self.video_last_idx + 1:
            ret, frame = self.video_cap.read()
        else:
            self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)
            ret, frame = self.video_cap.read()

        if ret and frame is not None:
            self.video_last_idx = target_f
            h, w, ch = frame.shape
            bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
            qimg = QImage(bgra.data, w, h, w * 4, QImage.Format_RGB32).copy()
            del frame, bgra
            self.video_cached_frame = qimg
            self.video_cached_idx = target_f

            frame_bytes = max(1, w * h * 4)
            max_ov_frames = max(1, min(30, int(32 * 1024 * 1024 // frame_bytes)))
            while len(self.video_cache) >= max_ov_frames:
                self.video_cache.popitem(last=False)
            self.video_cache[target_f] = qimg
            return qimg

        if self.video_cached_frame is not None:
            return self.video_cached_frame
        if self.video_cache:
            return next(reversed(self.video_cache.values()))
        return None

    def get_frame_at_time(self, elapsed_sec: float) -> QImage:
        if not getattr(self, 'is_visible', True):
            return None
        if self.obj_type == self.TYPE_TEXT:
            w, h = max(10, int(self.rect.width())), max(10, int(self.rect.height()))
            img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            if self.bg_color and self.bg_color != 'transparent':
                p.setBrush(QColor(self.bg_color))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(0, 0, w, h, 6, 6)
            font = QFont(self.font_family, self.font_size)
            font.setBold(self.font_bold)
            font.setItalic(self.font_italic)
            p.setFont(font)
            p.setPen(QColor(self.text_color))
            p.drawText(0, 0, w, h, Qt.AlignCenter | Qt.TextWordWrap, self.text)
            p.end()
            return img
        elif self.obj_type == self.TYPE_IMAGE:
            return self.static_image
        elif self.obj_type == self.TYPE_GIF:
            if not self.gif_frames:
                return self.static_image
            if self.gif_total_dur <= 0:
                return self.gif_frames[0]
            if getattr(self, 'sync_with_timeline', True):
                v_time = max(0.0, float(elapsed_sec) - self.start_time)
            else:
                v_time = max(0.0, time.perf_counter() - getattr(self, 'internal_clock', 0.0))
            cycle_time = v_time % self.gif_total_dur
            acc = 0.0
            for i, dur in enumerate(self.gif_durations):
                acc += dur
                if cycle_time <= acc:
                    return self.gif_frames[i]
            return self.gif_frames[-1]
        elif self.obj_type == self.TYPE_VIDEO:
            if self.video_cap is None or not self.video_cap.isOpened() or self.video_total_frames <= 0:
                return None

            dur = self.get_duration()
            spd = max(0.1, getattr(self, 'playback_speed', 1.0))
            offset = getattr(self, 'start_offset', 0.0)

            if getattr(self, 'sync_with_timeline', True):
                if getattr(self, 'is_playing', True):
                    v_time = max(0.0, (float(elapsed_sec) - self.start_time) * spd + offset)
                else:
                    v_time = max(0.0, (float(elapsed_sec) - self.start_time) + offset)
            else:
                if getattr(self, 'is_playing', True):
                    now = time.perf_counter()
                    v_time = max(0.0, (now - getattr(self, 'internal_clock', now)) * spd + offset)
                else:
                    v_time = max(0.0, offset)

            if getattr(self, 'loop', True) and self.video_total_frames > 0:
                target_f = int(v_time * self.video_fps) % self.video_total_frames
            else:
                target_f = min(self.video_total_frames - 1, max(0, int(v_time * self.video_fps)))

            if target_f == self.video_cached_idx and self.video_cached_frame is not None:
                return self.video_cached_frame

            return self._load_and_cache_frame(target_f)
        return None

    def reopen(self):
        if self.obj_type == self.TYPE_VIDEO:
            if self.video_cap is None or not self.video_cap.isOpened():
                if self.file_path and os.path.exists(self.file_path):
                    self._load_media()
        elif self.obj_type in (self.TYPE_IMAGE, self.TYPE_GIF):
            if self.static_image is None or self.static_image.isNull():
                if self.file_path and os.path.exists(self.file_path):
                    self._load_media()

    def close(self):
        if self.video_cap is not None:
            try:
                self.video_cap.release()
            except Exception:
                pass
            self.video_cap = None
        self.video_cache.clear()


class VideoCanvas(QWidget):
    zoom_changed = pyqtSignal(float)
    drawing_changed = pyqtSignal()

    TOOL_PEN = 0
    TOOL_ERASER = 1
    TOOL_PAN = 2
    TOOL_SELECT = 3
    TOOL_TEXT = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setStyleSheet("background-color: #121216;")

        self.current_qimage = None
        self.video_width = 0
        self.video_height = 0

        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.is_panning = False
        self.last_mouse_pos = QPointF(0, 0)
        self._press_pos = None

        self.active_tool = self.TOOL_PEN
        self.pen_color = QColor("#FF3B30")
        self.pen_width = 4.0
        self.eraser_radius = 16.0

        self.strokes = []
        self.current_stroke = None
        self.undo_stack = []
        self.recorder = None

        self.overlays = []
        self.selected_overlays = []
        self.hover_overlay = None
        self._overlay_drag_mode = None
        self._drag_start_vpt = None
        self._drag_start_rect = None
        self._drag_start_rects = {}
        self._is_rubber_banding = False
        self._rubber_band_start = None
        self._rubber_band_rect = None

        self.overlay_anim_timer = QTimer(self)
        self.overlay_anim_timer.setInterval(33)
        self.overlay_anim_timer.timeout.connect(self._on_overlay_anim_tick)
        self.overlay_anim_timer.start()

        self.update_cursor()

    @property
    def selected_overlay(self):
        return self.selected_overlays[-1] if self.selected_overlays else None

    @selected_overlay.setter
    def selected_overlay(self, val):
        if val is None:
            self.selected_overlays = []
        elif val not in self.selected_overlays:
            self.selected_overlays = [val]

    def _on_overlay_anim_tick(self):
        if any(ov.obj_type in (OverlayObject.TYPE_GIF, OverlayObject.TYPE_VIDEO) and getattr(ov, 'is_playing', True) for ov in self.overlays):
            self.update()

    def set_bgr_frame(self, frame_bgr: np.ndarray):
        if frame_bgr is None:
            return
        h, w, ch = frame_bgr.shape
        self.video_width = w
        self.video_height = h
        qimg = QImage(frame_bgr.data, w, h, ch * w, QImage.Format_BGR888).copy()
        self.current_qimage = qimg
        self.update()

    def set_blank_canvas(self, width: int, height: int, bg_hex: str = "#14141A"):
        self.video_width = width
        self.video_height = height
        img = QImage(width, height, QImage.Format_RGB32)
        img.fill(QColor(bg_hex))
        self.current_qimage = img
        self.fit_to_view()
        self.update()

    def _create_brush_cursor(self) -> QCursor:
        d = max(5, int(round(self.pen_width)))
        if d % 2 == 0:
            d += 1
        size = d + 4
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.5))
        painter.drawEllipse(2, 2, d, d)
        painter.setPen(QPen(self.pen_color, 1.0))
        painter.drawEllipse(2, 2, d, d)

        center = size // 2
        painter.setPen(QPen(QColor(0, 0, 0, 255), 1))
        painter.drawPoint(center, center)
        painter.setPen(QPen(QColor(255, 255, 255, 255), 1))
        painter.drawPoint(center, center)
        painter.end()

        return QCursor(pixmap, center, center)

    def _create_eraser_cursor(self) -> QCursor:
        d = max(8, int(round(self.eraser_radius * 2)))
        if d % 2 == 0:
            d += 1
        size = d + 4
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(0, 0, 0, 200), 1.5))
        painter.drawEllipse(2, 2, d, d)
        painter.setPen(QPen(QColor(255, 255, 255, 240), 1.0, Qt.DashLine))
        painter.drawEllipse(2, 2, d, d)

        center = size // 2
        painter.setPen(QPen(QColor(255, 60, 60, 240), 1))
        painter.drawPoint(center, center)
        painter.end()

        return QCursor(pixmap, center, center)

    def update_cursor(self):
        if self.active_tool == self.TOOL_PEN:
            self.setCursor(self._create_brush_cursor())
        elif self.active_tool == self.TOOL_ERASER:
            self.setCursor(self._create_eraser_cursor())
        elif self.active_tool == self.TOOL_PAN:
            self.setCursor(Qt.OpenHandCursor)
        elif self.active_tool == self.TOOL_SELECT:
            self.setCursor(Qt.ArrowCursor)

    def set_tool(self, tool_code):
        self.active_tool = tool_code
        self.update_cursor()
        self.update()

    def remove_overlay(self, ov, record_undo=True):
        if ov in self.overlays:
            idx = self.overlays.index(ov)
            self.overlays.remove(ov)
            if self.recorder:
                self.recorder.record_overlay_remove(ov.obj_id)
            ov.close()
            if ov in self.selected_overlays:
                self.selected_overlays.remove(ov)
            if record_undo:
                self.undo_stack.append(('delete_overlay', [(ov, idx)]))
            p = self.window()
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
            self.update()
            self.drawing_changed.emit()

    def remove_selected_overlays(self, record_undo=True):
        if not self.selected_overlays:
            return
        items = []
        for ov in list(self.selected_overlays):
            if ov in self.overlays:
                idx = self.overlays.index(ov)
                items.append((ov, idx))
                self.overlays.remove(ov)
                if self.recorder:
                    self.recorder.record_overlay_remove(ov.obj_id)
                ov.close()
        self.selected_overlays.clear()
        if items and record_undo:
            self.undo_stack.append(('delete_overlay', items))
        p = self.window()
        if hasattr(p, '_refresh_overlay_tracks'):
            p._refresh_overlay_tracks()
        self.update()
        self.drawing_changed.emit()

    def _get_current_time(self) -> float:
        if self.recorder and self.recorder.is_active():
            return self.recorder.current_time()
        p = self.window()
        if p and hasattr(p, 'current_frame_idx') and hasattr(p, 'fps') and p.fps > 0:
            return p.current_frame_idx / p.fps
        return 0.0

    def set_pen_color(self, color):
        self.pen_color = QColor(color)
        self.update_cursor()
        self.update()

    def set_pen_width(self, width):
        self.pen_width = max(1.0, float(width))
        self.update_cursor()
        self.update()

    def set_eraser_radius(self, radius):
        self.eraser_radius = max(2.0, float(radius))
        self.update_cursor()
        self.update()

    def set_frame(self, frame_bgr_or_qimg):
        if frame_bgr_or_qimg is None:
            self.current_qimage = None
            self.update()
            return

        if isinstance(frame_bgr_or_qimg, QImage):
            self.current_qimage = frame_bgr_or_qimg
            w = frame_bgr_or_qimg.width()
            h = frame_bgr_or_qimg.height()
        else:
            h, w = frame_bgr_or_qimg.shape[:2]
            bgra = cv2.cvtColor(frame_bgr_or_qimg, cv2.COLOR_BGR2BGRA)
            self.current_qimage = QImage(
                bgra.data, w, h, w * 4, QImage.Format_RGB32
            ).copy()

        first_time = (self.video_width != w or self.video_height != h)
        self.video_width = w
        self.video_height = h

        if first_time:
            self.fit_to_view()
        else:
            self.update()

    def set_bgr_frame(self, frame_bgr):
        self.set_frame(frame_bgr)

    def fit_to_view(self, *args):
        if self.video_width <= 0 or self.video_height <= 0:
            self.zoom_factor = 1.0
            self.pan_offset = QPointF(0, 0)
            self.update()
            self.zoom_changed.emit(self.zoom_factor)
            return

        canvas_w = max(10, self.width())
        canvas_h = max(10, self.height())

        scale_w = canvas_w / float(self.video_width)
        scale_h = canvas_h / float(self.video_height)
        self.zoom_factor = min(scale_w, scale_h) * 0.96
        self.pan_offset = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit(self.zoom_factor)

    def zoom_in(self, *args):
        self.apply_zoom_at(self.zoom_factor * 1.25, QPointF(self.width() / 2, self.height() / 2))

    def zoom_out(self, *args):
        self.apply_zoom_at(self.zoom_factor / 1.25, QPointF(self.width() / 2, self.height() / 2))

    def reset_zoom_100(self, *args):
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit(self.zoom_factor)

    def apply_zoom_at(self, target_zoom, center_pt):
        target_zoom = max(0.01, min(50.0, target_zoom))
        if abs(target_zoom - self.zoom_factor) < 1e-4:
            return

        old_zoom = max(1e-4, self.zoom_factor)
        old_origin = self._get_origin(old_zoom, self.pan_offset)

        video_x = (center_pt.x() - old_origin.x()) / old_zoom
        video_y = (center_pt.y() - old_origin.y()) / old_zoom

        new_origin_x = center_pt.x() - video_x * target_zoom
        new_origin_y = center_pt.y() - video_y * target_zoom

        base_origin_x = (self.width() - self.video_width * target_zoom) / 2.0
        base_origin_y = (self.height() - self.video_height * target_zoom) / 2.0

        self.pan_offset = QPointF(new_origin_x - base_origin_x, new_origin_y - base_origin_y)
        self.zoom_factor = target_zoom
        self.update()
        self.zoom_changed.emit(self.zoom_factor)

    def _get_origin(self, zoom, pan):
        ox = (self.width() - self.video_width * zoom) / 2.0 + pan.x()
        oy = (self.height() - self.video_height * zoom) / 2.0 + pan.y()
        return QPointF(ox, oy)

    def screen_to_video(self, pt: QPointF) -> QPointF:
        zf = max(1e-4, self.zoom_factor)
        origin = self._get_origin(zf, self.pan_offset)
        vx = (pt.x() - origin.x()) / zf
        vy = (pt.y() - origin.y()) / zf
        return QPointF(vx, vy)

    def video_to_screen(self, pt: QPointF) -> QPointF:
        origin = self._get_origin(self.zoom_factor, self.pan_offset)
        sx = origin.x() + pt.x() * self.zoom_factor
        sy = origin.y() + pt.y() * self.zoom_factor
        return QPointF(sx, sy)

    def clear_all_drawings(self, *args):
        if not self.strokes and not self.overlays:
            return
        saved_strokes = list(self.strokes)
        saved_overlays = list(self.overlays)
        self.undo_stack.append(('clear_all', (saved_strokes, saved_overlays)))
        self.strokes.clear()
        for ov in self.overlays:
            ov.close()
        self.overlays.clear()
        self.selected_overlays.clear()
        if self.recorder:
            self.recorder.record_clear()
            for ov in saved_overlays:
                self.recorder.record_overlay_remove(ov.obj_id)
        p = self.window()
        if hasattr(p, '_refresh_overlay_tracks'):
            p._refresh_overlay_tracks()
        self.update()
        self.drawing_changed.emit()

    def undo_last_action(self, *args):
        p = self.window()
        if not self.undo_stack:
            if self.strokes:
                self.strokes.pop()
                if self.recorder:
                    self.recorder.record_undo()
                self.update()
                self.drawing_changed.emit()
            elif self.overlays:
                ov = self.overlays.pop()
                ov.close()
                if ov in self.selected_overlays:
                    self.selected_overlays.remove(ov)
                if hasattr(p, '_refresh_overlay_tracks'):
                    p._refresh_overlay_tracks()
                if self.recorder and self.recorder.is_active():
                    self.recorder.record_overlay_remove(ov.obj_id)
                self.update()
                self.drawing_changed.emit()
            return

        action_type, payload = self.undo_stack.pop()
        if action_type == 'add':
            if payload in self.strokes:
                self.strokes.remove(payload)
        elif action_type == 'add_overlay':
            ov = payload
            if ov in self.overlays:
                self.overlays.remove(ov)
                ov.close()
            if ov in self.selected_overlays:
                self.selected_overlays.remove(ov)
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
            if self.recorder and self.recorder.is_active():
                self.recorder.record_overlay_remove(ov.obj_id)
        elif action_type == 'delete_overlay':
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if isinstance(item, tuple):
                    ov, idx = item
                else:
                    ov, idx = item, len(self.overlays)
                if hasattr(ov, 'reopen'):
                    ov.reopen()
                target_idx = min(idx, len(self.overlays))
                if ov not in self.overlays:
                    self.overlays.insert(target_idx, ov)
            self.selected_overlays = [item[0] if isinstance(item, tuple) else item for item in items]
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
            if self.recorder and self.recorder.is_active():
                for item in items:
                    ov = item[0] if isinstance(item, tuple) else item
                    self.recorder.record_overlay_add(ov)
        elif action_type == 'transform_overlay':
            for ov, old_rect, new_rect in payload:
                ov.rect = QRectF(old_rect)
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
            if self.recorder and self.recorder.is_active():
                for ov, _, _ in payload:
                    self.recorder.record_overlay_transform(ov)
        elif action_type == 'reorder_overlays':
            self.overlays = list(payload)
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
        elif action_type == 'erase':
            stroke, original_idx = payload
            idx = min(original_idx, len(self.strokes))
            self.strokes.insert(idx, stroke)
        elif action_type in ('modify_strokes', 'modify'):
            if action_type == 'modify_strokes':
                self.strokes = [s.copy() for s in payload]
            else:
                # payload: list of tuples (orig_stroke, replaced_strokes, orig_idx)
                for orig_stroke, replaced_strokes, orig_idx in reversed(payload):
                    for r in replaced_strokes:
                        if r in self.strokes:
                            self.strokes.remove(r)
                    idx = min(orig_idx, len(self.strokes))
                    self.strokes.insert(idx, orig_stroke)
        elif action_type in ('clear', 'clear_all'):
            if action_type == 'clear_all' or (isinstance(payload, (tuple, list)) and len(payload) == 2 and isinstance(payload[0], list) and isinstance(payload[1], list)):
                saved_strokes, saved_overlays = payload
                self.strokes = list(saved_strokes)
                self.overlays = list(saved_overlays)
                for ov in self.overlays:
                    if hasattr(ov, 'reopen'):
                        ov.reopen()
                if hasattr(p, '_refresh_overlay_tracks'):
                    p._refresh_overlay_tracks()
                if self.recorder and self.recorder.is_active():
                    for ov in self.overlays:
                        self.recorder.record_overlay_add(ov)
            else:
                self.strokes = list(payload)

        if self.recorder:
            self.recorder.record_undo()

        self.update()
        self.drawing_changed.emit()

    def erase_strokes_at_video_pt(self, video_pt: QPointF, record_undo: bool = None):
        """
        Partial eraser: erases strokes like in MS Paint or Photoshop.
        Cuts out the circle of radius `eraser_radius` from intersecting strokes,
        splitting them into sub-segments rather than blindly deleting whole lines.
        """
        if record_undo is None:
            record_undo = not getattr(self, '_is_erasing', False)

        initial_strokes = [s.copy() for s in self.strokes] if record_undo else None

        erased_any = False
        radius_video = self.eraser_radius / max(0.01, self.zoom_factor)
        r2 = radius_video * radius_video

        # Find strokes that are intersected by the eraser circle
        to_modify = []
        for i, stroke in enumerate(self.strokes):
            pts = stroke.points
            if not pts:
                continue
            eff_r = radius_video + (stroke.width * 0.5)
            eff_r2 = eff_r * eff_r
            if len(pts) == 1:
                dx = pts[0].x() - video_pt.x()
                dy = pts[0].y() - video_pt.y()
                if (dx * dx + dy * dy) <= eff_r2:
                    to_modify.append((i, stroke))
                continue

            hit = False
            for j in range(len(pts) - 1):
                if dist_to_segment_sq(video_pt, pts[j], pts[j + 1]) <= eff_r2:
                    hit = True
                    break
            if hit:
                to_modify.append((i, stroke))

        if not to_modify:
            return

        undo_batch = []
        erased_ids = []
        # Process from highest index down so insertions/removals keep indices clean
        for idx, old_stroke in reversed(to_modify):
            sub_point_lists = erase_stroke_subsegments(
                old_stroke.points, video_pt, radius_video, old_stroke.width
            )
            created_replacements = []
            for s_pts in sub_point_lists:
                if len(s_pts) == 1:
                    new_sid = self.recorder.allocate_stroke_id() if self.recorder else len(self.strokes) + 1
                    new_s = Stroke(old_stroke.color, old_stroke.width, s_pts, stroke_id=new_sid)
                    created_replacements.append(new_s)
                elif len(s_pts) >= 2:
                    new_sid = self.recorder.allocate_stroke_id() if self.recorder else len(self.strokes) + 1
                    new_s = Stroke(old_stroke.color, old_stroke.width, s_pts, stroke_id=new_sid)
                    created_replacements.append(new_s)

            # Replace old_stroke at idx with created_replacements
            self.strokes.pop(idx)
            for offset, r_stroke in enumerate(created_replacements):
                self.strokes.insert(idx + offset, r_stroke)

            undo_batch.append((old_stroke, created_replacements, idx))
            if hasattr(old_stroke, 'stroke_id') and old_stroke.stroke_id is not None:
                erased_ids.append(old_stroke.stroke_id)
            erased_any = True

        if record_undo and erased_any:
            self.undo_stack.append(('modify_strokes', initial_strokes))
            self.drawing_changed.emit()

        if self.recorder and erased_ids:
            self.recorder.record_erase(erased_ids)
            # Record newly created carved stroke pieces so video export matches live canvas
            for old_stroke, created_replacements, _ in undo_batch:
                for r_stroke in created_replacements:
                    if r_stroke.points:
                        self.recorder.record_stroke_start(
                            r_stroke.stroke_id, r_stroke.color.name(), r_stroke.width,
                            (r_stroke.points[0].x(), r_stroke.points[0].y())
                        )
                        for pt in r_stroke.points[1:]:
                            self.recorder.record_stroke_point(r_stroke.stroke_id, (pt.x(), pt.y()))
                        self.recorder.record_stroke_end(r_stroke.stroke_id)

        if erased_any:
            self.update()
            self.drawing_changed.emit()

    def _hit_test_overlay_handle(self, ov, vpt: QPointF):
        r = ov.rect
        zf = max(0.001, self.zoom_factor)
        hs = 14.0 / zf

        btn_r = 10.0 / zf
        btn_center = QPointF(r.right() + 10.0 / zf, r.top() - 10.0 / zf)
        d_del = (vpt.x() - btn_center.x()) ** 2 + (vpt.y() - btn_center.y()) ** 2
        if d_del <= (btn_r * 1.4) ** 2:
            return 'del', btn_center

        hr_tl = QRectF(r.left() - hs, r.top() - hs, hs * 2, hs * 2)
        hr_tr = QRectF(r.right() - hs, r.top() - hs, hs * 2, hs * 2)
        hr_bl = QRectF(r.left() - hs, r.bottom() - hs, hs * 2, hs * 2)
        hr_br = QRectF(r.right() - hs, r.bottom() - hs, hs * 2, hs * 2)

        if hr_br.contains(vpt):
            return 'br', None
        if hr_tr.contains(vpt):
            return 'tr', None
        if hr_tl.contains(vpt):
            return 'tl', None
        if hr_bl.contains(vpt):
            return 'bl', None

        if r.contains(vpt):
            return 'move', None

        return None, None

    def _show_overlay_context_menu(self, ov, global_pos, vpt=None):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #242430;
                color: #E2E2EC;
                border: 1px solid #3E3E50;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #007AFF;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #383848;
                margin: 4px 6px;
            }
        """)

        act_vid_settings = None
        act_vid_play = None
        act_vid_loop = None
        act_vid_rewind = None
        act_vid_fwd = None
        act_vid_restart = None
        if ov.obj_type == OverlayObject.TYPE_VIDEO:
            act_vid_settings = menu.addAction("⚙️ Video Playback Settings...")
            act_vid_play = menu.addAction("❚❚ Pause Video" if getattr(ov, 'is_playing', True) else "▶ Play Video")
            act_vid_rewind = menu.addAction("⏪ Rewind 1s")
            act_vid_fwd = menu.addAction("⏩ Forward 1s")
            act_vid_restart = menu.addAction("⏮ Restart from 0s")
            act_vid_loop = menu.addAction("Disable Loop" if getattr(ov, 'loop', True) else "Enable Loop")
            menu.addSeparator()

        act_aspect = menu.addAction("🔓 Unlock Aspect Ratio" if getattr(ov, 'keep_aspect_ratio', True) else "🔒 Lock Aspect Ratio")
        menu.addSeparator()
        act_front = menu.addAction("Bring to Front")
        act_back = menu.addAction("Send to Back")
        act_forward = menu.addAction("Bring Forward")
        act_backward = menu.addAction("Send Backward")
        menu.addSeparator()
        act_dup = menu.addAction("Duplicate")
        menu.addSeparator()
        act_del = menu.addAction(f"Delete ({len(self.selected_overlays)})" if len(self.selected_overlays) > 1 else "Delete")
        menu.addSeparator()
        act_add_media = menu.addAction("➕ Add Image / GIF / PIP Video...")
        act_add_text = menu.addAction("🔤 Add Text Overlay...")

        player = self.window()
        chosen = menu.exec_(global_pos)
        if chosen == act_vid_settings:
            dlg = VideoOverlaySettingsDialog(ov, parent=self)
            dlg.exec_()
            self.update()
        elif chosen == act_vid_play:
            ov.is_playing = not getattr(ov, 'is_playing', True)
            self.update()
        elif chosen == act_vid_rewind:
            ov.step_frames(-int(round(getattr(ov, 'video_fps', 25.0))))
            self.update()
        elif chosen == act_vid_fwd:
            ov.step_frames(int(round(getattr(ov, 'video_fps', 25.0))))
            self.update()
        elif chosen == act_vid_restart:
            ov.seek_to_frame(0)
            self.update()
        elif chosen == act_vid_loop:
            ov.loop = not getattr(ov, 'loop', True)
            self.update()
        elif chosen == act_front:
            if hasattr(player, 'bring_overlay_to_front'):
                player.bring_overlay_to_front(ov)
        elif chosen == act_back:
            if hasattr(player, 'send_overlay_to_back'):
                player.send_overlay_to_back(ov)
        elif chosen == act_forward:
            if hasattr(player, 'bring_overlay_forward'):
                player.bring_overlay_forward(ov)
        elif chosen == act_backward:
            if hasattr(player, 'send_overlay_backward'):
                player.send_overlay_backward(ov)
        elif chosen == act_dup:
            if hasattr(player, 'duplicate_overlay'):
                player.duplicate_overlay(ov)
        elif chosen == act_aspect:
            ov.keep_aspect_ratio = not getattr(ov, 'keep_aspect_ratio', True)
            self.update()
        elif chosen == act_del:
            if self.selected_overlays:
                self.remove_selected_overlays()
            else:
                self.remove_overlay(ov)
        elif chosen == act_add_media:
            if hasattr(player, 'add_overlay_dialog'):
                player.add_overlay_dialog(pos=vpt)
        elif chosen == act_add_text:
            if hasattr(player, 'add_text_overlay'):
                player.add_text_overlay(pos=vpt)

    def _show_canvas_context_menu(self, vpt, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #242430;
                color: #E2E2EC;
                border: 1px solid #3E3E50;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #007AFF;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #383848;
                margin: 4px 6px;
            }
        """)

        act_add_media = menu.addAction("➕ Add Image / GIF / PIP Video...")
        act_add_text = menu.addAction("🔤 Add Text Overlay...")
        menu.addSeparator()
        act_undo = menu.addAction("↩ Undo (Ctrl+Z)")
        act_clear = menu.addAction("🗑 Clear Drawings (Del)")
        menu.addSeparator()
        act_fit = menu.addAction("📐 Fit to View")
        act_100 = menu.addAction("🔍 Reset Zoom 1:1")

        player = self.window()
        chosen = menu.exec_(global_pos)
        if chosen == act_add_media:
            if hasattr(player, 'add_overlay_dialog'):
                player.add_overlay_dialog(pos=vpt)
        elif chosen == act_add_text:
            if hasattr(player, 'add_text_overlay'):
                player.add_text_overlay(pos=vpt)
        elif chosen == act_undo:
            self.undo_last_action()
        elif chosen == act_clear:
            self.clear_all_drawings()
        elif chosen == act_fit:
            self.fit_to_view()
        elif chosen == act_100:
            self.reset_zoom_100()

    def contextMenuEvent(self, event):
        vpt = self.screen_to_video(QPointF(event.pos()))
        clicked_ov = None
        for ov in reversed(self.overlays):
            if ov.rect.contains(vpt):
                clicked_ov = ov
                break
        if clicked_ov:
            if clicked_ov not in self.selected_overlays:
                self.selected_overlays = [clicked_ov]
            self.active_tool = self.TOOL_SELECT
            self.update_cursor()
            self.update()
            self._show_overlay_context_menu(clicked_ov, event.globalPos(), vpt)
        else:
            self._show_canvas_context_menu(vpt, event.globalPos())
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return

        player = self.window()
        if not hasattr(player, 'load_video'):
            return

        has_active_canvas = (self.video_width > 0 and self.video_height > 0)
        if not has_active_canvas:
            first_path = urls[0].toLocalFile()
            if os.path.exists(first_path):
                player.load_video(first_path)
                event.acceptProposedAction()
                return

        drop_vpt = self.screen_to_video(QPointF(event.pos()))
        added_any = False

        for i, u in enumerate(urls):
            fpath = u.toLocalFile()
            if not fpath or not os.path.exists(fpath):
                continue
            ext = os.path.splitext(fpath)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif',
                       '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v'):
                pos = QPointF(drop_vpt.x() + i * 25, drop_vpt.y() + i * 25)
                player.add_overlay(fpath, pos=pos)
                added_any = True

        if added_any:
            event.acceptProposedAction()
            self.update()

    def mouseDoubleClickEvent(self, event):
        if self.active_tool == self.TOOL_SELECT and self.selected_overlay:
            if self.selected_overlay.obj_type == OverlayObject.TYPE_TEXT:
                ov = self.selected_overlay
                dlg = TextOverlayDialog(
                    initial_text=ov.text,
                    initial_font=QFont(ov.font_family, ov.font_size),
                    initial_color=ov.text_color,
                    initial_bg=ov.bg_color,
                    parent=self
                )
                if dlg.exec_() == 1:
                    d = dlg.get_data()
                    ov.text = d['text']
                    ov.font_family = d['font_family']
                    ov.font_size = d['font_size']
                    ov.font_bold = d['bold']
                    ov.font_italic = d['italic']
                    ov.text_color = d['color']
                    ov.bg_color = d['bg_color']
                    if self.recorder and self.recorder.is_active():
                        self.recorder.record_overlay_transform(ov)
                    self.update()
                return
            elif self.selected_overlay.obj_type == OverlayObject.TYPE_VIDEO:
                dlg = VideoOverlaySettingsDialog(self.selected_overlay, parent=self)
                dlg.exec_()
                self.update()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        pos = QPointF(event.pos())
        self.last_mouse_pos = pos
        self._press_pos = event.pos()

        if event.button() == Qt.RightButton:
            self.is_panning = False
            return

        if event.button() == Qt.MidButton:
            self.is_panning = True
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton:
            if self.active_tool == self.TOOL_PAN:
                self.is_panning = True
                self.setCursor(Qt.ClosedHandCursor)
            elif self.active_tool == self.TOOL_TEXT:
                vpt = self.screen_to_video(pos)
                player = self.window()
                if hasattr(player, 'add_text_overlay'):
                    player.add_text_overlay(pos=vpt)
                return
            elif self.active_tool == self.TOOL_PEN:
                vpt = self.screen_to_video(pos)
                stroke_width_video = max(0.5, self.pen_width / max(0.01, self.zoom_factor))
                sid = self.recorder.allocate_stroke_id() if self.recorder else len(self.strokes) + 1
                self.current_stroke = Stroke(self.pen_color, stroke_width_video, [vpt], stroke_id=sid)
                self.strokes.append(self.current_stroke)
                self.undo_stack.append(('add', self.current_stroke))
                if self.recorder:
                    self.recorder.record_stroke_start(sid, self.pen_color.name(), stroke_width_video, (vpt.x(), vpt.y()))
                self.update()
                self.drawing_changed.emit()
            elif self.active_tool == self.TOOL_ERASER:
                vpt = self.screen_to_video(pos)
                self._is_erasing = True
                self._eraser_initial_strokes = [s.copy() for s in self.strokes]
                self._last_eraser_vpt = vpt
                self.erase_strokes_at_video_pt(vpt, record_undo=False)
                self.update()
            elif self.active_tool == self.TOOL_SELECT:
                vpt = self.screen_to_video(pos)
                ctrl_or_shift = bool(event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))

                # Check handles on currently selected overlays
                for ov in self.selected_overlays:
                    handle, _ = self._hit_test_overlay_handle(ov, vpt)
                    if handle == 'del':
                        self.remove_overlay(ov)
                        return
                    elif handle in ('tl', 'tr', 'bl', 'br'):
                        self.selected_overlays = [ov]
                        self._overlay_drag_mode = handle
                        self._drag_start_vpt = vpt
                        self._drag_start_rect = QRectF(ov.rect)
                        self._drag_start_rects = {ov: QRectF(ov.rect)}
                        return

                # Check overlay body hit
                hit_ov = None
                for ov in reversed(self.overlays):
                    if ov.rect.contains(vpt):
                        hit_ov = ov
                        break

                if hit_ov:
                    if ctrl_or_shift:
                        if hit_ov in self.selected_overlays:
                            self.selected_overlays.remove(hit_ov)
                        else:
                            self.selected_overlays.append(hit_ov)
                    else:
                        if hit_ov not in self.selected_overlays:
                            self.selected_overlays = [hit_ov]
                    self._overlay_drag_mode = 'move'
                    self._drag_start_vpt = vpt
                    self._drag_start_rects = {o: QRectF(o.rect) for o in self.selected_overlays}
                    if self.selected_overlay:
                        self._drag_start_rect = QRectF(self.selected_overlay.rect)
                else:
                    if not ctrl_or_shift:
                        self.selected_overlays = []
                    self._is_rubber_banding = True
                    self._rubber_band_start = vpt
                    self._rubber_band_rect = QRectF(vpt, vpt)
                self.update()

    def mouseMoveEvent(self, event):
        pos = QPointF(event.pos())
        delta = pos - self.last_mouse_pos
        self.last_mouse_pos = pos

        if (event.buttons() & (Qt.RightButton | Qt.MidButton)) and self._press_pos is not None:
            if (event.pos() - self._press_pos).manhattanLength() > 4:
                self.is_panning = True
                self.setCursor(Qt.ClosedHandCursor)

        if self.is_panning:
            self.pan_offset += delta
            self.update()
            return

        if self._is_rubber_banding:
            vpt = self.screen_to_video(pos)
            self._rubber_band_rect = QRectF(self._rubber_band_start, vpt).normalized()
            self.update()
            return

        if event.buttons() & Qt.LeftButton:
            if self.active_tool == self.TOOL_PEN and self.current_stroke:
                vpt = self.screen_to_video(pos)
                self.current_stroke.add_point(vpt)
                if self.recorder:
                    self.recorder.record_stroke_point(self.current_stroke.stroke_id, (vpt.x(), vpt.y()))
                self.update()
            elif self.active_tool == self.TOOL_ERASER:
                vpt = self.screen_to_video(pos)
                if getattr(self, '_is_erasing', False) and getattr(self, '_last_eraser_vpt', None):
                    dx = vpt.x() - self._last_eraser_vpt.x()
                    dy = vpt.y() - self._last_eraser_vpt.y()
                    dist = math.hypot(dx, dy)
                    radius_video = self.eraser_radius / max(0.01, self.zoom_factor)
                    step = max(2.0, radius_video * 0.35)
                    n_steps = max(1, int(dist / step))
                    for i in range(1, n_steps + 1):
                        t = i / float(n_steps)
                        pt = QPointF(
                            self._last_eraser_vpt.x() + t * dx,
                            self._last_eraser_vpt.y() + t * dy
                        )
                        self.erase_strokes_at_video_pt(pt, record_undo=False)
                else:
                    self.erase_strokes_at_video_pt(vpt, record_undo=False)
                self._last_eraser_vpt = vpt
                self.update()
            elif self.active_tool == self.TOOL_SELECT and self.selected_overlays and self._overlay_drag_mode:
                vpt = self.screen_to_video(pos)
                dx = vpt.x() - self._drag_start_vpt.x()
                dy = vpt.y() - self._drag_start_vpt.y()
                sr = self._drag_start_rect
                min_s = 20.0
                shift_held = bool(event.modifiers() & Qt.ShiftModifier)

                mode = self._overlay_drag_mode
                if mode == 'move':
                    for o, orig_r in self._drag_start_rects.items():
                        o.rect.moveTo(orig_r.x() + dx, orig_r.y() + dy)
                elif self.selected_overlay and sr:
                    ov = self.selected_overlay
                    preserve_aspect = getattr(ov, 'keep_aspect_ratio', True) or shift_held
                    ratio = getattr(ov, 'orig_aspect_ratio', None) or (sr.width() / max(1.0, sr.height()))
                    if mode == 'br':
                        new_w = max(min_s, sr.width() + dx)
                        new_h = (new_w / ratio) if preserve_aspect else max(min_s, sr.height() + dy)
                        ov.rect = QRectF(sr.left(), sr.top(), new_w, new_h)
                    elif mode == 'tr':
                        new_w = max(min_s, sr.width() + dx)
                        new_h = (new_w / ratio) if preserve_aspect else max(min_s, sr.height() - dy)
                        new_top = sr.bottom() - new_h
                        ov.rect = QRectF(sr.left(), new_top, new_w, new_h)
                    elif mode == 'bl':
                        new_w = max(min_s, sr.width() - dx)
                        new_h = (new_w / ratio) if preserve_aspect else max(min_s, sr.height() + dy)
                        new_left = sr.right() - new_w
                        ov.rect = QRectF(new_left, sr.top(), new_w, new_h)
                    elif mode == 'tl':
                        new_w = max(min_s, sr.width() - dx)
                        new_h = (new_w / ratio) if preserve_aspect else max(min_s, sr.height() - dy)
                        new_left = sr.right() - new_w
                        new_top = sr.bottom() - new_h
                        ov.rect = QRectF(new_left, new_top, new_w, new_h)
                self.update()
        else:
            if self.active_tool == self.TOOL_SELECT and self.video_width > 0:
                vpt = self.screen_to_video(pos)
                hovered = None
                for ov in reversed(self.overlays):
                    if ov.rect.contains(vpt):
                        hovered = ov
                        break

                if hovered != self.hover_overlay:
                    self.hover_overlay = hovered
                    self.update()

                if self.selected_overlay:
                    h, _ = self._hit_test_overlay_handle(self.selected_overlay, vpt)
                    if h in ('tl', 'br'):
                        self.setCursor(Qt.SizeFDiagCursor)
                    elif h in ('tr', 'bl'):
                        self.setCursor(Qt.SizeBDiagCursor)
                    elif h == 'del':
                        self.setCursor(Qt.PointingHandCursor)
                    elif h == 'move':
                        self.setCursor(Qt.SizeAllCursor)
                    elif hovered is not None:
                        self.setCursor(Qt.PointingHandCursor)
                    else:
                        self.setCursor(Qt.ArrowCursor)
                elif hovered is not None:
                    self.setCursor(Qt.PointingHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        was_panning = self.is_panning
        if event.button() in (Qt.RightButton, Qt.MidButton) or (
            event.button() == Qt.LeftButton and self.is_panning
        ):
            self.is_panning = False
            self.update_cursor()

        if event.button() == Qt.RightButton:
            dragged = False
            if self._press_pos is not None:
                dragged = (event.pos() - self._press_pos).manhattanLength() > 4
            self._press_pos = None

            if not dragged and not was_panning and self.video_width > 0:
                vpt = self.screen_to_video(QPointF(event.pos()))
                clicked_ov = None
                for ov in reversed(self.overlays):
                    if ov.rect.contains(vpt):
                        clicked_ov = ov
                        break
                if clicked_ov:
                    if clicked_ov not in self.selected_overlays:
                        self.selected_overlays = [clicked_ov]
                    self.active_tool = self.TOOL_SELECT
                    self.update_cursor()
                    self.update()
                    self._show_overlay_context_menu(clicked_ov, event.globalPos(), vpt)
                    return
                else:
                    self._show_canvas_context_menu(vpt, event.globalPos())
                    return

        if event.button() == Qt.LeftButton:
            self._press_pos = None
            if self.current_stroke and self.recorder:
                self.recorder.record_stroke_end(self.current_stroke.stroke_id)
            self.current_stroke = None

            if self._is_rubber_banding:
                self._is_rubber_banding = False
                if self._rubber_band_rect:
                    rb = self._rubber_band_rect.normalized()
                    hit_list = [ov for ov in self.overlays if rb.intersects(ov.rect)]
                    ctrl_or_shift = bool(event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier))
                    if ctrl_or_shift:
                        for ov in hit_list:
                            if ov not in self.selected_overlays:
                                self.selected_overlays.append(ov)
                    else:
                        self.selected_overlays = hit_list
                self._rubber_band_rect = None
                self.update()
                return

            if self.active_tool == self.TOOL_SELECT and self.selected_overlays and self._overlay_drag_mode:
                if self.recorder:
                    for ov in self.selected_overlays:
                        self.recorder.record_overlay_transform(ov)
                if getattr(self, '_drag_start_rects', None):
                    transforms = []
                    for ov in self.selected_overlays:
                        old_r = self._drag_start_rects.get(ov)
                        if old_r and (abs(old_r.x() - ov.rect.x()) > 0.5 or
                                     abs(old_r.y() - ov.rect.y()) > 0.5 or
                                     abs(old_r.width() - ov.rect.width()) > 0.5 or
                                     abs(old_r.height() - ov.rect.height()) > 0.5):
                            transforms.append((ov, old_r, QRectF(ov.rect)))
                    if transforms:
                        self.undo_stack.append(('transform_overlay', transforms))
                        self.drawing_changed.emit()
                self._overlay_drag_mode = None
                self._drag_start_vpt = None
                self._drag_start_rect = None
                self._drag_start_rects = {}
                self.update()

            if getattr(self, '_is_erasing', False):
                self._is_erasing = False
                self._last_eraser_vpt = None
                init_strokes = getattr(self, '_eraser_initial_strokes', None)
                if init_strokes is not None:
                    changed = len(init_strokes) != len(self.strokes)
                    if not changed:
                        for s_old, s_new in zip(init_strokes, self.strokes):
                            if len(s_old.points) != len(s_new.points):
                                changed = True
                                break
                    if changed:
                        self.undo_stack.append(('modify_strokes', init_strokes))
                        self.drawing_changed.emit()
                self._eraser_initial_strokes = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.active_tool == self.TOOL_SELECT and self.selected_overlays:
                self.remove_selected_overlays()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        num_degrees = event.angleDelta().y() / 8.0
        num_steps = max(-10.0, min(10.0, num_degrees / 15.0))
        factor = 1.15 ** num_steps
        self.apply_zoom_at(self.zoom_factor * factor, QPointF(event.pos()))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            zf = max(1e-4, self.zoom_factor)
            if abs(zf - 1.0) > 0.01:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            painter.fillRect(self.rect(), QColor("#121216"))
            zf = max(1e-4, self.zoom_factor)
            origin = self._get_origin(zf, self.pan_offset)

            if self.current_qimage is not None and not self.current_qimage.isNull():
                painter.save()
                painter.translate(origin.x(), origin.y())
                painter.scale(zf, zf)

                painter.drawImage(0, 0, self.current_qimage)
                painter.setPen(QPen(QColor(60, 60, 75, 180), 1.0 / zf))
                painter.drawRect(0, 0, self.video_width, self.video_height)

                cur_time = self._get_current_time()
                for ov in self.overlays:
                    ov_img = ov.get_frame_at_time(cur_time)
                    if ov_img and not ov_img.isNull():
                        painter.save()
                        if hasattr(ov, 'opacity') and ov.opacity < 1.0:
                            painter.setOpacity(ov.opacity)
                        painter.drawImage(ov.rect, ov_img)
                        painter.restore()

                    if ov == self.hover_overlay and ov not in self.selected_overlays and self.active_tool == self.TOOL_SELECT:
                        painter.save()
                        hover_pen = QPen(QColor(0, 229, 255, 120), 1.5 / zf, Qt.DashLine)
                        painter.setPen(hover_pen)
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(ov.rect)
                        painter.restore()

                    if ov in self.selected_overlays and self.active_tool == self.TOOL_SELECT:
                        painter.save()
                        sel_pen = QPen(QColor("#00E5FF"), 1.8 / zf, Qt.DashLine)
                        painter.setPen(sel_pen)
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(ov.rect)

                        hs = 12.0 / zf
                        r = ov.rect
                        painter.setPen(QPen(QColor("#007AFF"), 1.0 / zf))
                        painter.setBrush(QBrush(QColor("#00E5FF")))
                        painter.drawRect(QRectF(r.left() - hs / 2, r.top() - hs / 2, hs, hs))
                        painter.drawRect(QRectF(r.right() - hs / 2, r.top() - hs / 2, hs, hs))
                        painter.drawRect(QRectF(r.left() - hs / 2, r.bottom() - hs / 2, hs, hs))
                        painter.drawRect(QRectF(r.right() - hs / 2, r.bottom() - hs / 2, hs, hs))

                        btn_r = 9.0 / zf
                        btn_center = QPointF(r.right() + 10.0 / zf, r.top() - 10.0 / zf)
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(QColor("#FF3B30")))
                        painter.drawEllipse(btn_center, btn_r, btn_r)
                        painter.setPen(QPen(QColor("#FFFFFF"), 1.5 / zf))
                        del_d = 4.0 / zf
                        painter.drawLine(QPointF(btn_center.x() - del_d, btn_center.y() - del_d),
                                         QPointF(btn_center.x() + del_d, btn_center.y() + del_d))
                        painter.drawLine(QPointF(btn_center.x() + del_d, btn_center.y() - del_d),
                                         QPointF(btn_center.x() - del_d, btn_center.y() + del_d))
                        painter.restore()

                if self.active_tool == self.TOOL_SELECT and self._is_rubber_banding and self._rubber_band_rect:
                    painter.save()
                    rb = self._rubber_band_rect.normalized()
                    painter.setBrush(QBrush(QColor(0, 122, 255, 45)))
                    painter.setPen(QPen(QColor(0, 229, 255, 220), 1.5 / zf, Qt.DashLine))
                    painter.drawRect(rb)
                    painter.restore()

                for stroke in self.strokes:
                    if not stroke.points:
                        continue
                    pen = QPen(stroke.color, stroke.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                    painter.setPen(pen)

                    if len(stroke.points) == 1:
                        painter.setBrush(QBrush(stroke.color))
                        r = stroke.width / 2.0
                        painter.drawEllipse(stroke.points[0], r, r)
                    else:
                        painter.setBrush(Qt.NoBrush)
                        painter.drawPath(stroke.path)

                painter.restore()
            else:
                painter.setPen(QColor("#7E7E94"))
                painter.setFont(QFont("Segoe UI", 13, QFont.Bold))
                painter.drawText(
                    self.rect(),
                    Qt.AlignCenter,
                    "Click 'Open' (O) or Drag & Drop a video file here\n(Drop images, GIFs, or videos to add overlays)"
                )
        except Exception as e:
            logger.error(f"Error in VideoCanvas.paintEvent: {e}")

