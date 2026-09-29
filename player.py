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
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal, QPoint, QUrl, QThread, QObject
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QBrush, QCursor,
    QFont, QIcon, QKeySequence, QPainterPath
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QColorDialog,
    QComboBox, QSpinBox, QFrame, QShortcut, QMessageBox, QToolTip,
    QProgressDialog, QScrollArea, QMenu, QAction, QSizePolicy,
    QTabWidget, QToolButton
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

from i18n import tr, I18nManager
from capture import list_open_windows, WindowCaptureWorker
from audio import MicrophoneRecorder, get_audio_input_devices, SystemAudioRecorder
from projects import ProjectManager
from settings_dialogs import (
    NewCanvasDialog, WindowCaptureDialog, TextOverlayDialog,
    ExportDialog, PreferencesDialog, AboutDialog, UpdatesDialog,
    VideoOverlaySettingsDialog, AutoAdjustTabBar,
    DEFAULT_HOTKEYS, HOTKEYS_CONFIG_PATH
)

from logger import init_logging, get_logger, open_log_file, open_logs_folder
logger = get_logger("Player")


def set_dark_titlebar(window):
    """Enable native dark titlebar and caption color on Windows 10/11"""
    try:
        import ctypes
        from ctypes import c_int, byref, sizeof
        hwnd = int(window.winId())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 19041+ / Windows 11)
        val = c_int(1)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(val), sizeof(val))
        if res != 0:
            # Older Windows 10 (1809 / 1903)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, byref(val), sizeof(val))
        # DWMWA_CAPTION_COLOR = 35 (Windows 11 build 22000+) - Dark theme #121218 (BGR: 0x00181212)
        dark_color = c_int(0x00181212)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, byref(dark_color), sizeof(dark_color))
        # DWMWA_TEXT_COLOR = 36 (White titlebar text)
        white_text = c_int(0x00FFFFFF)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, byref(white_text), sizeof(white_text))
    except Exception:
        pass


def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


APP_VERSION = "1.1.2"


def get_ffmpeg_path():
    """Finds ffmpeg executable: bundled in PyInstaller MEIPASS, in app directory, in PATH, or system fallback."""
    import shutil
    candidates = []
    # 1. Bundled in PyInstaller _MEIPASS
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, 'ffmpeg.exe'))
        candidates.append(os.path.join(sys._MEIPASS, 'ffmpeg'))
    # 2. Next to executable or current file
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(app_dir, 'ffmpeg.exe'))
    candidates.append(os.path.join(app_dir, 'ffmpeg'))
    # 3. In PATH
    which_path = shutil.which("ffmpeg")
    if which_path:
        candidates.append(which_path)
    # 4. Standard Windows locations
    candidates.extend([
        r"C:\ffmpeg\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\ffmpeg.exe",
    ])
    for cand in candidates:
        if cand and os.path.exists(cand) and os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


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


def simplify_points(pts: list, tol: float = 0.8) -> list:
    """Ramer-Douglas-Peucker-like fast polyline simplifier to keep point counts minimal"""
    if len(pts) <= 2:
        return pts
    result = [pts[0]]
    tol2 = tol * tol
    for i in range(1, len(pts) - 1):
        prev = result[-1]
        cur = pts[i]
        nxt = pts[i + 1]
        dx = nxt.x() - prev.x()
        dy = nxt.y() - prev.y()
        l2 = dx * dx + dy * dy
        if l2 < 1e-6:
            continue
        t = max(0.0, min(1.0, ((cur.x() - prev.x()) * dx + (cur.y() - prev.y()) * dy) / l2))
        px = prev.x() + t * dx
        py = prev.y() + t * dy
        dist2 = (cur.x() - px) ** 2 + (cur.y() - py) ** 2
        if dist2 > tol2:
            result.append(cur)
    result.append(pts[-1])
    return result


def erase_stroke_subsegments(pts: list, center: QPointF, radius: float, stroke_width: float) -> list:
    """
    Sub-segment stroke carving: like Paint/Photoshop, cuts out and erases
    only the portion of the stroke under the circular eraser tip rather than
    deleting the whole stroke.
    """
    import math
    eff_r = radius + (stroke_width * 0.5)
    r2 = eff_r * eff_r
    if not pts:
        return []
    if len(pts) == 1:
        dx = pts[0].x() - center.x()
        dy = pts[0].y() - center.y()
        if (dx * dx + dy * dy) <= r2:
            return []
        return [pts]

    dense_pts = [pts[0]]
    for i in range(len(pts) - 1):
        p1 = pts[i]
        p2 = pts[i + 1]
        dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        step = max(2.0, min(5.0, stroke_width * 0.5))
        n_steps = max(1, int(dist / step))
        for s in range(1, n_steps + 1):
            t = s / float(n_steps)
            dense_pts.append(QPointF(p1.x() + t * (p2.x() - p1.x()), p1.y() + t * (p2.y() - p1.y())))

    new_segments = []
    current_run = []
    for pt in dense_pts:
        dx = pt.x() - center.x()
        dy = pt.y() - center.y()
        if (dx * dx + dy * dy) > r2:
            current_run.append(pt)
        else:
            if current_run:
                new_segments.append(simplify_points(current_run))
                current_run = []
    if current_run:
        new_segments.append(simplify_points(current_run))

    return new_segments


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
            qimg = QImage(frame.data, w, h, ch * w, QImage.Format_BGR888).copy()
            self.video_cached_frame = qimg
            self.video_cached_idx = target_f
            if len(self.video_cache) >= self.MAX_VIDEO_CACHE:
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
        rel_time = max(0.0, float(elapsed_sec) - self.start_time)
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

    def close(self):
        if self.video_cap is not None:
            try:
                self.video_cap.release()
            except Exception:
                pass
            self.video_cap = None
        self.video_cache.clear()


