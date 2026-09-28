import sys
import os
import collections
import tempfile
import wave
import json
import time
import av
import cv2
import numpy as np
from PIL import Image, ImageSequence
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal, QPoint, QUrl, QThread
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QBrush, QCursor,
    QFont, QIcon, QKeySequence, QPainterPath
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QColorDialog,
    QComboBox, QSpinBox, QFrame, QShortcut, QMessageBox, QToolTip,
    QProgressDialog, QScrollArea, QMenu, QAction, QSizePolicy
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent


def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def dist_to_segment_sq(p: QPointF, a: QPointF, b: QPointF) -> float:
    ab_x = b.x() - a.x()
    ab_y = b.y() - a.y()
    l2 = ab_x * ab_x + ab_y * ab_y
    if l2 < 1e-6:
        dx = p.x() - a.x()
        dy = p.y() - a.y()
        return dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p.x() - a.x()) * ab_x + (p.y() - a.y()) * ab_y) / l2))
    proj_x = a.x() + t * ab_x
    proj_y = a.y() + t * ab_y
    dx = p.x() - proj_x
    dy = p.y() - proj_y
    return dx * dx + dy * dy


class ClickableSlider(QSlider):
    wheel_scrolled = pyqtSignal(int)

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.fps = 25.0
        self.is_dragging = False

    def pixel_to_value(self, px: int) -> int:
        w = max(1, self.width())
        ratio = max(0.0, min(1.0, px / float(w)))
        return int(round(self.minimum() + ratio * (self.maximum() - self.minimum())))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            val = self.pixel_to_value(event.x())
            self.setValue(val)
            self.sliderMoved.emit(val)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        val = self.pixel_to_value(event.x())
        if self.is_dragging and (event.buttons() & Qt.LeftButton):
            self.setValue(val)
            self.sliderMoved.emit(val)

        if self.fps > 0 and self.maximum() > 0:
            sec = val / float(self.fps)
            mins = int(sec // 60)
            secs = sec % 60
            tip_text = f"{mins:02d}:{secs:05.2f} (Frame {val + 1})"
            QToolTip.showText(event.globalPos(), tip_text, self)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = 1 if event.angleDelta().y() > 0 else -1
        if event.modifiers() & Qt.ShiftModifier:
            delta *= int(round(self.fps))
        elif event.modifiers() & Qt.ControlModifier:
            delta *= int(round(self.fps * 5))
        self.wheel_scrolled.emit(delta)
        event.accept()


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


class OverlayObject:
    TYPE_IMAGE = "image"
    TYPE_GIF = "gif"
    TYPE_VIDEO = "video"
    MAX_VIDEO_CACHE = 45

    def __init__(self, obj_id: int, file_path: str, rect: QRectF, start_time: float = 0.0):
        self.obj_id = obj_id
        self.file_path = file_path
        self.rect = QRectF(rect)
        self.start_time = float(start_time)
        self.obj_type = self._detect_type(file_path)

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

        self._load_media()

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
            except Exception:
                self.obj_type = self.TYPE_IMAGE
                self.static_image = QImage(self.file_path)
        elif self.obj_type == self.TYPE_VIDEO:
            self.video_cap = cv2.VideoCapture(self.file_path)
            if self.video_cap.isOpened():
                self.video_fps = float(self.video_cap.get(cv2.CAP_PROP_FPS))
                if self.video_fps <= 0 or np.isnan(self.video_fps):
                    self.video_fps = 25.0
                self.video_total_frames = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            else:
                self.video_cap = None

    def get_frame_at_time(self, elapsed_sec: float) -> QImage:
        rel_time = max(0.0, float(elapsed_sec) - self.start_time)
        if self.obj_type == self.TYPE_IMAGE:
            return self.static_image
        elif self.obj_type == self.TYPE_GIF:
            if not self.gif_frames:
                return self.static_image
            if self.gif_total_dur <= 0:
                return self.gif_frames[0]
            cycle_time = rel_time % self.gif_total_dur
            acc = 0.0
            for i, dur in enumerate(self.gif_durations):
                acc += dur
                if cycle_time <= acc:
                    return self.gif_frames[i]
            return self.gif_frames[-1]
        elif self.obj_type == self.TYPE_VIDEO:
            if self.video_cap is None or not self.video_cap.isOpened() or self.video_total_frames <= 0:
                return None
            target_f = int(rel_time * self.video_fps) % self.video_total_frames
            if target_f in self.video_cache:
                self.video_cache.move_to_end(target_f)
                return self.video_cache[target_f]

            if target_f == self.video_cached_idx and self.video_cached_frame is not None:
                return self.video_cached_frame

            if target_f == self.video_last_idx + 1:
                ret, frame = self.video_cap.read()
            else:
                self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)
                ret, frame = self.video_cap.read()

            if ret and frame is not None:
                self.video_last_idx = target_f
                h, w, ch = frame.shape
                qimg = QImage(frame.data, w, h, ch * w, QImage.Format_BGR888).copy()
                self.video_cached_frame = qimg
                self.video_cached_idx = target_f
                if len(self.video_cache) >= self.MAX_VIDEO_CACHE:
                    self.video_cache.popitem(last=False)
                self.video_cache[target_f] = qimg
                return qimg

            if self.video_cache:
                return next(reversed(self.video_cache.values()))
            return self.video_cached_frame
        return None

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
        self.selected_overlay = None
        self.hover_overlay = None
        self._overlay_drag_mode = None
        self._drag_start_vpt = None
        self._drag_start_rect = None

        self.update_cursor()

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

    def remove_overlay(self, ov):
        if ov in self.overlays:
            self.overlays.remove(ov)
            if self.recorder:
                self.recorder.record_overlay_remove(ov.obj_id)
            ov.close()
            if self.selected_overlay == ov:
                self.selected_overlay = None
            self.update()

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
            h, w, ch = frame_bgr_or_qimg.shape
            bytes_per_line = ch * w
            self.current_qimage = QImage(
                frame_bgr_or_qimg.data, w, h, bytes_per_line, QImage.Format_BGR888
            ).copy()

        first_time = (self.video_width != w or self.video_height != h)
        self.video_width = w
        self.video_height = h

        if first_time:
            self.fit_to_view()
        else:
            self.update()

    def fit_to_view(self):
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

    def zoom_in(self):
        self.apply_zoom_at(self.zoom_factor * 1.25, QPointF(self.width() / 2, self.height() / 2))

    def zoom_out(self):
        self.apply_zoom_at(self.zoom_factor / 1.25, QPointF(self.width() / 2, self.height() / 2))

    def reset_zoom_100(self):
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit(self.zoom_factor)

    def apply_zoom_at(self, target_zoom, center_pt):
        target_zoom = max(0.01, min(50.0, target_zoom))
        if abs(target_zoom - self.zoom_factor) < 1e-4:
            return

        old_zoom = self.zoom_factor
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
        origin = self._get_origin(self.zoom_factor, self.pan_offset)
        vx = (pt.x() - origin.x()) / self.zoom_factor
        vy = (pt.y() - origin.y()) / self.zoom_factor
        return QPointF(vx, vy)

    def video_to_screen(self, pt: QPointF) -> QPointF:
        origin = self._get_origin(self.zoom_factor, self.pan_offset)
        sx = origin.x() + pt.x() * self.zoom_factor
        sy = origin.y() + pt.y() * self.zoom_factor
        return QPointF(sx, sy)

    def clear_all_drawings(self):
        if not self.strokes:
            return
        self.undo_stack.append(('clear', list(self.strokes)))
        self.strokes.clear()
        if self.recorder:
            self.recorder.record_clear()
        self.update()
        self.drawing_changed.emit()

    def undo_last_action(self):
        if not self.undo_stack:
            if self.strokes:
                self.strokes.pop()
                if self.recorder:
                    self.recorder.record_undo()
                self.update()
                self.drawing_changed.emit()
            return

        action_type, payload = self.undo_stack.pop()
        if action_type == 'add':
            if payload in self.strokes:
                self.strokes.remove(payload)
        elif action_type == 'erase':
            stroke, original_idx = payload
            idx = min(original_idx, len(self.strokes))
            self.strokes.insert(idx, stroke)
        elif action_type == 'clear':
            self.strokes = list(payload)

        if self.recorder:
            self.recorder.record_undo()

        self.update()
        self.drawing_changed.emit()

    def erase_strokes_at_video_pt(self, video_pt: QPointF):
        erased_any = False
        radius_video = self.eraser_radius / max(0.01, self.zoom_factor)
        r2 = radius_video * radius_video

        indices_to_remove = []
        for i, stroke in enumerate(self.strokes):
            pts = stroke.points
            if not pts:
                continue
            if len(pts) == 1:
                dx = pts[0].x() - video_pt.x()
                dy = pts[0].y() - video_pt.y()
                if (dx * dx + dy * dy) <= r2:
                    indices_to_remove.append(i)
                continue

            hit = False
            for j in range(len(pts) - 1):
                if dist_to_segment_sq(video_pt, pts[j], pts[j + 1]) <= r2:
                    hit = True
                    break
            if hit:
                indices_to_remove.append(i)

        erased_ids = []
        for idx in reversed(indices_to_remove):
            removed = self.strokes.pop(idx)
            self.undo_stack.append(('erase', (removed, idx)))
            if hasattr(removed, 'stroke_id') and removed.stroke_id is not None:
                erased_ids.append(removed.stroke_id)
            erased_any = True

        if self.recorder and erased_ids:
            self.recorder.record_erase(erased_ids)

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

    def _show_overlay_context_menu(self, ov, global_pos):
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

        act_front = menu.addAction("Bring to Front")
        act_back = menu.addAction("Send to Back")
        act_forward = menu.addAction("Bring Forward")
        act_backward = menu.addAction("Send Backward")
        menu.addSeparator()
        act_dup = menu.addAction("Duplicate")
        menu.addSeparator()
        act_del = menu.addAction("Delete")

        player = self.window()
        chosen = menu.exec_(global_pos)
        if chosen == act_front:
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
        elif chosen == act_del:
            self.remove_overlay(ov)

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

        if player.cap is None or self.video_width <= 0:
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
                self.erase_strokes_at_video_pt(vpt)
            elif self.active_tool == self.TOOL_SELECT:
                vpt = self.screen_to_video(pos)
                if self.selected_overlay:
                    handle, _ = self._hit_test_overlay_handle(self.selected_overlay, vpt)
                    if handle == 'del':
                        self.remove_overlay(self.selected_overlay)
                        return
                    elif handle in ('tl', 'tr', 'bl', 'br', 'move'):
                        self._overlay_drag_mode = handle
                        self._drag_start_vpt = vpt
                        self._drag_start_rect = QRectF(self.selected_overlay.rect)
                        return

                hit_ov = None
                for ov in reversed(self.overlays):
                    if ov.rect.contains(vpt):
                        hit_ov = ov
                        break

                self.selected_overlay = hit_ov
                if hit_ov:
                    self._overlay_drag_mode = 'move'
                    self._drag_start_vpt = vpt
                    self._drag_start_rect = QRectF(hit_ov.rect)
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

        if event.buttons() & Qt.LeftButton:
            if self.active_tool == self.TOOL_PEN and self.current_stroke:
                vpt = self.screen_to_video(pos)
                self.current_stroke.add_point(vpt)
                if self.recorder:
                    self.recorder.record_stroke_point(self.current_stroke.stroke_id, (vpt.x(), vpt.y()))
                self.update()
            elif self.active_tool == self.TOOL_ERASER:
                vpt = self.screen_to_video(pos)
                self.erase_strokes_at_video_pt(vpt)
            elif self.active_tool == self.TOOL_SELECT and self.selected_overlay and self._overlay_drag_mode:
                vpt = self.screen_to_video(pos)
                dx = vpt.x() - self._drag_start_vpt.x()
                dy = vpt.y() - self._drag_start_vpt.y()
                sr = self._drag_start_rect
                min_s = 20.0
                shift_held = bool(event.modifiers() & Qt.ShiftModifier)
                ratio = sr.width() / max(1.0, sr.height())

                mode = self._overlay_drag_mode
                if mode == 'move':
                    self.selected_overlay.rect.moveTo(sr.x() + dx, sr.y() + dy)
                elif mode == 'br':
                    new_w = max(min_s, sr.width() + dx)
                    new_h = max(min_s, sr.height() + dy)
                    if shift_held:
                        new_h = new_w / ratio
                    self.selected_overlay.rect = QRectF(sr.left(), sr.top(), new_w, new_h)
                elif mode == 'tr':
                    new_w = max(min_s, sr.width() + dx)
                    new_h = max(min_s, sr.height() - dy)
                    if shift_held:
                        new_h = new_w / ratio
                    new_top = sr.bottom() - new_h
                    self.selected_overlay.rect = QRectF(sr.left(), new_top, new_w, new_h)
                elif mode == 'bl':
                    new_w = max(min_s, sr.width() - dx)
                    new_h = max(min_s, sr.height() + dy)
                    if shift_held:
                        new_h = new_w / ratio
                    new_left = sr.right() - new_w
                    self.selected_overlay.rect = QRectF(new_left, sr.top(), new_w, new_h)
                elif mode == 'tl':
                    new_w = max(min_s, sr.width() - dx)
                    new_h = max(min_s, sr.height() - dy)
                    if shift_held:
                        new_h = new_w / ratio
                    new_left = sr.right() - new_w
                    new_top = sr.bottom() - new_h
                    self.selected_overlay.rect = QRectF(new_left, new_top, new_w, new_h)
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
                    self.selected_overlay = clicked_ov
                    self.active_tool = self.TOOL_SELECT
                    self.update_cursor()
                    self.update()
                    self._show_overlay_context_menu(clicked_ov, event.globalPos())
                    return

        if event.button() == Qt.LeftButton:
            self._press_pos = None
            if self.current_stroke and self.recorder:
                self.recorder.record_stroke_end(self.current_stroke.stroke_id)
            self.current_stroke = None

            if self.active_tool == self.TOOL_SELECT and self.selected_overlay and self._overlay_drag_mode:
                if self.recorder:
                    self.recorder.record_overlay_transform(self.selected_overlay)
                self._overlay_drag_mode = None
                self._drag_start_vpt = None
                self._drag_start_rect = None

    def wheelEvent(self, event):
        num_degrees = event.angleDelta().y() / 8.0
        num_steps = num_degrees / 15.0
        factor = 1.15 ** num_steps
        self.apply_zoom_at(self.zoom_factor * factor, QPointF(event.pos()))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), QColor("#121216"))
        origin = self._get_origin(self.zoom_factor, self.pan_offset)

        if self.current_qimage is not None and not self.current_qimage.isNull():
            painter.save()
            painter.translate(origin.x(), origin.y())
            painter.scale(self.zoom_factor, self.zoom_factor)

            painter.drawImage(0, 0, self.current_qimage)
            painter.setPen(QPen(QColor(60, 60, 75, 180), 1.0 / self.zoom_factor))
            painter.drawRect(0, 0, self.video_width, self.video_height)

            cur_time = self._get_current_time()
            for ov in self.overlays:
                ov_img = ov.get_frame_at_time(cur_time)
                if ov_img and not ov_img.isNull():
                    painter.drawImage(ov.rect, ov_img)

                if ov == self.hover_overlay and ov != self.selected_overlay and self.active_tool == self.TOOL_SELECT:
                    painter.save()
                    hover_pen = QPen(QColor(0, 229, 255, 120), 1.5 / self.zoom_factor, Qt.DashLine)
                    painter.setPen(hover_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(ov.rect)
                    painter.restore()

                if ov == self.selected_overlay and self.active_tool == self.TOOL_SELECT:
                    painter.save()
                    sel_pen = QPen(QColor("#00E5FF"), 1.8 / self.zoom_factor, Qt.DashLine)
                    painter.setPen(sel_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(ov.rect)

                    hs = 12.0 / self.zoom_factor
                    r = ov.rect
                    painter.setPen(QPen(QColor("#007AFF"), 1.0 / self.zoom_factor))
                    painter.setBrush(QBrush(QColor("#00E5FF")))
                    painter.drawRect(QRectF(r.left() - hs / 2, r.top() - hs / 2, hs, hs))
                    painter.drawRect(QRectF(r.right() - hs / 2, r.top() - hs / 2, hs, hs))
                    painter.drawRect(QRectF(r.left() - hs / 2, r.bottom() - hs / 2, hs, hs))
                    painter.drawRect(QRectF(r.right() - hs / 2, r.bottom() - hs / 2, hs, hs))

                    btn_r = 9.0 / self.zoom_factor
                    btn_center = QPointF(r.right() + 10.0 / self.zoom_factor, r.top() - 10.0 / self.zoom_factor)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(QColor("#FF3B30")))
                    painter.drawEllipse(btn_center, btn_r, btn_r)
                    painter.setPen(QPen(QColor("#FFFFFF"), 1.5 / self.zoom_factor))
                    del_d = 4.0 / self.zoom_factor
                    painter.drawLine(QPointF(btn_center.x() - del_d, btn_center.y() - del_d),
                                     QPointF(btn_center.x() + del_d, btn_center.y() + del_d))
                    painter.drawLine(QPointF(btn_center.x() + del_d, btn_center.y() - del_d),
                                     QPointF(btn_center.x() - del_d, btn_center.y() + del_d))
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


class ActionRecorder:
    STATE_IDLE = 0
    STATE_RECORDING = 1
    STATE_PAUSED = 2

    def __init__(self, player):
        self.player = player
        self.state = self.STATE_IDLE
        self.events = []
        self.elapsed_time = 0.0
        self.last_resume_time = 0.0
        self.last_recorded_frame = -1
        self.last_zoom = -1.0
        self.last_pan = (None, None)
        self._stroke_counter = 0

    def allocate_stroke_id(self) -> int:
        self._stroke_counter += 1
        return self._stroke_counter

    def is_recording(self) -> bool:
        return self.state == self.STATE_RECORDING

    def is_paused(self) -> bool:
        return self.state == self.STATE_PAUSED

    def is_active(self) -> bool:
        return self.state in (self.STATE_RECORDING, self.STATE_PAUSED)

    def current_time(self) -> float:
        if self.state == self.STATE_RECORDING:
            return round(self.elapsed_time + (time.perf_counter() - self.last_resume_time), 4)
        return round(self.elapsed_time, 4)

    def start(self):
        self.events.clear()
        self.elapsed_time = 0.0
        self.last_resume_time = time.perf_counter()
        self.state = self.STATE_RECORDING
        self._stroke_counter = 0
        self.last_recorded_frame = -1
        self.last_zoom = -1.0
        self.last_pan = (None, None)

        self.record_frame(force=True)

        if hasattr(self.player, 'canvas') and self.player.canvas.strokes:
            for s in self.player.canvas.strokes:
                sid = self.allocate_stroke_id()
                s.stroke_id = sid
                if s.points:
                    self.events.append({
                        'type': 'stroke_start',
                        'time': 0.0,
                        'stroke_id': sid,
                        'color': s.color.name(),
                        'width': s.width,
                        'pt': (round(s.points[0].x(), 2), round(s.points[0].y(), 2))
                    })
                    for pt in s.points[1:]:
                        self.events.append({
                            'type': 'stroke_point',
                            'time': 0.0,
                            'stroke_id': sid,
                            'pt': (round(pt.x(), 2), round(pt.y(), 2))
                        })
                    self.events.append({
                        'type': 'stroke_end',
                        'time': 0.0,
                        'stroke_id': sid
                    })

        if hasattr(self.player, 'canvas') and self.player.canvas.overlays:
            for ov in self.player.canvas.overlays:
                self.events.append({
                    'type': 'overlay_add',
                    'time': 0.0,
                    'obj_id': ov.obj_id,
                    'file_path': ov.file_path,
                    'rect': [round(ov.rect.x(), 2), round(ov.rect.y(), 2),
                             round(ov.rect.width(), 2), round(ov.rect.height(), 2)]
                })

    def pause(self):
        if self.state == self.STATE_RECORDING:
            self.elapsed_time += (time.perf_counter() - self.last_resume_time)
            self.state = self.STATE_PAUSED
        elif self.state == self.STATE_PAUSED:
            self.last_resume_time = time.perf_counter()
            self.state = self.STATE_RECORDING
            self.record_frame(force=True)

    def stop(self):
        if self.state == self.STATE_RECORDING:
            self.elapsed_time += (time.perf_counter() - self.last_resume_time)
        self.state = self.STATE_IDLE
        self.events.append({
            'type': 'stop',
            'time': round(self.elapsed_time, 4)
        })

    def record_frame(self, force=False):
        if self.state != self.STATE_RECORDING:
            return
        t = self.current_time()
        f_idx = self.player.current_frame_idx
        zoom = self.player.canvas.zoom_factor if hasattr(self.player, 'canvas') else 1.0
        pan = (
            self.player.canvas.pan_offset.x() if hasattr(self.player, 'canvas') else 0.0,
            self.player.canvas.pan_offset.y() if hasattr(self.player, 'canvas') else 0.0
        )
        if not force and f_idx == self.last_recorded_frame and abs(zoom - self.last_zoom) < 1e-3 and pan == self.last_pan:
            return

        self.last_recorded_frame = f_idx
        self.last_zoom = zoom
        self.last_pan = pan
        self.events.append({
            'type': 'frame',
            'time': t,
            'frame_idx': f_idx,
            'zoom': round(zoom, 4),
            'pan': (round(pan[0], 2), round(pan[1], 2))
        })

    def record_stroke_start(self, stroke_id: int, color_hex: str, width: float, pt: tuple):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_start',
            'time': self.current_time(),
            'stroke_id': stroke_id,
            'color': color_hex,
            'width': round(width, 2),
            'pt': (round(pt[0], 2), round(pt[1], 2))
        })

    def record_stroke_point(self, stroke_id: int, pt: tuple):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_point',
            'time': self.current_time(),
            'stroke_id': stroke_id,
            'pt': (round(pt[0], 2), round(pt[1], 2))
        })

    def record_stroke_end(self, stroke_id: int):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_end',
            'time': self.current_time(),
            'stroke_id': stroke_id
        })

    def record_undo(self):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'undo',
            'time': self.current_time()
        })

    def record_clear(self):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'clear',
            'time': self.current_time()
        })

    def record_erase(self, stroke_ids: list):
        if self.state != self.STATE_RECORDING or not stroke_ids:
            return
        self.events.append({
            'type': 'erase',
            'time': self.current_time(),
            'stroke_ids': list(stroke_ids)
        })

    def record_overlay_add(self, overlay):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'overlay_add',
            'time': self.current_time(),
            'obj_id': overlay.obj_id,
            'file_path': overlay.file_path,
            'rect': [round(overlay.rect.x(), 2), round(overlay.rect.y(), 2),
                     round(overlay.rect.width(), 2), round(overlay.rect.height(), 2)]
        })

    def record_overlay_transform(self, overlay):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'overlay_transform',
            'time': self.current_time(),
            'obj_id': overlay.obj_id,
            'rect': [round(overlay.rect.x(), 2), round(overlay.rect.y(), 2),
                     round(overlay.rect.width(), 2), round(overlay.rect.height(), 2)]
        })

    def record_overlay_remove(self, obj_id: int):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'overlay_remove',
            'time': self.current_time(),
            'obj_id': obj_id
        })

    def to_dict(self) -> dict:
        return {
            'video_path': getattr(self.player, 'video_path', ''),
            'video_fps': getattr(self.player, 'fps', 25.0),
            'total_frames': getattr(self.player, 'total_frames', 0),
            'total_duration': round(self.elapsed_time, 4),
            'event_count': len(self.events),
            'events': self.events
        }

    def save_json(self, path: str):
        data = self.to_dict()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_json(self, path: str):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.events = data.get('events', [])
        self.elapsed_time = float(data.get('total_duration', 0.0))
        self.state = self.STATE_IDLE


class ExportVideoWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, video_path, events, total_duration, output_path, fps=30.0, out_size=None):
        super().__init__()
        self.video_path = video_path
        self.events = events
        self.total_duration = total_duration
        self.output_path = output_path
        self.fps = max(10.0, min(60.0, fps))
        self.out_size = out_size
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        if not self.events or self.total_duration <= 0.05:
            self.finished.emit(False, "Recording is empty or duration is too short.")
            return

        cap = None
        src_w, src_h = 1280, 720
        if self.video_path and os.path.exists(self.video_path):
            cap = cv2.VideoCapture(self.video_path)
            if cap.isOpened():
                src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            else:
                cap = None

        if src_w <= 0 or src_h <= 0:
            src_w, src_h = 1280, 720

        out_w = (src_w // 2) * 2
        out_h = (src_h // 2) * 2

        use_pyav = True
        container = None
        stream = None
        cv_writer = None

        try:
            container = av.open(self.output_path, mode='w')
            stream = container.add_stream('h264', rate=int(round(self.fps)))
            stream.width = out_w
            stream.height = out_h
            stream.pix_fmt = 'yuv420p'
            stream.options = {'crf': '20', 'preset': 'veryfast'}
        except Exception:
            use_pyav = False
            if container:
                try:
                    container.close()
                except Exception:
                    pass
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            cv_writer = cv2.VideoWriter(self.output_path, fourcc, self.fps, (out_w, out_h))
            if not cv_writer.isOpened():
                if cap:
                    cap.release()
                self.finished.emit(False, "Failed to initialize video codec for export.")
                return

        total_frames = max(1, int(round(self.total_duration * self.fps)))
        event_idx = 0
        total_events = len(self.events)

        active_strokes = collections.OrderedDict()
        active_overlays = collections.OrderedDict()
        current_frame_idx = 0
        cached_bgr_frame = None
        cached_frame_idx = -1
        last_cap_pos = -1

        for frame_num in range(total_frames):
            if self._is_cancelled:
                break

            t = frame_num / float(self.fps)

            while event_idx < total_events and self.events[event_idx]['time'] <= t:
                ev = self.events[event_idx]
                etype = ev['type']
                if etype == 'frame':
                    current_frame_idx = ev['frame_idx']
                elif etype == 'stroke_start':
                    sid = ev['stroke_id']
                    col = QColor(ev['color'])
                    w = ev['width']
                    pt = QPointF(ev['pt'][0], ev['pt'][1])
                    p = QPainterPath()
                    p.moveTo(pt)
                    active_strokes[sid] = {
                        'color': col,
                        'width': w,
                        'points': [pt],
                        'path': p
                    }
                elif etype == 'stroke_point':
                    sid = ev['stroke_id']
                    if sid in active_strokes:
                        pt = QPointF(ev['pt'][0], ev['pt'][1])
                        active_strokes[sid]['points'].append(pt)
                        active_strokes[sid]['path'].lineTo(pt)
                elif etype == 'undo':
                    if active_strokes:
                        active_strokes.popitem(last=True)
                elif etype == 'clear':
                    active_strokes.clear()
                elif etype == 'erase':
                    for sid in ev.get('stroke_ids', []):
                        active_strokes.pop(sid, None)
                elif etype == 'overlay_add':
                    oid = ev['obj_id']
                    fpath = ev.get('file_path', '')
                    r = QRectF(*ev['rect'])
                    active_overlays[oid] = OverlayObject(oid, fpath, r, start_time=ev['time'])
                elif etype == 'overlay_transform':
                    oid = ev['obj_id']
                    if oid in active_overlays:
                        active_overlays[oid].rect = QRectF(*ev['rect'])
                elif etype == 'overlay_remove':
                    oid = ev['obj_id']
                    ov = active_overlays.pop(oid, None)
                    if ov:
                        ov.close()
                event_idx += 1

            if cap is not None:
                if current_frame_idx != cached_frame_idx:
                    if last_cap_pos != current_frame_idx:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame_idx)
                    ret, read_frame = cap.read()
                    if ret:
                        cached_bgr_frame = read_frame
                        cached_frame_idx = current_frame_idx
                        last_cap_pos = current_frame_idx + 1

            render_img = QImage(out_w, out_h, QImage.Format_RGB32)
            painter = QPainter(render_img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            if cached_bgr_frame is not None:
                h_f, w_f, ch_f = cached_bgr_frame.shape
                q_video = QImage(cached_bgr_frame.data, w_f, h_f, ch_f * w_f, QImage.Format_BGR888)
                painter.drawImage(QRectF(0, 0, out_w, out_h), q_video)
            else:
                painter.fillRect(0, 0, out_w, out_h, QColor("#121216"))

            sx = out_w / float(src_w)
            sy = out_h / float(src_h)
            painter.save()
            painter.scale(sx, sy)

            for ov in active_overlays.values():
                ov_img = ov.get_frame_at_time(t)
                if ov_img and not ov_img.isNull():
                    painter.drawImage(ov.rect, ov_img)

            for s in active_strokes.values():
                pts = s['points']
                if not pts:
                    continue
                pen = QPen(s['color'], s['width'], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                if len(pts) == 1:
                    painter.setBrush(QBrush(s['color']))
                    r = s['width'] / 2.0
                    painter.drawEllipse(pts[0], r, r)
                else:
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(s['path'])

            painter.restore()
            painter.end()

            ptr = render_img.bits()
            ptr.setsize(out_h * out_w * 4)
            arr = np.frombuffer(ptr, np.uint8).reshape((out_h, out_w, 4))
            bgr_out = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

            if use_pyav:
                av_frame = av.VideoFrame.from_ndarray(bgr_out, format='bgr24')
                for packet in stream.encode(av_frame):
                    container.mux(packet)
            else:
                cv_writer.write(bgr_out)

            if frame_num % 10 == 0 or frame_num == total_frames - 1:
                self.progress.emit(frame_num + 1, total_frames, f"Exporting: {frame_num + 1}/{total_frames} frames")

        for ov in active_overlays.values():
            ov.close()
        active_overlays.clear()

        if cap:
            cap.release()

        if use_pyav:
            if not self._is_cancelled:
                for packet in stream.encode(None):
                    container.mux(packet)
            container.close()
        else:
            if cv_writer:
                cv_writer.release()

        if self._is_cancelled:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass
            self.finished.emit(False, "Export cancelled.")
        else:
            self.finished.emit(True, self.output_path)


class FKVideoPlayer(QMainWindow):
    MAX_CACHE_FRAMES = 120

    def __init__(self, initial_video_path=None):
        super().__init__()
        self.setWindowTitle("FKVideoPlayer")
        self.resize(1180, 800)
        self.setMinimumSize(800, 540)
        self.setAcceptDrops(True)

        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

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

        self.audio_player = QMediaPlayer(self, QMediaPlayer.LowLatency)
        self.current_volume = 80
        self.is_muted = False
        self.audio_player.setVolume(self.current_volume)

        self.frame_cache = collections.OrderedDict()
        self._cap_pos = -1

        self._pending_seek_frame = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._process_pending_seek)

        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._on_step_timer)

        self._audio_sync_timer = QTimer(self)
        self._audio_sync_timer.setSingleShot(True)
        self._audio_sync_timer.timeout.connect(self._sync_audio_position)

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._on_play_tick)

        self.recorder = ActionRecorder(self)
        self.rec_update_timer = QTimer(self)
        self.rec_update_timer.timeout.connect(self._on_rec_update_timer)
        self.export_worker = None

        self.overlay_anim_timer = QTimer(self)
        self.overlay_anim_timer.timeout.connect(self._on_overlay_anim_tick)
        self.overlay_anim_timer.start(33)

        self._init_ui()
        self._apply_dark_theme()
        self._setup_shortcuts()

        if initial_video_path and os.path.exists(initial_video_path):
            self.load_video(initial_video_path)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        self.canvas = VideoCanvas(self)
        self.canvas.recorder = self.recorder
        self.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
        self.canvas.drawing_changed.connect(self._on_drawing_changed)

        self._create_top_toolbar()
        self._create_recording_bar()
        main_layout.addWidget(self.top_toolbar)
        main_layout.addWidget(self.recording_bar)
        main_layout.addWidget(self.canvas, stretch=1)

        bottom_panel = self._create_bottom_controls()
        main_layout.addWidget(bottom_panel)

    def _create_top_toolbar(self):
        self.top_toolbar = QFrame()
        self.top_toolbar.setObjectName("TopToolbar")
        layout = QHBoxLayout(self.top_toolbar)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        self.btn_open = QPushButton("📂 Open")
        self.btn_open.setToolTip("Open video file (O)")
        self.btn_open.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.btn_open)

        self._add_separator(layout)

        self.btn_tool_pen = QPushButton("✏️ Pen")
        self.btn_tool_pen.setToolTip("Draw on video (P)")
        self.btn_tool_pen.setCheckable(True)
        self.btn_tool_pen.setChecked(True)
        self.btn_tool_pen.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        layout.addWidget(self.btn_tool_pen)

        self.btn_tool_eraser = QPushButton("🧹 Eraser")
        self.btn_tool_eraser.setToolTip("Erase drawings (E)")
        self.btn_tool_eraser.setCheckable(True)
        self.btn_tool_eraser.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        layout.addWidget(self.btn_tool_eraser)

        self.btn_tool_pan = QPushButton("✋ Pan")
        self.btn_tool_pan.setToolTip("Pan video canvas (H)")
        self.btn_tool_pan.setCheckable(True)
        self.btn_tool_pan.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        layout.addWidget(self.btn_tool_pan)

        self.btn_tool_select = QPushButton("🎯 Select")
        self.btn_tool_select.setToolTip("Select, move, and transform overlay objects (V)")
        self.btn_tool_select.setCheckable(True)
        self.btn_tool_select.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_SELECT))
        layout.addWidget(self.btn_tool_select)

        self._add_separator(layout)

        self.preset_color_buttons = {}
        preset_colors = [
            ("#FF3B30", "Red"),
            ("#34C759", "Green"),
            ("#007AFF", "Blue"),
            ("#FFCC00", "Yellow"),
            ("#FFFFFF", "White"),
            ("#FF9500", "Orange"),
            ("#AF52DE", "Purple"),
        ]
        layout.addWidget(QLabel("Color:"))
        for hex_code, name in preset_colors:
            btn = QPushButton()
            btn.setFixedSize(20, 20)
            btn.setToolTip(f"{name} ({hex_code})")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=hex_code: self._set_brush_color(c))
            layout.addWidget(btn)
            self.preset_color_buttons[hex_code.upper()] = btn

        self.btn_custom_color = QPushButton("🎨")
        self.btn_custom_color.setToolTip("Choose custom brush color...")
        self.btn_custom_color.setFixedSize(24, 24)
        self.btn_custom_color.clicked.connect(self._pick_custom_color)
        layout.addWidget(self.btn_custom_color)

        self.color_indicator = QFrame()
        self.color_indicator.setFixedSize(18, 18)
        self.color_indicator.setToolTip("Current brush color")
        layout.addWidget(self.color_indicator)
        self._update_color_buttons_state("#FF3B30")

        layout.addWidget(QLabel("Size:"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 60)
        self.spin_width.setValue(4)
        self.spin_width.setSuffix(" px")
        self.spin_width.setToolTip("Brush stroke width ([ and ])")
        self.spin_width.valueChanged.connect(self._on_pen_width_changed)
        layout.addWidget(self.spin_width)

        self._add_separator(layout)

        self.btn_undo = QPushButton("↩ Undo")
        self.btn_undo.setToolTip("Undo last drawing action (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.canvas.undo_last_action)
        layout.addWidget(self.btn_undo)

        self.btn_clear_all = QPushButton("🗑 Clear")
        self.btn_clear_all.setToolTip("Clear all drawings (Delete / C)")
        self.btn_clear_all.clicked.connect(self.canvas.clear_all_drawings)
        layout.addWidget(self.btn_clear_all)

        self.btn_add_overlay = QPushButton("🖼️ Add Overlay...")
        self.btn_add_overlay.setToolTip("Add image, GIF, or secondary video overlay (Ctrl+I)")
        self.btn_add_overlay.clicked.connect(self.add_overlay_dialog)
        layout.addWidget(self.btn_add_overlay)

        layout.addStretch(1)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(40)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)

        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.setToolTip("Zoom out (-)")
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Zoom in (+)")
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)

        self.btn_zoom_reset = QPushButton("1:1")
        self.btn_zoom_reset.setToolTip("Fit to window (0)")
        self.btn_zoom_reset.clicked.connect(self.canvas.fit_to_view)

        layout.addWidget(self.btn_zoom_out)
        layout.addWidget(self.lbl_zoom)
        layout.addWidget(self.btn_zoom_in)
        layout.addWidget(self.btn_zoom_reset)

    def _add_separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #383848; margin: 2px 2px;")
        layout.addWidget(line)

    def _create_recording_bar(self):
        self.recording_bar = QFrame()
        self.recording_bar.setObjectName("RecordBar")
        layout = QHBoxLayout(self.recording_bar)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(5)

        self.btn_rec_start = QPushButton("⏺ Record")
        self.btn_rec_start.setObjectName("BtnRecord")
        self.btn_rec_start.setToolTip("Start action recording (Ctrl+R)")
        self.btn_rec_start.clicked.connect(self.start_actions_record)
        layout.addWidget(self.btn_rec_start)

        self.btn_rec_pause = QPushButton("❚❚ Pause")
        self.btn_rec_pause.setObjectName("BtnPauseRec")
        self.btn_rec_pause.setToolTip("Pause / Resume action recording (Ctrl+Shift+P)")
        self.btn_rec_pause.setEnabled(False)
        self.btn_rec_pause.clicked.connect(self.pause_actions_record)
        layout.addWidget(self.btn_rec_pause)

        self.btn_rec_stop = QPushButton("⏹ Stop")
        self.btn_rec_stop.setObjectName("BtnStopRec")
        self.btn_rec_stop.setToolTip("Stop action recording")
        self.btn_rec_stop.setEnabled(False)
        self.btn_rec_stop.clicked.connect(self.stop_actions_record)
        layout.addWidget(self.btn_rec_stop)

        self._add_separator(layout)

        self.btn_rec_export = QPushButton("💾 Export MP4...")
        self.btn_rec_export.setObjectName("BtnExportRec")
        self.btn_rec_export.setToolTip("Export recorded actions to MP4 video (Ctrl+E)")
        self.btn_rec_export.setEnabled(False)
        self.btn_rec_export.clicked.connect(self.export_recorded_video)
        layout.addWidget(self.btn_rec_export)

        self.btn_rec_save = QPushButton("📥 Save JSON")
        self.btn_rec_save.setToolTip("Save actions session to JSON")
        self.btn_rec_save.setEnabled(False)
        self.btn_rec_save.clicked.connect(self.save_actions_json)
        layout.addWidget(self.btn_rec_save)

        self.btn_rec_load = QPushButton("📤 Load JSON")
        self.btn_rec_load.setToolTip("Load actions session from JSON")
        self.btn_rec_load.clicked.connect(self.load_actions_json)
        layout.addWidget(self.btn_rec_load)

        layout.addStretch(1)

        self.lbl_rec_status = QLabel("● Recording idle")
        self.lbl_rec_status.setObjectName("RecStatus")
        self.lbl_rec_status.setStyleSheet("color: #7E7E94; font-family: Consolas, monospace;")
        layout.addWidget(self.lbl_rec_status)

    def _create_bottom_controls(self):
        panel = QFrame()
        panel.setObjectName("BottomPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self.timeline_slider = ClickableSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.wheel_scrolled.connect(self._on_slider_wheel)
        layout.addWidget(self.timeline_slider)

        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(5)

        self.btn_rewind_5s = QPushButton("⏮ -5s")
        self.btn_rewind_5s.setToolTip("Rewind 5 seconds (J or Ctrl+Left)")
        self.btn_rewind_5s.clicked.connect(lambda: self.seek_seconds(-5.0))
        ctrl_layout.addWidget(self.btn_rewind_5s)

        self.btn_rewind_1s = QPushButton("◀◀ -1s")
        self.btn_rewind_1s.setToolTip("Rewind 1 second (Shift+Left)")
        self.btn_rewind_1s.clicked.connect(lambda: self.seek_seconds(-1.0))
        ctrl_layout.addWidget(self.btn_rewind_1s)

        self.btn_prev_frame = QPushButton("◀| Prev")
        self.btn_prev_frame.setToolTip("Step backward 1 frame (Left Arrow)")
        self.btn_prev_frame.clicked.connect(lambda: self.step_frame(-1))
        ctrl_layout.addWidget(self.btn_prev_frame)

        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setObjectName("PlayButton")
        self.btn_play_pause.setToolTip("Play / Pause (Space / K)")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        ctrl_layout.addWidget(self.btn_play_pause)

        self.btn_next_frame = QPushButton("Next |▶")
        self.btn_next_frame.setToolTip("Step forward 1 frame (Right Arrow)")
        self.btn_next_frame.clicked.connect(lambda: self.step_frame(1))
        ctrl_layout.addWidget(self.btn_next_frame)

        self.btn_forward_1s = QPushButton("+1s ▶▶")
        self.btn_forward_1s.setToolTip("Forward 1 second (Shift+Right)")
        self.btn_forward_1s.clicked.connect(lambda: self.seek_seconds(1.0))
        ctrl_layout.addWidget(self.btn_forward_1s)

        self.btn_forward_5s = QPushButton("+5s ⏭")
        self.btn_forward_5s.setToolTip("Forward 5 seconds (L or Ctrl+Right)")
        self.btn_forward_5s.clicked.connect(lambda: self.seek_seconds(5.0))
        ctrl_layout.addWidget(self.btn_forward_5s)

        self.btn_loop = QPushButton("🔁 Loop")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        self.btn_loop.setToolTip("Loop playback")
        self.btn_loop.clicked.connect(self._toggle_loop)
        ctrl_layout.addWidget(self.btn_loop)

        self._add_separator(ctrl_layout)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedSize(26, 24)
        self.btn_mute.setToolTip("Toggle mute (M)")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl_layout.addWidget(self.btn_mute)

        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(self.current_volume)
        self.slider_volume.setFixedWidth(70)
        self.slider_volume.setFocusPolicy(Qt.NoFocus)
        self.slider_volume.setToolTip("Volume (Up / Down)")
        self.slider_volume.valueChanged.connect(self.set_volume)
        ctrl_layout.addWidget(self.slider_volume)

        self.lbl_volume = QLabel(f"{self.current_volume}%")
        self.lbl_volume.setMinimumWidth(32)
        ctrl_layout.addWidget(self.lbl_volume)

        self._add_separator(ctrl_layout)

        ctrl_layout.addWidget(QLabel("Speed:"))
        self.combo_speed = QComboBox()
        self.speed_presets = [
            "0.05x", "0.1x", "0.2x", "0.25x", "0.33x", "0.5x", "0.75x",
            "1.0x",
            "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x", "4.0x", "5.0x", "8.0x", "10.0x"
        ]
        self.combo_speed.addItems(self.speed_presets)
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.setToolTip("Playback speed (< and >, R for 1.0x)")
        self.combo_speed.currentTextChanged.connect(self._on_speed_changed)
        ctrl_layout.addWidget(self.combo_speed)

        ctrl_layout.addStretch(1)

        self.lbl_time_info = QLabel("00:00.00 / 00:00.00  |  Frame: 0 / 0  (0.0 FPS)")
        self.lbl_time_info.setStyleSheet("font-family: Consolas, monospace; font-size: 12px; color: #9EABB8;")
        ctrl_layout.addWidget(self.lbl_time_info)

        layout.addLayout(ctrl_layout)
        return panel

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play_pause)
        QShortcut(QKeySequence("K"), self, self.toggle_play_pause)

        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.step_frame(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.step_frame(1))

        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.seek_seconds(-1.0))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_seconds(1.0))

        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.seek_seconds(-5.0))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.seek_seconds(5.0))
        QShortcut(QKeySequence("J"), self, lambda: self.seek_seconds(-5.0))
        QShortcut(QKeySequence("L"), self, lambda: self.seek_seconds(5.0))

        QShortcut(QKeySequence(Qt.Key_Home), self, lambda: self._seek_to_frame(0))
        QShortcut(QKeySequence(Qt.Key_End), self, lambda: self._seek_to_frame(self.total_frames - 1))

        QShortcut(QKeySequence("M"), self, self.toggle_mute)
        QShortcut(QKeySequence(Qt.Key_Up), self, lambda: self.adjust_volume(5))
        QShortcut(QKeySequence(Qt.Key_Down), self, lambda: self.adjust_volume(-5))

        QShortcut(QKeySequence("Ctrl+Z"), self, self.canvas.undo_last_action)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self._on_delete_shortcut)
        QShortcut(QKeySequence(Qt.Key_Backspace), self, self._on_delete_shortcut)
        QShortcut(QKeySequence("Ctrl+D"), self, self._on_duplicate_shortcut)
        QShortcut(QKeySequence("C"), self, self.canvas.clear_all_drawings)

        QShortcut(QKeySequence("["), self, lambda: self.spin_width.setValue(self.spin_width.value() - 1))
        QShortcut(QKeySequence("]"), self, lambda: self.spin_width.setValue(self.spin_width.value() + 1))

        QShortcut(QKeySequence("<"), self, self.decrease_speed)
        QShortcut(QKeySequence(">"), self, self.increase_speed)
        QShortcut(QKeySequence("Shift+,"), self, self.decrease_speed)
        QShortcut(QKeySequence("Shift+."), self, self.increase_speed)
        QShortcut(QKeySequence("R"), self, self.reset_speed)

        QShortcut(QKeySequence("+"), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("="), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("-"), self, self.canvas.zoom_out)
        QShortcut(QKeySequence("0"), self, self.canvas.fit_to_view)

        QShortcut(QKeySequence("P"), self, lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        QShortcut(QKeySequence("E"), self, lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        QShortcut(QKeySequence("H"), self, lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        QShortcut(QKeySequence("V"), self, lambda: self._select_tool(VideoCanvas.TOOL_SELECT))
        QShortcut(QKeySequence("O"), self, self.open_file_dialog)
        QShortcut(QKeySequence("Ctrl+I"), self, self.add_overlay_dialog)

        QShortcut(QKeySequence("Ctrl+R"), self, self._shortcut_toggle_record)
        QShortcut(QKeySequence("Ctrl+Shift+P"), self, self.pause_actions_record)
        QShortcut(QKeySequence("Ctrl+E"), self, self.export_recorded_video)

        QShortcut(QKeySequence("PageUp"), self, lambda: self.bring_overlay_forward(self.canvas.selected_overlay))
        QShortcut(QKeySequence("PageDown"), self, lambda: self.send_overlay_backward(self.canvas.selected_overlay))
        QShortcut(QKeySequence("Shift+PageUp"), self, lambda: self.bring_overlay_to_front(self.canvas.selected_overlay))
        QShortcut(QKeySequence("Shift+PageDown"), self, lambda: self.send_overlay_to_back(self.canvas.selected_overlay))

    def delete_selected_overlay(self):
        if self.canvas.selected_overlay:
            self.canvas.remove_overlay(self.canvas.selected_overlay)

    def _on_delete_shortcut(self):
        if self.canvas.active_tool == VideoCanvas.TOOL_SELECT and self.canvas.selected_overlay:
            self.delete_selected_overlay()
        else:
            self.canvas.clear_all_drawings()

    def _on_duplicate_shortcut(self):
        if self.canvas.active_tool == VideoCanvas.TOOL_SELECT and self.canvas.selected_overlay:
            self.duplicate_overlay(self.canvas.selected_overlay)

    def bring_overlay_to_front(self, ov):
        if ov and ov in self.canvas.overlays:
            self.canvas.overlays.remove(ov)
            self.canvas.overlays.append(ov)
            self.canvas.update()

    def send_overlay_to_back(self, ov):
        if ov and ov in self.canvas.overlays:
            self.canvas.overlays.remove(ov)
            self.canvas.overlays.insert(0, ov)
            self.canvas.update()

    def bring_overlay_forward(self, ov):
        if ov and ov in self.canvas.overlays:
            idx = self.canvas.overlays.index(ov)
            if idx < len(self.canvas.overlays) - 1:
                self.canvas.overlays[idx], self.canvas.overlays[idx + 1] = (
                    self.canvas.overlays[idx + 1], self.canvas.overlays[idx]
                )
                self.canvas.update()

    def send_overlay_backward(self, ov):
        if ov and ov in self.canvas.overlays:
            idx = self.canvas.overlays.index(ov)
            if idx > 0:
                self.canvas.overlays[idx], self.canvas.overlays[idx - 1] = (
                    self.canvas.overlays[idx - 1], self.canvas.overlays[idx]
                )
                self.canvas.update()

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
        if self.recorder.is_active():
            self.recorder.record_overlay_add(new_ov)
        self.canvas.update()

    def _on_overlay_anim_tick(self):
        if not self.is_playing and self.canvas.overlays:
            if any(ov.obj_type in (OverlayObject.TYPE_GIF, OverlayObject.TYPE_VIDEO) for ov in self.canvas.overlays):
                self.canvas.update()

    def add_overlay(self, file_path: str, pos: QPointF = None):
        if not file_path or not os.path.exists(file_path):
            return

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
            except Exception:
                pass
        elif ext in ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v'):
            try:
                temp_cap = cv2.VideoCapture(file_path)
                if temp_cap.isOpened():
                    cw = temp_cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                    ch = temp_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                    if cw > 0 and ch > 0:
                        oh = ow * (ch / float(cw))
                temp_cap.release()
            except Exception:
                pass

        if pos is not None:
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

        if self.recorder.is_active():
            self.recorder.record_overlay_add(overlay)

        self._select_tool(VideoCanvas.TOOL_SELECT)
        self.canvas.update()

    def add_overlay_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image, GIF, or Video Overlay",
            "",
            "Media Files (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.mp4 *.avi *.mov *.mkv *.webm);;Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;Videos (*.mp4 *.avi *.mov *.mkv *.webm);;All Files (*.*)"
        )
        if file_path:
            self.add_overlay(file_path)

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

        if self.cap is None or self.canvas.video_width <= 0:
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

    def toggle_mute(self):
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

    def _update_color_buttons_state(self, current_hex):
        current_hex = current_hex.upper()
        for hex_code, btn in self.preset_color_buttons.items():
            if hex_code == current_hex:
                btn.setStyleSheet(
                    f"background-color: {hex_code}; border-radius: 10px; border: 2px solid #FFFFFF; outline: 2px solid #007AFF;"
                )
            else:
                btn.setStyleSheet(
                    f"background-color: {hex_code}; border-radius: 10px; border: 2px solid #484858;"
                )

        self.color_indicator.setStyleSheet(
            f"background-color: {current_hex}; border: 2px solid #FFFFFF; border-radius: 4px;"
        )

    def _set_brush_color(self, hex_color):
        self.canvas.set_pen_color(hex_color)
        self._update_color_buttons_state(hex_color)
        if self.canvas.active_tool != VideoCanvas.TOOL_PEN:
            self._select_tool(VideoCanvas.TOOL_PEN)

    def _pick_custom_color(self):
        col = QColorDialog.getColor(self.canvas.pen_color, self, "Select Brush Color")
        if col.isValid():
            self._set_brush_color(col.name())

    def _on_pen_width_changed(self, val):
        self.canvas.set_pen_width(val)

    def _on_canvas_zoom_changed(self, zoom):
        self.lbl_zoom.setText(f"{int(round(zoom * 100))}%")
        if hasattr(self, 'recorder'):
            self.recorder.record_frame()

    def _on_drawing_changed(self):
        pass

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.flv *.m4v);;All Files (*.*)"
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path):
        if self.cap is not None:
            self.cap.release()
            self.pause()

        self.cap = cv2.VideoCapture(file_path)
        if not self.cap.isOpened():
            QMessageBox.critical(self, "Error", f"Failed to open video file:\n{file_path}")
            return

        self.video_path = file_path
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        if self.fps <= 1.0 or np.isnan(self.fps):
            self.fps = 25.0

        self.frame_cache.clear()
        self._cap_pos = 0

        self._cleanup_temp_audio()
        self.has_audio = False
        try:
            container = av.open(file_path)
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
                self.temp_audio_path = temp_wav
                self.has_audio = True
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

        self._seek_to_frame(0)
        self.setWindowTitle(f"FKVideoPlayer — {os.path.basename(file_path)}")

    def _cleanup_temp_audio(self):
        if getattr(self, 'temp_audio_path', None) and os.path.exists(self.temp_audio_path):
            try:
                self.audio_player.setMedia(QMediaContent())
                os.remove(self.temp_audio_path)
            except Exception:
                pass
            self.temp_audio_path = None

    def _get_frame_cached(self, frame_idx):
        if self.cap is None or not self.cap.isOpened():
            return None

        if frame_idx in self.frame_cache:
            self.frame_cache.move_to_end(frame_idx)
            return self.frame_cache[frame_idx]

        if getattr(self, '_cap_pos', -1) != frame_idx:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            self._cap_pos = frame_idx

        ret, frame_bgr = self.cap.read()
        if not ret or frame_bgr is None:
            return None

        self._cap_pos += 1
        h, w, ch = frame_bgr.shape
        qimg = QImage(frame_bgr.data, w, h, ch * w, QImage.Format_BGR888).copy()

        if len(self.frame_cache) >= self.MAX_CACHE_FRAMES:
            self.frame_cache.popitem(last=False)
        self.frame_cache[frame_idx] = qimg

        return qimg

    def _seek_to_frame(self, frame_idx):
        if self.cap is None or not self.cap.isOpened() or self.total_frames <= 0:
            return

        frame_idx = max(0, min(self.total_frames - 1, frame_idx))
        qimg = self._get_frame_cached(frame_idx)
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
        if not self.is_playing and self.fps > 0 and self.cap is not None:
            target_ms = int((self.current_frame_idx / self.fps) * 1000)
            self.audio_player.setPosition(target_ms)

    def toggle_play_pause(self):
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

        target_ms = int((self.current_frame_idx / self.fps) * 1000)
        self.audio_player.setPosition(target_ms)
        self._update_audio_rate()
        self.audio_player.play()

    def pause(self):
        self.play_timer.stop()
        self.is_playing = False
        self.btn_play_pause.setText("▶ Play")
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
            interval = int(round(1000.0 / target_fps))
            self.frames_per_tick = 1
        else:
            interval = 16
            self.frames_per_tick = max(1, int(round(target_fps / 60.0)))

        self.play_timer.setInterval(max(2, interval))
        self._update_audio_rate()

    def _on_play_tick(self):
        if self.cap is None or not self.cap.isOpened():
            self.pause()
            return

        step = getattr(self, 'frames_per_tick', 1)
        next_frame = self.current_frame_idx + step
        if next_frame >= self.total_frames:
            if self.is_looping:
                self._seek_to_frame(0)
                if self.is_playing:
                    self.audio_player.setPosition(0)
                    self.audio_player.play()
                return
            else:
                self._seek_to_frame(self.total_frames - 1)
                self.pause()
                return

        if step > 1:
            self._seek_to_frame(next_frame)
        else:
            qimg = self._get_frame_cached(next_frame)
            if qimg is not None:
                self.current_frame_idx = next_frame
                self.canvas.set_frame(qimg)
                self._update_time_label()
                if hasattr(self, 'recorder'):
                    self.recorder.record_frame()

                if 0.5 <= self.playback_speed <= 2.0 and not self.is_muted:
                    audio_pos = self.audio_player.position()
                    expected_audio_pos = int((next_frame / self.fps) * 1000)
                    if abs(audio_pos - expected_audio_pos) > 150:
                        self.audio_player.setPosition(expected_audio_pos)

                if not self.timeline_slider.is_dragging:
                    self.timeline_slider.blockSignals(True)
                    self.timeline_slider.setValue(next_frame)
                    self.timeline_slider.blockSignals(False)
            else:
                if self.is_looping:
                    self._seek_to_frame(0)
                else:
                    self.pause()

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
        self.step_frame(delta_frames)

    def _toggle_loop(self):
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
        if not self._seek_timer.isActive():
            self._seek_timer.start(16)

    def _process_pending_seek(self):
        if self._pending_seek_frame is not None:
            target = self._pending_seek_frame
            self._pending_seek_frame = None
            self._seek_to_frame(target)

    def _on_slider_released(self):
        self._seek_timer.stop()
        if self._pending_seek_frame is not None:
            val = self._pending_seek_frame
            self._pending_seek_frame = None
        else:
            val = self.timeline_slider.value()

        self._seek_to_frame(val)
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
        if self.fps > 0 and self.total_frames > 0:
            cur_sec = self.current_frame_idx / self.fps
            tot_sec = self.total_frames / self.fps
            self.lbl_time_info.setText(
                f"{self._format_time(cur_sec)} / {self._format_time(tot_sec)}  |  Frame: {self.current_frame_idx + 1} / {self.total_frames}  ({self.fps:.1f} FPS)"
            )
        else:
            self.lbl_time_info.setText("00:00.00 / 00:00.00  |  Frame: 0 / 0  (0.0 FPS)")

    def closeEvent(self, event):
        self.play_timer.stop()
        self.audio_player.stop()
        self._cleanup_temp_audio()
        if self.cap is not None:
            self.cap.release()
        event.accept()

    def _format_rec_time(self, sec: float) -> str:
        mins = int(sec // 60)
        secs = sec % 60
        return f"{mins:02d}:{secs:04.1f}"

    def start_actions_record(self):
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

    def pause_actions_record(self):
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

    def stop_actions_record(self):
        if not self.recorder.is_active():
            return
        self.recorder.stop()
        self.rec_update_timer.stop()

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

    def export_recorded_video(self):
        if not self.recorder.events or self.recorder.elapsed_time <= 0.05:
            QMessageBox.information(self, "Export Video", "No recorded actions to export.")
            return

        base_name = "action_export.mp4"
        dir_name = ""
        v_path = self.video_path or getattr(self.recorder.player, 'video_path', '')
        if v_path:
            v_name = os.path.splitext(os.path.basename(v_path))[0]
            base_name = f"{v_name}_edited.mp4"
            dir_name = os.path.dirname(v_path)

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Edited Video",
            os.path.join(dir_name, base_name),
            "MP4 Video (*.mp4);;All Files (*.*)"
        )
        if not save_path:
            return

        export_fps = min(60.0, max(24.0, self.fps))
        progress_dialog = QProgressDialog("Preparing video export...", "Cancel", 0, 100, self)
        progress_dialog.setWindowTitle("Export Video")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setValue(0)

        self.export_worker = ExportVideoWorker(
            video_path=v_path,
            events=list(self.recorder.events),
            total_duration=self.recorder.elapsed_time,
            output_path=save_path,
            fps=export_fps
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

    def save_actions_json(self):
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

    def load_actions_json(self):
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

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #16161C;
            }
            #TopToolbar, #BottomPanel, #RecordBar {
                background-color: #1F1F28;
                border: 1px solid #2B2B38;
                border-radius: 8px;
            }
            #RecordBar {
                background-color: #1A1A22;
                border-color: #272734;
            }
            #BtnRecord {
                background-color: #7A1414;
                color: #FFFFFF;
                font-weight: bold;
                border-color: #9B1C1C;
            }
            #BtnRecord:hover {
                background-color: #9B1C1C;
                border-color: #BD2424;
            }
            #BtnPauseRec {
                background-color: #5A4710;
                color: #FFECA8;
                border-color: #7A5F14;
            }
            #BtnPauseRec:hover {
                background-color: #7A5F14;
            }
            #BtnStopRec {
                background-color: #2C2C3A;
                color: #D6D6E6;
            }
            #BtnStopRec:hover {
                background-color: #3C3C4E;
            }
            #BtnExportRec {
                background-color: #1B5935;
                color: #FFFFFF;
                font-weight: bold;
                border-color: #267A49;
            }
            #BtnExportRec:hover {
                background-color: #267A49;
            }
            #RecStatus {
                font-family: Consolas, monospace;
                font-size: 12px;
                font-weight: 500;
                padding: 3px 8px;
                border-radius: 4px;
                background-color: #14141A;
                border: 1px solid #262632;
            }
            QLabel {
                color: #D2D2E0;
                font-size: 12px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QPushButton {
                background-color: #2A2A38;
                color: #E2E2EC;
                border: 1px solid #3B3B4E;
                border-radius: 5px;
                padding: 5px 9px;
                font-weight: 500;
                font-size: 12px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QPushButton:hover {
                background-color: #373748;
                border-color: #55556B;
            }
            QPushButton:pressed {
                background-color: #1A1A24;
            }
            QPushButton:checked {
                background-color: #007AFF;
                color: #FFFFFF;
                border-color: #3895FF;
            }
            #PlayButton {
                background-color: #248A3D;
                font-weight: bold;
                min-width: 80px;
                border-color: #2EA249;
            }
            #PlayButton:hover {
                background-color: #2CAC4B;
            }
            QSpinBox, QComboBox {
                background-color: #2A2A38;
                color: #E2E2EC;
                border: 1px solid #3B3B4E;
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 12px;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            QSlider::groove:horizontal {
                height: 7px;
                background: #282836;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #007AFF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #007AFF;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #60A5FA;
                border-color: #3895FF;
            }
            QToolTip {
                background-color: #2A2A36;
                color: #FFFFFF;
                border: 1px solid #4E4E62;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-family: Consolas, monospace;
            }
        """)


VideoPlayerWindow = FKVideoPlayer


def main():
    app = QApplication(sys.argv)
    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    initial_file = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        initial_file = sys.argv[1]

    player = FKVideoPlayer(initial_file)
    player.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