class OverlayTrackRow(QFrame):
    def __init__(self, overlay, player, parent=None):
        super().__init__(parent)
        self.overlay = overlay
        self.player = player
        self.setObjectName("OverlayTrackRow")
        self.setStyleSheet("""
            QFrame#OverlayTrackRow {
                background-color: #1A1A24;
                border: 1px solid #2C2C3E;
                border-radius: 4px;
                padding: 1px 4px;
            }
            QFrame#OverlayTrackRow:hover {
                border-color: #007AFF;
            }
            QPushButton {
                background-color: #242434;
                border: 1px solid #38384C;
                border-radius: 3px;
                color: #E2E2EC;
                font-size: 10px;
                padding: 1px 4px;
                min-width: 0px;
            }
            QPushButton:hover {
                background-color: #323246;
                border-color: #007AFF;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #28283A;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00E5FF;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid #00E5FF;
                width: 10px;
                margin-top: -3px;
                margin-bottom: -3px;
                border-radius: 5px;
            }
        """)

        h_layout = QHBoxLayout(self)
        h_layout.setContentsMargins(4, 2, 4, 2)
        h_layout.setSpacing(6)

        # Icon & Name
        icon = "🎬" if overlay.obj_type == OverlayObject.TYPE_VIDEO else ("🎞️" if overlay.obj_type == OverlayObject.TYPE_GIF else "🔤")
        fname = os.path.basename(getattr(overlay, 'file_path', 'clip')) if getattr(overlay, 'file_path', None) else (overlay.text[:12] if hasattr(overlay, 'text') else 'Overlay')
        if len(fname) > 16:
            fname = fname[:13] + "..."
        self.lbl_title = QLabel(f"{icon} {fname}")
        self.lbl_title.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 11px;")
        self.lbl_title.setToolTip(getattr(overlay, 'file_path', ''))
        h_layout.addWidget(self.lbl_title)

        # Play/Pause toggle (for video)
        if overlay.obj_type == OverlayObject.TYPE_VIDEO:
            self.btn_play = QPushButton("❚❚" if getattr(overlay, 'is_playing', True) else "▶")
            self.btn_play.setToolTip("Play / Pause overlay video")
            self.btn_play.clicked.connect(self._toggle_play)
            h_layout.addWidget(self.btn_play)

        # Track Slider
        self.slider = ClickableSlider(Qt.Horizontal)
        self.dur = max(0.1, overlay.get_duration())
        self.slider.setRange(0, int(self.dur * 100))
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        h_layout.addWidget(self.slider)

        # Time label
        self.lbl_time = QLabel(f"00:00.0 / {self._fmt_time(self.dur)}")
        self.lbl_time.setStyleSheet("color: #8E8EA8; font-family: Consolas, monospace; font-size: 10px; min-width: 85px;")
        h_layout.addWidget(self.lbl_time)

        # Aspect ratio lock toggle
        self.btn_aspect = QPushButton("🔗" if getattr(overlay, 'keep_aspect_ratio', True) else "🔓")
        self.btn_aspect.setToolTip("Lock / Unlock Aspect Ratio")
        self.btn_aspect.clicked.connect(self._toggle_aspect)
        h_layout.addWidget(self.btn_aspect)

        # Visibility toggle
        self.btn_vis = QPushButton("👁️" if getattr(overlay, 'is_visible', True) else "🚫")
        self.btn_vis.setToolTip("Toggle Overlay Visibility")
        self.btn_vis.clicked.connect(self._toggle_visibility)
        h_layout.addWidget(self.btn_vis)

        # Settings dialog button
        if overlay.obj_type == OverlayObject.TYPE_VIDEO:
            self.btn_gear = QPushButton("⚙️")
            self.btn_gear.setToolTip("Video Overlay Settings")
            self.btn_gear.clicked.connect(self._open_settings)
            h_layout.addWidget(self.btn_gear)

        # Delete button
        self.btn_del = QPushButton("✖")
        self.btn_del.setStyleSheet("color: #FF453A;")
        self.btn_del.setToolTip("Delete Overlay Track")
        self.btn_del.clicked.connect(self._delete_overlay)
        h_layout.addWidget(self.btn_del)

        self._is_scrubbing = False

    def mousePressEvent(self, event):
        self.player.canvas.selected_overlay = self.overlay
        self.player.canvas.update()
        super().mousePressEvent(event)

    def _fmt_time(self, sec: float) -> str:
        m = int(sec // 60)
        s = sec % 60
        return f"{m:02d}:{s:04.1f}"

    def _toggle_play(self):
        if hasattr(self.overlay, 'is_playing'):
            self.overlay.is_playing = not getattr(self.overlay, 'is_playing', True)
            if hasattr(self, 'btn_play'):
                self.btn_play.setText("❚❚" if self.overlay.is_playing else "▶")
            self.player.canvas.update()

    def _toggle_aspect(self):
        cur = getattr(self.overlay, 'keep_aspect_ratio', True)
        self.overlay.keep_aspect_ratio = not cur
        self.btn_aspect.setText("🔗" if self.overlay.keep_aspect_ratio else "🔓")
        self.player.canvas.update()

    def _toggle_visibility(self):
        cur = getattr(self.overlay, 'is_visible', True)
        self.overlay.is_visible = not cur
        self.btn_vis.setText("👁️" if self.overlay.is_visible else "🚫")
        self.player.canvas.update()

    def _open_settings(self):
        dlg = VideoOverlaySettingsDialog(self.overlay, parent=self.player)
        dlg.exec_()
        self.player.canvas.update()

    def _delete_overlay(self):
        self.player.canvas.remove_overlay(self.overlay)

    def _on_slider_pressed(self):
        self._is_scrubbing = True

    def _on_slider_moved(self, val):
        sec = val / 100.0
        if self.overlay.obj_type == OverlayObject.TYPE_VIDEO:
            self.overlay.seek_to_seconds(sec)
        self.lbl_time.setText(f"{self._fmt_time(sec)} / {self._fmt_time(self.dur)}")
        self.player.canvas.update()

    def _on_slider_released(self):
        self._is_scrubbing = False
        val = self.slider.value()
        sec = val / 100.0
        if self.overlay.obj_type == OverlayObject.TYPE_VIDEO:
            self.overlay.seek_to_seconds(sec)
        self.player.canvas.update()

    def update_position(self, master_time: float):
        if self._is_scrubbing:
            return
        cur_t = self.overlay.get_current_time(master_time)
        val = int(cur_t * 100)
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.slider.blockSignals(False)
        self.lbl_time.setText(f"{self._fmt_time(cur_t)} / {self._fmt_time(self.dur)}")


class OverlayTrackContainer(QFrame):
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.setObjectName("OverlayTracksContainer")
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(0, 0, 0, 0)
        self.v_layout.setSpacing(2)
        self.rows = []
        self.hide()

    def refresh_tracks(self):
        for r in self.rows:
            self.v_layout.removeWidget(r)
            r.deleteLater()
        self.rows.clear()

        canvas = getattr(self.player, 'canvas', None)
        if canvas is None or not hasattr(canvas, 'overlays'):
            self.hide()
            return

        overlays = [ov for ov in canvas.overlays if ov.obj_type in (OverlayObject.TYPE_VIDEO, OverlayObject.TYPE_GIF)]
        if not overlays:
            self.hide()
            return

        self.show()
        for ov in overlays:
            row = OverlayTrackRow(ov, self.player, self)
            self.v_layout.addWidget(row)
            self.rows.append(row)

    def update_positions(self, master_time: float):
        if not self.isVisible():
            return
        for r in self.rows:
            r.update_position(master_time)


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

    def remove_overlay(self, ov):
        if ov in self.overlays:
            self.overlays.remove(ov)
            if self.recorder:
                self.recorder.record_overlay_remove(ov.obj_id)
            ov.close()
            if ov in self.selected_overlays:
                self.selected_overlays.remove(ov)
            p = self.window()
            if hasattr(p, '_refresh_overlay_tracks'):
                p._refresh_overlay_tracks()
            self.update()

    def remove_selected_overlays(self):
        for ov in list(self.selected_overlays):
            self.remove_overlay(ov)

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

    def set_bgr_frame(self, frame_bgr):
        self.set_frame(frame_bgr)

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
        elif action_type == 'modify':
            # payload: list of tuples (orig_stroke, replaced_strokes, orig_idx)
            for orig_stroke, replaced_strokes, orig_idx in reversed(payload):
                for r in replaced_strokes:
                    if r in self.strokes:
                        self.strokes.remove(r)
                idx = min(orig_idx, len(self.strokes))
                self.strokes.insert(idx, orig_stroke)
        elif action_type == 'clear':
            self.strokes = list(payload)

        if self.recorder:
            self.recorder.record_undo()

        self.update()
        self.drawing_changed.emit()

    def erase_strokes_at_video_pt(self, video_pt: QPointF):
        """
        Partial eraser: erases strokes like in MS Paint or Photoshop.
        Cuts out the circle of radius `eraser_radius` from intersecting strokes,
        splitting them into sub-segments rather than blindly deleting whole lines.
        """
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

        if undo_batch:
            self.undo_stack.append(('modify', undo_batch))

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
            for o in (list(self.selected_overlays) if self.selected_overlays else [ov]):
                self.remove_overlay(o)
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
                self.erase_strokes_at_video_pt(vpt)
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
                self.erase_strokes_at_video_pt(vpt)
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
                self._overlay_drag_mode = None
                self._drag_start_vpt = None
                self._drag_start_rect = None
                self._drag_start_rects = {}
                self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.active_tool == self.TOOL_SELECT and self.selected_overlays:
                self.remove_selected_overlays()
                return
        super().keyPressEvent(event)

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
                    painter.save()
                    if hasattr(ov, 'opacity') and ov.opacity < 1.0:
                        painter.setOpacity(ov.opacity)
                    painter.drawImage(ov.rect, ov_img)
                    painter.restore()

                if ov == self.hover_overlay and ov not in self.selected_overlays and self.active_tool == self.TOOL_SELECT:
                    painter.save()
                    hover_pen = QPen(QColor(0, 229, 255, 120), 1.5 / self.zoom_factor, Qt.DashLine)
                    painter.setPen(hover_pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(ov.rect)
                    painter.restore()

                if ov in self.selected_overlays and self.active_tool == self.TOOL_SELECT:
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

            if self.active_tool == self.TOOL_SELECT and self._is_rubber_banding and self._rubber_band_rect:
                painter.save()
                rb = self._rubber_band_rect.normalized()
                painter.setBrush(QBrush(QColor(0, 122, 255, 45)))
                painter.setPen(QPen(QColor(0, 229, 255, 220), 1.5 / self.zoom_factor, Qt.DashLine))
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
        entry = {
            'type': 'overlay_add',
            'time': self.current_time(),
            'obj_id': overlay.obj_id,
            'obj_type': overlay.obj_type,
            'file_path': overlay.file_path,
            'rect': [round(overlay.rect.x(), 2), round(overlay.rect.y(), 2),
                     round(overlay.rect.width(), 2), round(overlay.rect.height(), 2)]
        }
        if overlay.obj_type == OverlayObject.TYPE_TEXT:
            entry['text_data'] = {
                'text': overlay.text,
                'font_family': overlay.font_family,
                'font_size': overlay.font_size,
                'bold': overlay.font_bold,
                'italic': overlay.font_italic,
                'color': overlay.text_color,
                'bg_color': overlay.bg_color
            }
        self.events.append(entry)

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

    def __init__(self, video_path, events, total_duration, output_path, fps=30.0, out_size=None,
                 codec='libx264', bitrate='10M', rate_control='vbr', audio_path=None):
        super().__init__()
        self.video_path = video_path
        self.events = events
        self.total_duration = total_duration
        self.output_path = output_path
        self.fps = max(10.0, min(120.0, fps))
        self.out_size = out_size
        self.codec = codec or 'libx264'
        self.bitrate = bitrate or '10M'
        self.rate_control = (rate_control or 'vbr').lower()
        self.audio_path = audio_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self._do_run()
        except Exception as e:
            self.finished.emit(False, f"Export error: {e}")

    def _do_run(self):
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

        if self.out_size and len(self.out_size) == 2 and self.out_size[0] > 0 and self.out_size[1] > 0:
            out_w, out_h = self.out_size
        else:
            out_w, out_h = src_w, src_h

        out_w = (out_w // 2) * 2
        out_h = (out_h // 2) * 2

        use_pyav = True
        container = None
        stream = None
        cv_writer = None
        active_overlays = collections.OrderedDict()

        codec_name = self.codec.lower()
        if codec_name in ('x264', 'libx264', 'h264'):
            pyav_codec = 'h264'
        elif codec_name in ('hevc', 'x265', 'libx265', 'h265'):
            pyav_codec = 'hevc'
        elif codec_name in ('vp9', 'libvpx-vp9'):
            pyav_codec = 'vp9'
        elif codec_name in ('prores', 'prores_ks'):
            pyav_codec = 'prores'
        else:
            pyav_codec = 'h264'

        try:
            container = av.open(self.output_path, mode='w')
            stream = container.add_stream(pyav_codec, rate=int(round(self.fps)))
            stream.width = out_w
            stream.height = out_h
            stream.pix_fmt = 'yuv420p'
            def parse_bitrate_to_bps(b) -> int:
                s = str(b).strip().upper()
                if s.endswith('M'):
                    try:
                        return int(float(s[:-1]) * 1_000_000)
                    except Exception:
                        pass
                elif s.endswith('K'):
                    try:
                        return int(float(s[:-1]) * 1_000)
                    except Exception:
                        pass
                try:
                    return int(float(s))
                except Exception:
                    return 10_000_000

            b_val = parse_bitrate_to_bps(self.bitrate)
            is_cbr = (self.rate_control == 'cbr')

            if pyav_codec == 'h264':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'preset': 'veryfast',
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val),
                        'bufsize': str(b_val * 2)
                    }
                else:
                    stream.options = {
                        'preset': 'veryfast',
                        'crf': '20',
                        'maxrate': str(int(b_val * 1.5)),
                        'bufsize': str(b_val * 2)
                    }
            elif pyav_codec == 'hevc':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'preset': 'veryfast',
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val),
                        'bufsize': str(b_val * 2)
                    }
                else:
                    stream.options = {
                        'preset': 'veryfast',
                        'crf': '23',
                        'maxrate': str(int(b_val * 1.5)),
                        'bufsize': str(b_val * 2)
                    }
            elif pyav_codec == 'vp9':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val)
                    }
                else:
                    stream.options = {'crf': '28', 'b': str(b_val)}
            elif pyav_codec == 'prores':
                stream.options = {'profile': '3'}
                stream.pix_fmt = 'yuv422p10le'
            else:
                stream.bit_rate = b_val
        except Exception:
            use_pyav = False
            if container:
                try:
                    container.close()
                except Exception:
                    pass
                container = None
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
                    t_data = ev.get('text_data')
                    active_overlays[oid] = OverlayObject(oid, fpath, r, start_time=ev['time'], text_data=t_data)
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
                    painter.save()
                    if hasattr(ov, 'opacity') and ov.opacity < 1.0:
                        painter.setOpacity(ov.opacity)
                    painter.drawImage(ov.rect, ov_img)
                    painter.restore()

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

            bpl = render_img.bytesPerLine()
            ptr = render_img.constBits()
            ptr.setsize(bpl * out_h)
            raw = np.frombuffer(ptr, np.uint8).reshape((out_h, bpl))
            rgba = raw[:, :out_w * 4].reshape((out_h, out_w, 4))
            bgr_out = cv2.cvtColor(rgba, cv2.COLOR_BGRA2BGR)

            if use_pyav and container is not None and stream is not None:
                try:
                    av_frame = av.VideoFrame.from_ndarray(bgr_out, format='bgr24')
                    for packet in stream.encode(av_frame):
                        container.mux(packet)
                except Exception:
                    use_pyav = False
                    if container:
                        try:
                            container.close()
                        except Exception:
                            pass
                        container = None
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    cv_writer = cv2.VideoWriter(self.output_path, fourcc, self.fps, (out_w, out_h))
                    if cv_writer.isOpened():
                        cv_writer.write(bgr_out)
            elif cv_writer is not None:
                cv_writer.write(bgr_out)

            if frame_num % 10 == 0 or frame_num == total_frames - 1:
                self.progress.emit(frame_num + 1, total_frames, f"Exporting: {frame_num + 1}/{total_frames} frames")

        for ov in active_overlays.values():
            try:
                ov.close()
            except Exception:
                pass
        active_overlays.clear()

        if cap:
            try:
                cap.release()
            except Exception:
                pass

        if use_pyav and container is not None and stream is not None:
            if not self._is_cancelled:
                try:
                    for packet in stream.encode(None):
                        container.mux(packet)
                except Exception:
                    pass
            try:
                container.close()
            except Exception:
                pass
            container = None
        elif cv_writer is not None:
            try:
                cv_writer.release()
            except Exception:
                pass
            cv_writer = None

        # Audio commentary muxing via ffmpeg
        if not self._is_cancelled and self.audio_path and os.path.exists(self.audio_path) and os.path.getsize(self.audio_path) > 100:
            import subprocess
            ffmpeg_exe = get_ffmpeg_path()
            if ffmpeg_exe and os.path.exists(ffmpeg_exe):
                temp_mux = self.output_path + ".muxed" + os.path.splitext(self.output_path)[1]
                cmd = [
                    ffmpeg_exe, "-y",
                    "-i", self.output_path,
                    "-i", self.audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-shortest",
                    temp_mux
                ]
                flags = 0x08000000 if os.name == 'nt' else 0
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                if res.returncode == 0 and os.path.exists(temp_mux) and os.path.getsize(temp_mux) > 1000:
                    try:
                        os.replace(temp_mux, self.output_path)
                    except Exception:
                        pass

        if self._is_cancelled:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass
            self.finished.emit(False, "Export cancelled.")
        else:
            self.finished.emit(True, self.output_path)


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


class FKVideoPlayer(QMainWindow):
    MAX_CACHE_FRAMES = 120

    def __init__(self, initial_video_path=None):
        super().__init__()
        self.setWindowTitle("FKVideoPlayer")
        self.resize(1180, 800)
        self.setMinimumSize(920, 540)
        self.setAcceptDrops(True)

        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.projects = []
        self._fallback_canvas = VideoCanvas(None)
        self._fallback_canvas.hide()
        self._fallback_recorder = ActionRecorder(self)
        self._fallback_cache = collections.OrderedDict()

        self.audio_player = QMediaPlayer(self, QMediaPlayer.LowLatency)
        self.current_volume = 80
        self.is_muted = False
        self.audio_player.setVolume(self.current_volume)

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

        self.rec_update_timer = QTimer(self)
        self.rec_update_timer.timeout.connect(self._on_rec_update_timer)
        self.export_worker = None

        self.overlay_anim_timer = QTimer(self)
        self.overlay_anim_timer.timeout.connect(self._on_overlay_anim_tick)
        self.overlay_anim_timer.start(33)

        self.mic_recorder = MicrophoneRecorder(parent=self)
        self.is_mic_enabled = False
        self.last_mic_wav = None
        self.selected_mic_device = None

        self.sys_audio_recorder = SystemAudioRecorder(parent=self)
        self.last_sys_wav = None
        self.temp_sys_wav_path = None

        self.temp_capture_writer = None
        self.temp_capture_video_path = None

        self._init_ui()
        self._apply_dark_theme()
        self._setup_shortcuts()

        self.new_project_tab(name="Project 1", project_type="empty")

        if initial_video_path and os.path.exists(initial_video_path):
            self.load_video(initial_video_path)

        QTimer.singleShot(2500, self._check_updates_background)

    @property
    def active_project(self):
        if hasattr(self, 'projects') and self.projects:
            idx = self.project_tabs.currentIndex() if hasattr(self, 'project_tabs') else 0
            if 0 <= idx < len(self.projects):
                return self.projects[idx]
            if len(self.projects) > 0:
                return self.projects[0]
        return getattr(self, '_fallback_project', None)

    @property
    def canvas(self):
        proj = self.active_project
        return proj.canvas if proj else self._fallback_canvas

    @property
    def recorder(self):
        proj = self.active_project
        return proj.recorder if proj else self._fallback_recorder

    @recorder.setter
    def recorder(self, val):
        proj = self.active_project
        if proj:
            proj.recorder = val

    @property
    def cap(self):
        proj = self.active_project
        return proj.cap if proj else None

    @cap.setter
    def cap(self, val):
        proj = self.active_project
        if proj:
            proj.cap = val

    @property
    def video_path(self):
        proj = self.active_project
        return proj.video_path if proj else ""

    @video_path.setter
    def video_path(self, val):
        proj = self.active_project
        if proj:
            proj.video_path = val

    @property
    def fps(self):
        proj = self.active_project
        return proj.fps if proj else 24.0

    @fps.setter
    def fps(self, val):
        proj = self.active_project
        if proj:
            proj.fps = val

    @property
    def total_frames(self):
        proj = self.active_project
        return proj.total_frames if proj else 0

    @total_frames.setter
    def total_frames(self, val):
        proj = self.active_project
        if proj:
            proj.total_frames = val

    @property
    def current_frame_idx(self):
        proj = self.active_project
        return proj.current_frame_idx if proj else 0

    @current_frame_idx.setter
    def current_frame_idx(self, val):
        proj = self.active_project
        if proj:
            proj.current_frame_idx = val

    @property
    def is_playing(self):
        proj = self.active_project
        return proj.is_playing if proj else False

    @is_playing.setter
    def is_playing(self, val):
        proj = self.active_project
        if proj:
            proj.is_playing = val

    @property
    def is_looping(self):
        proj = self.active_project
        return proj.is_looping if proj else True

    @is_looping.setter
    def is_looping(self, val):
        proj = self.active_project
        if proj:
            proj.is_looping = val

    @property
    def playback_speed(self):
        proj = self.active_project
        return proj.playback_speed if proj else 1.0

    @playback_speed.setter
    def playback_speed(self, val):
        proj = self.active_project
        if proj:
            proj.playback_speed = val

    @property
    def frames_per_tick(self):
        proj = self.active_project
        return proj.frames_per_tick if proj else 1

    @frames_per_tick.setter
    def frames_per_tick(self, val):
        proj = self.active_project
        if proj:
            proj.frames_per_tick = val

    @property
    def has_audio(self):
        proj = self.active_project
        return proj.has_audio if proj else False

    @has_audio.setter
    def has_audio(self, val):
        proj = self.active_project
        if proj:
            proj.has_audio = val

    @property
    def temp_audio_path(self):
        proj = self.active_project
        return proj.temp_audio_path if proj else None

    @temp_audio_path.setter
    def temp_audio_path(self, val):
        proj = self.active_project
        if proj:
            proj.temp_audio_path = val

    @property
    def frame_cache(self):
        proj = self.active_project
        return proj.frame_cache if proj else self._fallback_cache

    @property
    def _cap_pos(self):
        proj = self.active_project
        return proj._cap_pos if proj else -1

    @_cap_pos.setter
    def _cap_pos(self, val):
        proj = self.active_project
        if proj:
            proj._cap_pos = val

    @property
    def window_capture_worker(self):
        proj = self.active_project
        return proj.window_capture_worker if proj else None

    @window_capture_worker.setter
    def window_capture_worker(self, val):
        proj = self.active_project
        if proj:
            proj.window_capture_worker = val

    @property
    def is_capturing_window(self):
        proj = self.active_project
        return proj.is_capturing_window if proj else False

    @is_capturing_window.setter
    def is_capturing_window(self, val):
        proj = self.active_project
        if proj:
            proj.is_capturing_window = val

    @property
    def captured_window_hwnd(self):
        proj = self.active_project
        return proj.captured_window_hwnd if proj else None

    @captured_window_hwnd.setter
    def captured_window_hwnd(self, val):
        proj = self.active_project
        if proj:
            proj.captured_window_hwnd = val

    @property
    def captured_window_title(self):
        proj = self.active_project
        return proj.captured_window_title if proj else ""

    @captured_window_title.setter
    def captured_window_title(self, val):
        proj = self.active_project
        if proj:
            proj.captured_window_title = val

    @property
    def temp_capture_writer(self):
        proj = self.active_project
        return proj.temp_capture_writer if proj else None

    @temp_capture_writer.setter
    def temp_capture_writer(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_writer = val

    @property
    def temp_capture_writer_size(self):
        proj = self.active_project
        return proj.temp_capture_writer_size if proj else None

    @temp_capture_writer_size.setter
    def temp_capture_writer_size(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_writer_size = val

    @property
    def temp_capture_video_path(self):
        proj = self.active_project
        return proj.temp_capture_video_path if proj else None

    @temp_capture_video_path.setter
    def temp_capture_video_path(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_video_path = val

    @property
    def _capture_frame_count(self):
        proj = self.active_project
        return proj._capture_frame_count if proj else 0

    @_capture_frame_count.setter
    def _capture_frame_count(self, val):
        proj = self.active_project
        if proj:
            proj._capture_frame_count = val

    def showEvent(self, event):
        super().showEvent(event)
        set_dark_titlebar(self)
        if not getattr(self, '_welcome_checked', False):
            self._welcome_checked = True
            from PyQt5.QtCore import QSettings
            settings = QSettings("furrykit", "FKVideoPlayer")
            if settings.value("show_welcome", True, type=bool):
                QTimer.singleShot(250, self.open_welcome_dialog)

    def open_welcome_dialog(self):
        from settings_dialogs import WelcomeDialog
        dlg = WelcomeDialog(parent=self)
        dlg.exec_()

    def _init_ui(self):
        self._create_menu_bar()
        I18nManager.instance().add_listener(self.update_ui_texts)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        self.project_tabs = QTabWidget(self)
        self.project_tabs.setObjectName("ProjectTabs")
        self.project_tabs.setTabBar(AutoAdjustTabBar(self.project_tabs, extra_padding=36, min_tab_width=110))
        self.project_tabs.setTabsClosable(True)
        self.project_tabs.setMovable(True)
        self.project_tabs.setDocumentMode(True)
        self.project_tabs.setUsesScrollButtons(True)
        self.project_tabs.setElideMode(Qt.ElideRight)
        self.project_tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.project_tabs.currentChanged.connect(self._on_tab_changed)

        btn_add_tab = QToolButton(self.project_tabs)
        btn_add_tab.setText("+")
        btn_add_tab.setToolTip("New Project Tab (Ctrl+T)")
        btn_add_tab.setCursor(Qt.PointingHandCursor)
        btn_add_tab.setStyleSheet("""
            QToolButton {
                background-color: #1A1A24;
                color: #00E5FF;
                font-size: 15px;
                font-weight: bold;
                border: 1px solid #2C2C3E;
                border-radius: 4px;
                padding: 2px 8px;
                margin: 2px 4px;
            }
            QToolButton:hover {
                background-color: #007AFF;
                color: #FFFFFF;
                border-color: #007AFF;
            }
        """)
        btn_add_tab.clicked.connect(lambda: self.new_project_tab())
        self.project_tabs.setCornerWidget(btn_add_tab, Qt.TopRightCorner)

        self._create_top_toolbar()
        self._create_recording_bar()
        main_layout.addWidget(self.top_toolbar)
        main_layout.addWidget(self.recording_bar)
        main_layout.addWidget(self.project_tabs, stretch=1)

        bottom_panel = self._create_bottom_controls()
        main_layout.addWidget(bottom_panel)

    def _create_top_toolbar(self):
        self.top_toolbar = QFrame()
        self.top_toolbar.setObjectName("TopToolbar")
        layout = QHBoxLayout(self.top_toolbar)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        self.btn_new_canvas = QPushButton("📄 New")
        self.btn_new_canvas.setToolTip("Create blank canvas project (Ctrl+N)")
        self.btn_new_canvas.clicked.connect(self.open_new_canvas_dialog)
        layout.addWidget(self.btn_new_canvas)

        self.btn_open = QPushButton("📂 Open")
        self.btn_open.setToolTip("Open video file (Ctrl+O)")
        self.btn_open.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.btn_open)

        self.btn_capture_win = QPushButton("🪟 Stream")
        self.btn_capture_win.setToolTip("Capture live window or stream (Ctrl+W)")
        self.btn_capture_win.clicked.connect(self.open_window_capture_dialog)
        layout.addWidget(self.btn_capture_win)

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

        self.btn_tool_text = QPushButton("🔤 Text")
        self.btn_tool_text.setToolTip("Add text overlay (T)")
        self.btn_tool_text.setCheckable(True)
        self.btn_tool_text.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_TEXT))
        layout.addWidget(self.btn_tool_text)

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

        self.lbl_tool_size = QLabel("Brush Size:")
        layout.addWidget(self.lbl_tool_size)
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 100)
        self.spin_width.setValue(4)
        self.spin_width.setSuffix(" px")
        self.spin_width.setToolTip("Brush / Eraser size ([ and ])")
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

        layout.addStretch(1)

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
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

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

        self.btn_mic_toggle = QPushButton("🎤 Mic: OFF")
        self.btn_mic_toggle.setObjectName("BtnMicToggle")
        self.btn_mic_toggle.setStyleSheet("background-color: #381A1A; color: #FF3B30; border: 1px solid #FF3B30;")
        self.btn_mic_toggle.setToolTip("Toggle microphone commentary recording")
        self.btn_mic_toggle.clicked.connect(self._toggle_microphone)
        layout.addWidget(self.btn_mic_toggle)

        self._add_separator(layout)

        self.btn_rec_export = QPushButton("💾 Export...")
        self.btn_rec_export.setObjectName("BtnExportRec")
        self.btn_rec_export.setToolTip("Export recorded actions to MP4 video (Ctrl+E)")
        self.btn_rec_export.setEnabled(False)
        self.btn_rec_export.clicked.connect(self.export_recorded_video)
        layout.addWidget(self.btn_rec_export)

        self.btn_rec_save = QPushButton("📥 Save")
        self.btn_rec_save.setToolTip("Save actions session to JSON")
        self.btn_rec_save.setEnabled(False)
        self.btn_rec_save.clicked.connect(self.save_actions_json)
        layout.addWidget(self.btn_rec_save)

        self.btn_rec_load = QPushButton("📤 Load")
        self.btn_rec_load.setToolTip("Load actions session from JSON")
        self.btn_rec_load.clicked.connect(self.load_actions_json)
        layout.addWidget(self.btn_rec_load)

        self._add_separator(layout)

        self.btn_add_overlay = QPushButton("🖼️ Add Overlay...")
        self.btn_add_overlay.setToolTip("Add image, GIF, or secondary video overlay (Ctrl+I)")
        self.btn_add_overlay.clicked.connect(lambda: self.add_overlay_dialog(None))
        layout.addWidget(self.btn_add_overlay)

        self._add_separator(layout)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(38)
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

        layout.addStretch(1)

        self.lbl_rec_status = QLabel("● Recording idle")
        self.lbl_rec_status.setObjectName("RecStatus")
        self.lbl_rec_status.setStyleSheet("color: #7E7E94; font-family: Consolas, monospace;")
        layout.addWidget(self.lbl_rec_status)

    def _create_bottom_controls(self):
        panel = QFrame()
        panel.setObjectName("BottomPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)

        # Multi-Track Container for Overlay Videos / GIFs (positioned above base video track)
        self.overlay_tracks_container = OverlayTrackContainer(self)
        layout.addWidget(self.overlay_tracks_container)

        self.timeline_slider = ClickableSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.wheel_scrolled.connect(self._on_slider_wheel)
        layout.addWidget(self.timeline_slider)

        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(3)

        self.btn_rewind_5s = QPushButton("-5s")
        self.btn_rewind_5s.setToolTip("Rewind 5 seconds (J or Ctrl+Left)")
        self.btn_rewind_5s.clicked.connect(lambda: self.seek_seconds(-5.0))
        ctrl_layout.addWidget(self.btn_rewind_5s)

        self.btn_rewind_1s = QPushButton("-1s")
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

        self.btn_forward_1s = QPushButton("+1s")
        self.btn_forward_1s.setToolTip("Forward 1 second (Shift+Right)")
        self.btn_forward_1s.clicked.connect(lambda: self.seek_seconds(1.0))
        ctrl_layout.addWidget(self.btn_forward_1s)

        self.btn_forward_5s = QPushButton("+5s")
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
        self.btn_mute.setFixedSize(24, 22)
        self.btn_mute.setToolTip("Toggle mute (M)")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl_layout.addWidget(self.btn_mute)

        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(self.current_volume)
        self.slider_volume.setFixedWidth(65)
        self.slider_volume.setFocusPolicy(Qt.NoFocus)
        self.slider_volume.setToolTip("Volume (Up / Down)")
        self.slider_volume.valueChanged.connect(self.set_volume)
        ctrl_layout.addWidget(self.slider_volume)

        self.lbl_volume = QLabel(f"{self.current_volume}%")
        self.lbl_volume.setMinimumWidth(28)
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
        self.lbl_time_info.setStyleSheet("font-family: Consolas, monospace; font-size: 11px; color: #9EABB8;")
        ctrl_layout.addWidget(self.lbl_time_info)

        layout.addLayout(ctrl_layout)
        return panel

    def _setup_shortcuts(self):
        if hasattr(self, '_active_shortcuts'):
            for sc in self._active_shortcuts:
                sc.setEnabled(False)
        self._active_shortcuts = []

        cfg_hotkeys = dict(DEFAULT_HOTKEYS)
        try:
            if os.path.exists(HOTKEYS_CONFIG_PATH):
                with open(HOTKEYS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg_hotkeys.update(json.load(f))
        except Exception:
            pass

        def reg_sc(key_seq, callback):
            if key_seq:
                try:
                    sc = QShortcut(QKeySequence(key_seq), self, callback)
                    self._active_shortcuts.append(sc)
                except Exception:
                    pass

        reg_sc(cfg_hotkeys.get("play_pause", "Space"), self.toggle_play_pause)
        reg_sc("K", self.toggle_play_pause)
        reg_sc(cfg_hotkeys.get("step_prev", "Left"), lambda: self.step_frame(-1))
        reg_sc(cfg_hotkeys.get("step_next", "Right"), lambda: self.step_frame(1))
        reg_sc(cfg_hotkeys.get("skip_back_1s", "J"), lambda: self.seek_seconds(-1.0))
        reg_sc(cfg_hotkeys.get("skip_fwd_1s", "L"), lambda: self.seek_seconds(1.0))

        reg_sc("Shift+Left", lambda: self.seek_seconds(-1.0))
        reg_sc("Shift+Right", lambda: self.seek_seconds(1.0))
        reg_sc("Ctrl+Left", lambda: self.seek_seconds(-5.0))
        reg_sc("Ctrl+Right", lambda: self.seek_seconds(5.0))

        reg_sc(Qt.Key_Home, lambda: self._seek_to_frame(0))
        reg_sc(Qt.Key_End, lambda: self._seek_to_frame(self.total_frames - 1))

        reg_sc("M", self.toggle_mute)
        reg_sc(Qt.Key_Up, lambda: self.adjust_volume(5))
        reg_sc(Qt.Key_Down, lambda: self.adjust_volume(-5))

        reg_sc(cfg_hotkeys.get("undo", "Ctrl+Z"), self.canvas.undo_last_action)
        reg_sc(cfg_hotkeys.get("delete_overlay", "Delete"), self._on_delete_shortcut)
        reg_sc(Qt.Key_Backspace, self._on_delete_shortcut)
        reg_sc(cfg_hotkeys.get("duplicate_overlay", "Ctrl+D"), self._on_duplicate_shortcut)
        reg_sc("C", self.canvas.clear_all_drawings)

        reg_sc("[", lambda: self.spin_width.setValue(self.spin_width.value() - 1))
        reg_sc("]", lambda: self.spin_width.setValue(self.spin_width.value() + 1))

        reg_sc("<", self.decrease_speed)
        reg_sc(">", self.increase_speed)
        reg_sc("Shift+,", self.decrease_speed)
        reg_sc("Shift+.", self.increase_speed)
        reg_sc("R", self.reset_speed)

        reg_sc("+", self.canvas.zoom_in)
        reg_sc("=", self.canvas.zoom_in)
        reg_sc("-", self.canvas.zoom_out)
        reg_sc("0", self.canvas.fit_to_view)

        reg_sc(cfg_hotkeys.get("tool_brush", "P"), lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        reg_sc("B", lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        reg_sc(cfg_hotkeys.get("tool_eraser", "E"), lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        reg_sc("H", lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        reg_sc(cfg_hotkeys.get("tool_select", "V"), lambda: self._select_tool(VideoCanvas.TOOL_SELECT))
        reg_sc(cfg_hotkeys.get("tool_text", "T"), lambda: self._select_tool(VideoCanvas.TOOL_TEXT))

        reg_sc(cfg_hotkeys.get("open_video", "Ctrl+O"), self.open_file_dialog)
        reg_sc("O", self.open_file_dialog)
        reg_sc(cfg_hotkeys.get("new_canvas", "Ctrl+N"), self.open_new_canvas_dialog)
        reg_sc(cfg_hotkeys.get("capture_window", "Ctrl+Shift+W"), self.open_window_capture_dialog)
        reg_sc("Ctrl+I", self.add_overlay_dialog)

        reg_sc("Ctrl+T", lambda: self.new_project_tab())
        reg_sc("Ctrl+W", self.close_current_tab)
        reg_sc("Ctrl+Tab", self.next_project_tab)
        reg_sc("Ctrl+Shift+Tab", self.prev_project_tab)

        reg_sc(cfg_hotkeys.get("record_toggle", "Ctrl+R"), self._shortcut_toggle_record)
        reg_sc("Ctrl+Shift+P", self.pause_actions_record)
        reg_sc(cfg_hotkeys.get("export_video", "Ctrl+E"), self.export_recorded_video)

        reg_sc("PageUp", lambda: self.bring_overlay_forward(self.canvas.selected_overlay))
        reg_sc("PageDown", lambda: self.send_overlay_backward(self.canvas.selected_overlay))
        reg_sc("Shift+PageUp", lambda: self.bring_overlay_to_front(self.canvas.selected_overlay))
        reg_sc("Shift+PageDown", lambda: self.send_overlay_to_back(self.canvas.selected_overlay))

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

        if self.recorder.is_active():
            self.recorder.record_overlay_add(overlay)

        self._select_tool(VideoCanvas.TOOL_SELECT)
        self._refresh_overlay_tracks()
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

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.webm *.flv *.m4v);;All Files (*.*)"
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path, in_new_tab=None):
        if in_new_tab or (in_new_tab is None and self.active_project and not self.active_project.is_empty()):
            self.new_project_tab(name=os.path.basename(file_path), project_type="video")

        if self.cap is not None:
            self.cap.release()
            self.pause()

        self.cap = cv2.VideoCapture(file_path)
        if not self.cap.isOpened():
            logger.error(f"Failed to open video file with OpenCV: {file_path}")
            QMessageBox.critical(self, "Error", f"Failed to open video file:\n{file_path}")
            return

        self.video_path = file_path
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        if self.fps <= 1.0 or np.isnan(self.fps):
            self.fps = 25.0
        logger.info(f"Loaded video: {file_path} (frames={self.total_frames}, fps={self.fps:.2f})")

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
            cur_sec = 0.0
            self.lbl_time_info.setText("00:00.00 / 00:00.00  |  Frame: 0 / 0  (0.0 FPS)")

        if hasattr(self, 'overlay_tracks_container') and self.overlay_tracks_container is not None:
            self.overlay_tracks_container.update_positions(cur_sec)

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

    def _toggle_microphone(self):
        self.is_mic_enabled = not self.is_mic_enabled
        if self.is_mic_enabled:
            self.btn_mic_toggle.setText("🎤 Mic: ON")
            self.btn_mic_toggle.setStyleSheet("background-color: #1A3824; color: #34C759; border: 1px solid #34C759;")
        else:
            self.btn_mic_toggle.setText("🎤 Mic: OFF")
            self.btn_mic_toggle.setStyleSheet("background-color: #381A1A; color: #FF3B30; border: 1px solid #FF3B30;")

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

    def export_recorded_video(self):
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
            audio_path=audio_target
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
        self.canvas.active_tool = VideoCanvas.TOOL_SELECT
        self._select_tool(VideoCanvas.TOOL_SELECT)
        if self.recorder.is_active():
            self.recorder.record_overlay_add(ov)
        self.canvas.update()
        self._refresh_overlay_tracks()

    def open_new_canvas_dialog(self):
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

    def open_window_capture_dialog(self):
        dlg = WindowCaptureDialog(parent=self)
        if dlg.exec_() == 1 and dlg.selected_hwnd:
            self.start_window_capture(dlg.selected_hwnd, dlg.selected_title)

    def start_window_capture(self, hwnd: int, title: str):
        if self.active_project and not self.active_project.is_empty():
            self.new_project_tab(name=f"Stream: {title[:16]}", project_type="window")

        self._stop_window_capture()
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        self.is_capturing_window = True
        self.captured_window_hwnd = hwnd
        self.captured_window_title = title
        self.total_frames = 0
        self.current_frame_idx = 0
        self.fps = 30.0
        self.video_path = ""
        tab_name = f"Stream: {title[:16]}"
        if self.active_project:
            self.active_project.name = tab_name
            self.active_project.project_type = 'window'
        if hasattr(self, 'project_tabs'):
            cur_idx = self.project_tabs.currentIndex()
            if cur_idx >= 0:
                self.project_tabs.setTabText(cur_idx, tab_name)

        self.setWindowTitle(f"FKVideoPlayer — Live Capture: {title}")
        if hasattr(self, 'lbl_time_info'):
            self.lbl_time_info.setText(f"Live Window: {title}")

        self.window_capture_worker = WindowCaptureWorker(hwnd, target_fps=30.0, parent=self)
        self.window_capture_worker.frame_captured.connect(self._on_captured_window_frame)
        self.window_capture_worker.start()

        ProjectManager.instance().add_project('window', f'Stream: {title}', str(hwnd))
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

    def open_preferences_dialog(self):
        dlg = PreferencesDialog(parent=self)
        dlg.hotkeys_updated.connect(self._setup_shortcuts)
        dlg.language_changed.connect(lambda _: self.update_ui_texts())
        dlg.exec_()

    def open_video_properties_dialog(self):
        QMessageBox.information(
            self,
            "Video & Canvas Properties",
            f"Source: {self.video_path or 'Blank Canvas / Stream'}\n"
            f"Resolution: {self.canvas.video_width}x{self.canvas.video_height}\n"
            f"FPS: {self.fps:.2f}\n"
            f"Total Frames: {self.total_frames}"
        )

    def open_export_presets_dialog(self):
        dlg = ExportDialog(default_w=self.canvas.video_width, default_h=self.canvas.video_height, parent=self)
        dlg.exec_()

    def open_about_dialog(self):
        dlg = AboutDialog(parent=self)
        dlg.exec_()

    def open_updates_dialog(self):
        dlg = UpdatesDialog(parent=self)
        dlg.exec_()

    def _check_updates_background(self):
        try:
            from settings_dialogs import GitHubUpdateCheckerWorker
            self._update_worker = GitHubUpdateCheckerWorker(current_version=APP_VERSION, parent=self)
            self._update_worker.finished.connect(self._on_update_check_bg_finished)
            self._update_worker.start()
        except Exception:
            pass

    def _on_update_check_bg_finished(self, info: dict):
        if info.get("is_newer"):
            tag = info.get("tag_name", "")
            ans = QMessageBox.question(
                self,
                tr('updates_available'),
                f"{tr('updates_available')}\n\nVersion: {tag}\n\n{tr('btn_download_update')}?",
                QMessageBox.Yes | QMessageBox.No
            )
            if ans == QMessageBox.Yes:
                self.open_updates_dialog()

    def open_donate(self):
        from settings_dialogs import DONATEPAY_URL
        import webbrowser
        webbrowser.open(DONATEPAY_URL)

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

    def closeEvent(self, event):
        for i, proj in enumerate(list(self.projects)):
            if proj.has_unsaved_changes():
                self.project_tabs.setCurrentIndex(i)
                if not self.prompt_unsaved_changes(project=proj):
                    event.ignore()
                    return
        for proj in self.projects:
            proj.close()

        # Clean up session temporary audio/video files
        for p in [getattr(self, 'temp_mic_wav_path', None),
                  getattr(self, 'temp_sys_wav_path', None),
                  getattr(self, 'temp_capture_video_path', None),
                  getattr(self, 'last_mic_wav', None),
                  getattr(self, 'last_sys_wav', None)]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        event.accept()

    def _create_menu_bar(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #14141C;
                color: #C8C8DC;
                border-bottom: 1px solid #282838;
                padding: 2px 6px;
                font-size: 12px;
            }
            QMenuBar::item {
                background: transparent;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background-color: #242436;
                color: #FFFFFF;
            }
            QMenu {
                background-color: #1C1C28;
                color: #E2E2EC;
                border: 1px solid #323246;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #007AFF;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #323246;
                margin: 4px 6px;
            }
        """)

        # File Menu
        self.menu_file = menubar.addMenu(tr('menu_file'))
        self.menu_file.addAction("New Project Tab", lambda: self.new_project_tab(), QKeySequence("Ctrl+T"))
        self.menu_file.addAction(tr('act_new_canvas'), self.open_new_canvas_dialog, QKeySequence("Ctrl+N"))
        self.menu_file.addAction(tr('act_open_video'), self.open_file_dialog, QKeySequence("Ctrl+O"))
        self.menu_file.addAction(tr('act_capture_window'), self.open_window_capture_dialog, QKeySequence("Ctrl+Shift+W"))
        self.menu_file.addAction(tr('act_close_project'), self.close_project, QKeySequence("Ctrl+W"))

        self.menu_recent = self.menu_file.addMenu(tr('act_recent_projects'))
        self._refresh_recent_projects_menu()

        self.menu_file.addSeparator()
        self.menu_file.addAction(tr('act_save_actions'), self.save_actions_json, QKeySequence("Ctrl+S"))
        self.menu_file.addAction(tr('act_load_actions'), self.load_actions_json)
        self.menu_file.addSeparator()
        self.menu_file.addAction(tr('act_exit'), self.close, QKeySequence("Ctrl+Q"))

        # Settings Menu
        self.menu_settings = menubar.addMenu(tr('menu_settings'))
        self.menu_settings.addAction(tr('act_preferences'), self.open_preferences_dialog, QKeySequence("F2"))

        # Video Settings Menu
        self.menu_video_settings = menubar.addMenu(tr('menu_video_settings'))
        self.menu_video_settings.addAction(tr('act_video_props'), self.open_video_properties_dialog)
        self.menu_video_settings.addAction("Mute / Unmute Audio", self.toggle_mute, QKeySequence("M"))

        # Export Menu
        self.menu_export = menubar.addMenu(tr('menu_export'))
        self.menu_export.addAction(tr('act_export_video'), self.export_recorded_video, QKeySequence("Ctrl+E"))
        self.menu_export.addAction(tr('act_export_presets'), self.open_export_presets_dialog)

        # Help Menu
        self.menu_help = menubar.addMenu(tr('menu_help'))
        self.menu_help.addAction(tr('act_about'), self.open_about_dialog, QKeySequence("F1"))
        self.menu_help.addAction(tr('act_updates'), self.open_updates_dialog)
        self.menu_help.addSeparator()
        self.menu_help.addAction(tr('act_open_log_file'), open_log_file)
        self.menu_help.addAction(tr('act_open_logs_folder'), open_logs_folder)

    def _refresh_recent_projects_menu(self):
        if not hasattr(self, 'menu_recent'):
            return
        self.menu_recent.clear()
        projects = ProjectManager.instance().get_recent_projects()
        if not projects:
            act = self.menu_recent.addAction("No Recent Projects")
            act.setEnabled(False)
            return

        for p in projects[:10]:
            name = p.get('name', 'Project')
            ptype = p.get('type', 'video')
            icon_str = "🎬" if ptype == 'video' else ("🪟" if ptype == 'window' else "📄")
            label = f"{icon_str} {name} ({p.get('date', '')})"
            act = self.menu_recent.addAction(label)
            act.triggered.connect(lambda _, pr=p: self._open_recent_project(pr))

        self.menu_recent.addSeparator()
        act_clear = self.menu_recent.addAction("Clear Recent Projects")
        act_clear.triggered.connect(lambda: (ProjectManager.instance().clear(), self._refresh_recent_projects_menu()))

    def _open_recent_project(self, p: dict):
        ptype = p.get('type')
        target = p.get('target')
        if ptype == 'video' and os.path.exists(target):
            self.load_video(target)
        elif ptype == 'blank':
            self.create_blank_canvas(p.get('width', 1920), p.get('height', 1080))
        elif ptype == 'window':
            try:
                hwnd = int(target)
                self.start_window_capture(hwnd, p.get('name', 'Window'))
            except Exception:
                self.open_window_capture_dialog()

    def update_ui_texts(self):
        """Update tooltips and labels when language changes"""
        self.btn_tool_select.setToolTip(tr('btn_select'))
        self.btn_tool_pen.setToolTip(tr('btn_brush'))
        self.btn_tool_eraser.setToolTip(tr('btn_eraser'))
        self.btn_tool_text.setToolTip(tr('btn_add_text'))
        self.btn_add_overlay.setToolTip(tr('btn_add_overlay'))
        self.btn_undo.setToolTip(tr('btn_undo'))
        self.btn_clear_all.setToolTip(tr('btn_clear'))
        self.btn_rec_start.setToolTip(tr('btn_record_start'))
        self.btn_rec_pause.setToolTip(tr('btn_record_pause'))
        self.btn_rec_stop.setToolTip(tr('btn_record_stop'))
        self.btn_rec_export.setToolTip(tr('act_export_video'))
        self.menuBar().clear()
        self._create_menu_bar()

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
            QTabWidget::pane {
                border: 1px solid #222230;
                background-color: #0E0E14;
                border-radius: 4px;
            }
            QTabBar {
                background: transparent;
                qproperty-drawBase: 0;
            }
            QTabBar::tab {
                background-color: #161622;
                color: #A0A0B8;
                border: 1px solid #262638;
                border-bottom: none;
                padding: 6px 16px;
                margin-right: 3px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                font-size: 11px;
                font-weight: 500;
                min-width: 90px;
                max-width: 320px;
            }
            QTabBar::tab:selected {
                background-color: #1F1F2E;
                color: #00E5FF;
                border: 1px solid #007AFF;
                border-bottom: 2px solid #00E5FF;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background-color: #1C1C2A;
                color: #FFFFFF;
            }
            QTabBar::close-button {
                subcontrol-position: right;
                margin-left: 6px;
                padding: 1px;
            }
            QTabBar::close-button:hover {
                background-color: #FF3B30;
                border-radius: 3px;
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
            #BtnMicToggle {
                background-color: #1A3824;
                color: #34C759;
                border: 1px solid #34C759;
                font-weight: bold;
            }
            #BtnMicToggle:hover {
                background-color: #244C30;
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
                padding: 4px 8px;
                font-weight: 500;
                font-size: 11px;
                font-family: 'Segoe UI', Arial, sans-serif;
                min-width: 0px;
                min-height: 22px;
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
                min-width: 58px;
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
                padding: 3px 6px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #222230;
                color: #FFFFFF;
                selection-background-color: #007AFF;
                selection-color: #FFFFFF;
                border: 1px solid #3E3E52;
                outline: 0;
                padding: 4px;
            }
            QCheckBox, QRadioButton {
                color: #E2E2EC;
                font-size: 12px;
                spacing: 6px;
            }
            QCheckBox:hover, QRadioButton:hover {
                color: #FFFFFF;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #3E3E54;
                border-radius: 4px;
                background-color: #222230;
            }
            QCheckBox::indicator:hover {
                border-color: #007AFF;
            }
            QCheckBox::indicator:checked {
                background-color: #007AFF;
                border-color: #007AFF;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #3E3E54;
                border-radius: 8px;
                background-color: #222230;
            }
            QRadioButton::indicator:hover {
                border-color: #007AFF;
            }
            QRadioButton::indicator:checked {
                background-color: #007AFF;
                border-color: #007AFF;
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
    init_logging()
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, 'AA_DisableWindowContextHelpButton'):
        QApplication.setAttribute(Qt.AA_DisableWindowContextHelpButton, True)

    logger.info("Initializing QApplication")
    app = QApplication(sys.argv)

    # Set uniform scalable UI font so layout metrics match rendered text exactly
    app_font = app.font()
    app_font.setFamily("Segoe UI")
    app_font.setStyleHint(QFont.SansSerif)
    app_font.setPointSize(9)
    app.setFont(app_font)

    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    initial_file = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        initial_file = sys.argv[1]
        logger.info(f"Opening with initial file argument: {initial_file}")

    player = FKVideoPlayer(initial_file)
    player.show()
    logger.info("FKVideoPlayer main window displayed")
    ret = app.exec_()
    logger.info(f"FKVideoPlayer exiting with code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
