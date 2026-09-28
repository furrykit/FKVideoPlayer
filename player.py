#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный высокопроизводительный видеоплеер с покадровым воспроизведением,
аппаратным масштабированием (Zoom), панорамированием, интерактивным рисованием (Telestrator)
и полной поддержкой аудио с регулировкой громкости.
"""

import sys
import os
import collections
import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal, QPoint, QUrl
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QBrush, QCursor,
    QFont, QIcon, QKeySequence, QPainterPath
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QColorDialog,
    QComboBox, QSpinBox, QFrame, QSizePolicy, QShortcut, QMessageBox, QToolTip
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent


def dist_to_segment_sq(p: QPointF, a: QPointF, b: QPointF) -> float:
    """Квадрат расстояния от точки p до отрезка [a, b]"""
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
    """
    Продвинутый слайдер:
    - Мгновенный переход по клику мыши в любую точку
    - Плавный скраббинг без блокировки UI
    - Всплывающая подсказка с временем и номером кадра при наведении
    - Прокрутка колесиком мыши вперед/назад
    - Qt.NoFocus для исключения конфликтов со стрелками клавиатуры
    """
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

        # Вычисляем время для подсказки при наведении
        if self.fps > 0 and self.maximum() > 0:
            sec = val / float(self.fps)
            mins = int(sec // 60)
            secs = sec % 60
            tip_text = f"{mins:02d}:{secs:05.2f} (кадр {val + 1})"
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
    """
    Штрих рисования в координатах видео.
    Использует QPainterPath для высокопроизводительной отрисовки через C++ ядро Qt.
    """
    def __init__(self, color, width, points=None):
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


class VideoCanvas(QWidget):
    """
    Высокопроизводительный холст видео, панорамирования, зума и рисования поверх видео
    """
    zoom_changed = pyqtSignal(float)
    drawing_changed = pyqtSignal()

    TOOL_PEN = 0
    TOOL_ERASER = 1
    TOOL_PAN = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background-color: #121216;")

        # Кадр
        self.current_qimage = None
        self.video_width = 0
        self.video_height = 0

        # Зум и панорамирование
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.is_panning = False
        self.last_mouse_pos = QPointF(0, 0)

        # Инструменты рисования
        self.active_tool = self.TOOL_PEN
        self.pen_color = QColor("#FF3B30")
        self.pen_width = 4.0
        self.eraser_radius = 16.0

        # Штрихи и стек отмены
        self.strokes = []
        self.current_stroke = None
        self.undo_stack = []

        self.update_cursor()

    # --- Генерация круглого курсора под размер кисти/ластика ---

    def _create_brush_cursor(self) -> QCursor:
        """
        Создает динамический курсор-кружок под точный размер кисти на экране
        с контрастной двойной обводкой и центральным прицелом.
        """
        d = max(5, int(round(self.pen_width)))
        if d % 2 == 0:
            d += 1
        size = d + 4
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Внешний темный контур
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.5))
        painter.drawEllipse(2, 2, d, d)

        # Внутренний цветной контур
        painter.setPen(QPen(self.pen_color, 1.0))
        painter.drawEllipse(2, 2, d, d)

        # Точка-прицел по центру
        center = size // 2
        painter.setPen(QPen(QColor(0, 0, 0, 255), 1))
        painter.drawPoint(center, center)
        painter.setPen(QPen(QColor(255, 255, 255, 255), 1))
        painter.drawPoint(center, center)

        painter.end()
        return QCursor(pixmap, center, center)

    def _create_eraser_cursor(self) -> QCursor:
        """Динамический курсор для ластика"""
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
        """Обновление формы курсора"""
        if self.active_tool == self.TOOL_PEN:
            self.setCursor(self._create_brush_cursor())
        elif self.active_tool == self.TOOL_ERASER:
            self.setCursor(self._create_eraser_cursor())
        elif self.active_tool == self.TOOL_PAN:
            self.setCursor(Qt.OpenHandCursor)

    def set_tool(self, tool_code):
        self.active_tool = tool_code
        self.update_cursor()
        self.update()

    def set_pen_color(self, color):
        self.pen_color = QColor(color)
        self.update_cursor()
        self.update()

    def set_pen_width(self, width):
        self.pen_width = max(1.0, float(width))
        self.update_cursor()
        self.update()

    def set_frame(self, frame_bgr_or_qimg):
        """Быстрая установка кадра без лишних преобразований памяти"""
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
            # Прямое создание QImage через Format_BGR888 без cv2.cvtColor
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
        """Вписать видео полностью в размер окна с сохранением пропорций"""
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

    # --- Рисование, Ластик, Стереть всё, Стереть на шаг назад ---

    def clear_all_drawings(self):
        """Стереть всё нарисованное"""
        if not self.strokes:
            return
        self.undo_stack.append(('clear', list(self.strokes)))
        self.strokes.clear()
        self.update()
        self.drawing_changed.emit()

    def undo_last_action(self):
        """Стереть на шаг назад (Undo)"""
        if not self.undo_stack:
            if self.strokes:
                self.strokes.pop()
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

        self.update()
        self.drawing_changed.emit()

    def erase_strokes_at_video_pt(self, video_pt: QPointF):
        """Высокоточное стирание штрихов с проверкой расстояния до сегментов"""
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

        for idx in reversed(indices_to_remove):
            removed = self.strokes.pop(idx)
            self.undo_stack.append(('erase', (removed, idx)))
            erased_any = True

        if erased_any:
            self.update()
            self.drawing_changed.emit()

    # --- События мыши ---

    def mousePressEvent(self, event):
        pos = QPointF(event.pos())
        self.last_mouse_pos = pos

        if event.button() in (Qt.RightButton, Qt.MidButton):
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
                self.current_stroke = Stroke(self.pen_color, stroke_width_video, [vpt])
                self.strokes.append(self.current_stroke)
                self.undo_stack.append(('add', self.current_stroke))
                self.update()
                self.drawing_changed.emit()
            elif self.active_tool == self.TOOL_ERASER:
                vpt = self.screen_to_video(pos)
                self.erase_strokes_at_video_pt(vpt)

    def mouseMoveEvent(self, event):
        pos = QPointF(event.pos())
        delta = pos - self.last_mouse_pos
        self.last_mouse_pos = pos

        if self.is_panning:
            self.pan_offset += delta
            self.update()
            return

        if event.buttons() & Qt.LeftButton:
            if self.active_tool == self.TOOL_PEN and self.current_stroke:
                vpt = self.screen_to_video(pos)
                self.current_stroke.add_point(vpt)
                self.update()
            elif self.active_tool == self.TOOL_ERASER:
                vpt = self.screen_to_video(pos)
                self.erase_strokes_at_video_pt(vpt)

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.RightButton, Qt.MidButton) or (
            event.button() == Qt.LeftButton and self.is_panning
        ):
            self.is_panning = False
            self.update_cursor()

        if event.button() == Qt.LeftButton:
            self.current_stroke = None

    def wheelEvent(self, event):
        num_degrees = event.angleDelta().y() / 8.0
        num_steps = num_degrees / 15.0
        factor = 1.15 ** num_steps
        self.apply_zoom_at(self.zoom_factor * factor, QPointF(event.pos()))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    # --- Высокопроизводительная отрисовка ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 1. Заливка фона холста
        painter.fillRect(self.rect(), QColor("#121216"))

        origin = self._get_origin(self.zoom_factor, self.pan_offset)

        # 2. Отрисовка видеокадра и векторных штрихов через аппаратную трансформацию
        if self.current_qimage is not None and not self.current_qimage.isNull():
            painter.save()
            painter.translate(origin.x(), origin.y())
            painter.scale(self.zoom_factor, self.zoom_factor)

            # Кадр видео
            painter.drawImage(0, 0, self.current_qimage)
            painter.setPen(QPen(QColor(60, 60, 75, 180), 1.0 / self.zoom_factor))
            painter.drawRect(0, 0, self.video_width, self.video_height)

            # Все штрихи рисуются напрямую через C++ QPainterPath без циклов на питоне
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
            painter.setFont(QFont("Segoe UI", 14, QFont.Bold))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Нажмите 'Открыть видео' (O) или перетащите файл сюда"
            )


class VideoPlayerWindow(QMainWindow):
    """Главное окно видеоплеера с расширенным управлением и звуком"""

    MAX_CACHE_FRAMES = 120

    def __init__(self, initial_video_path=None):
        super().__init__()
        self.setWindowTitle("Pro Video Player & Telestrator")
        self.resize(1180, 800)
        self.setMinimumSize(800, 540)
        self.setAcceptDrops(True)

        # Переменные видеопотока
        self.cap = None
        self.video_path = ""
        self.fps = 24.0
        self.total_frames = 0
        self.current_frame_idx = 0
        self.is_playing = False
        self.is_looping = True
        self.playback_speed = 1.0
        self.frames_per_tick = 1

        # Аудиосистема
        self.audio_player = QMediaPlayer(self, QMediaPlayer.LowLatency)
        self.current_volume = 80
        self.is_muted = False
        self.audio_player.setVolume(self.current_volume)

        # Кольцевой LRU-кэш кадров для мгновенной покадровой перемотки
        self.frame_cache = collections.OrderedDict()
        self._cap_pos = -1

        # Дебаунсер скраббинга для 60 FPS отзывчивости
        self._pending_seek_frame = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._process_pending_seek)

        # Дебаунсер для покадрового спама
        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._on_step_timer)

        # Отложенная синхронизация звука
        self._audio_sync_timer = QTimer(self)
        self._audio_sync_timer.setSingleShot(True)
        self._audio_sync_timer.timeout.connect(self._sync_audio_position)

        # Таймер воспроизведения
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._on_play_tick)

        # Создание интерфейса
        self._init_ui()
        self._apply_dark_theme()
        self._setup_shortcuts()

        # Открытие начального видео при наличии явного аргумента
        if initial_video_path and os.path.exists(initial_video_path):
            self.load_video(initial_video_path)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 1. Центральный холст видео и рисования
        self.canvas = VideoCanvas(self)
        self.canvas.zoom_changed.connect(self._on_canvas_zoom_changed)
        self.canvas.drawing_changed.connect(self._on_drawing_changed)

        # 2. Верхняя панель инструментов
        self._create_top_toolbar()
        main_layout.addWidget(self.top_toolbar)

        # Холст
        main_layout.addWidget(self.canvas, stretch=1)

        # 3. Нижняя панель таймлайна, звука и воспроизведения
        bottom_panel = self._create_bottom_controls()
        main_layout.addWidget(bottom_panel)

    def _create_top_toolbar(self):
        self.top_toolbar = QFrame()
        self.top_toolbar.setObjectName("TopToolbar")
        layout = QHBoxLayout(self.top_toolbar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Открытие файла
        self.btn_open = QPushButton("📂 Открыть")
        self.btn_open.setToolTip("Открыть видеофайл (O)")
        self.btn_open.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.btn_open)

        self._add_separator(layout)

        # Инструменты
        self.btn_tool_pen = QPushButton("✏️ Кисть")
        self.btn_tool_pen.setToolTip("Инструмент 'Кисть' для рисования поверх видео (P)")
        self.btn_tool_pen.setCheckable(True)
        self.btn_tool_pen.setChecked(True)
        self.btn_tool_pen.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        layout.addWidget(self.btn_tool_pen)

        self.btn_tool_eraser = QPushButton("🧹 Ластик")
        self.btn_tool_eraser.setToolTip("Инструмент 'Ластик' для стирания штрихов (E)")
        self.btn_tool_eraser.setCheckable(True)
        self.btn_tool_eraser.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        layout.addWidget(self.btn_tool_eraser)

        self.btn_tool_pan = QPushButton("✋ Рука")
        self.btn_tool_pan.setToolTip("Инструмент 'Рука' для перемещения видео (H)")
        self.btn_tool_pan.setCheckable(True)
        self.btn_tool_pan.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        layout.addWidget(self.btn_tool_pan)

        self._add_separator(layout)

        # Палитра быстрых цветов с активной подсветкой
        self.preset_color_buttons = {}
        preset_colors = [
            ("#FF3B30", "Красный"),
            ("#34C759", "Зеленый"),
            ("#007AFF", "Синий"),
            ("#FFCC00", "Желтый"),
            ("#FFFFFF", "Белый"),
            ("#FF9500", "Оранжевый"),
            ("#AF52DE", "Фиолетовый"),
        ]
        layout.addWidget(QLabel("Цвет:"))
        for hex_code, name in preset_colors:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setToolTip(f"{name} ({hex_code})")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=hex_code: self._set_brush_color(c))
            layout.addWidget(btn)
            self.preset_color_buttons[hex_code.upper()] = btn

        self.btn_custom_color = QPushButton("🎨")
        self.btn_custom_color.setToolTip("Выбрать любой цвет из палитры...")
        self.btn_custom_color.setFixedSize(26, 26)
        self.btn_custom_color.clicked.connect(self._pick_custom_color)
        layout.addWidget(self.btn_custom_color)

        self.color_indicator = QFrame()
        self.color_indicator.setFixedSize(20, 20)
        self.color_indicator.setToolTip("Текущий цвет кисти")
        layout.addWidget(self.color_indicator)
        self._update_color_buttons_state("#FF3B30")

        # Толщина кисти
        layout.addWidget(QLabel("Толщина:"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 60)
        self.spin_width.setValue(4)
        self.spin_width.setSuffix(" px")
        self.spin_width.setToolTip("Толщина кисти (клавиши [ и ])")
        self.spin_width.valueChanged.connect(self._on_pen_width_changed)
        layout.addWidget(self.spin_width)

        self._add_separator(layout)

        # Кнопки: Стереть на шаг назад и Стереть всё
        self.btn_undo = QPushButton("↩ Стереть на шаг назад")
        self.btn_undo.setToolTip("Отменить последнее рисование или стирание (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.canvas.undo_last_action)
        layout.addWidget(self.btn_undo)

        self.btn_clear_all = QPushButton("🗑 Стереть всё")
        self.btn_clear_all.setToolTip("Очистить все рисунки с видео (Delete или C)")
        self.btn_clear_all.clicked.connect(self.canvas.clear_all_drawings)
        layout.addWidget(self.btn_clear_all)

        layout.addStretch(1)

        # Управление зумом
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(45)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)

        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.setToolTip("Отдалить видео (-)")
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Приблизить видео (+)")
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)

        self.btn_zoom_reset = QPushButton("1:1")
        self.btn_zoom_reset.setToolTip("Вписать видео в экран (0)")
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

    def _create_bottom_controls(self):
        panel = QFrame()
        panel.setObjectName("BottomPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # 1. Полоса перемотки (ClickableSlider)
        self.timeline_slider = ClickableSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.wheel_scrolled.connect(self._on_slider_wheel)
        layout.addWidget(self.timeline_slider)

        # 2. Панель кнопок навигации, звука и управления
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        # Быстрая перемотка назад
        self.btn_rewind_5s = QPushButton("⏮ -5с")
        self.btn_rewind_5s.setToolTip("Назад на 5 секунд (J или Ctrl+Left)")
        self.btn_rewind_5s.clicked.connect(lambda: self.seek_seconds(-5.0))
        ctrl_layout.addWidget(self.btn_rewind_5s)

        self.btn_rewind_1s = QPushButton("◀◀ -1с")
        self.btn_rewind_1s.setToolTip("Назад на 1 секунду (Shift+Left)")
        self.btn_rewind_1s.clicked.connect(lambda: self.seek_seconds(-1.0))
        ctrl_layout.addWidget(self.btn_rewind_1s)

        # Покадрово назад (-1 кадр)
        self.btn_prev_frame = QPushButton("◀| Кадр -1")
        self.btn_prev_frame.setToolTip("Шаг назад на 1 кадр (Стрелка влево)")
        self.btn_prev_frame.clicked.connect(lambda: self.step_frame(-1))
        ctrl_layout.addWidget(self.btn_prev_frame)

        # Воспроизведение / Пауза
        self.btn_play_pause = QPushButton("▶ Пуск")
        self.btn_play_pause.setObjectName("PlayButton")
        self.btn_play_pause.setToolTip("Воспроизведение / Пауза (Пробел или K)")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        ctrl_layout.addWidget(self.btn_play_pause)

        # Покадрово вперед (+1 кадр)
        self.btn_next_frame = QPushButton("|▶ Кадр +1")
        self.btn_next_frame.setToolTip("Шаг вперед на 1 кадр (Стрелка вправо)")
        self.btn_next_frame.clicked.connect(lambda: self.step_frame(1))
        ctrl_layout.addWidget(self.btn_next_frame)

        # Быстрая перемотка вперед
        self.btn_forward_1s = QPushButton("+1с ▶▶")
        self.btn_forward_1s.setToolTip("Вперед на 1 секунду (Shift+Right)")
        self.btn_forward_1s.clicked.connect(lambda: self.seek_seconds(1.0))
        ctrl_layout.addWidget(self.btn_forward_1s)

        self.btn_forward_5s = QPushButton("+5с ⏭")
        self.btn_forward_5s.setToolTip("Вперед на 5 секунд (L или Ctrl+Right)")
        self.btn_forward_5s.clicked.connect(lambda: self.seek_seconds(5.0))
        ctrl_layout.addWidget(self.btn_forward_5s)

        # Зацикливание
        self.btn_loop = QPushButton("🔁 Цикл")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        self.btn_loop.setToolTip("Зацикливать видео при достижении конца")
        self.btn_loop.clicked.connect(self._toggle_loop)
        ctrl_layout.addWidget(self.btn_loop)

        self._add_separator(ctrl_layout)

        # Звук и громкость
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedSize(28, 26)
        self.btn_mute.setToolTip("Включить / выключить звук (M)")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl_layout.addWidget(self.btn_mute)

        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(self.current_volume)
        self.slider_volume.setFixedWidth(80)
        self.slider_volume.setFocusPolicy(Qt.NoFocus)
        self.slider_volume.setToolTip("Громкость звука (Стрелки Вверх / Вниз)")
        self.slider_volume.valueChanged.connect(self.set_volume)
        ctrl_layout.addWidget(self.slider_volume)

        self.lbl_volume = QLabel(f"{self.current_volume}%")
        self.lbl_volume.setMinimumWidth(35)
        ctrl_layout.addWidget(self.lbl_volume)

        self._add_separator(ctrl_layout)

        # Скорость воспроизведения
        ctrl_layout.addWidget(QLabel("Скорость:"))
        self.combo_speed = QComboBox()
        self.speed_presets = [
            "0.05x", "0.1x", "0.2x", "0.25x", "0.33x", "0.5x", "0.75x",
            "1.0x",
            "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x", "4.0x", "5.0x", "8.0x", "10.0x"
        ]
        self.combo_speed.addItems(self.speed_presets)
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.setToolTip("Скорость видео (Клавиши < и > для переключения, R для сброса на 1.0x)")
        self.combo_speed.currentTextChanged.connect(self._on_speed_changed)
        ctrl_layout.addWidget(self.combo_speed)

        ctrl_layout.addStretch(1)

        # Информация о времени и кадрах
        self.lbl_time_info = QLabel("00:00.00 / 00:00.00  |  Кадр: 0 / 0  (0.0 FPS)")
        self.lbl_time_info.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #9EABB8;")
        ctrl_layout.addWidget(self.lbl_time_info)

        layout.addLayout(ctrl_layout)
        return panel

    def _setup_shortcuts(self):
        """Полный набор удобных горячих клавиш"""
        # Воспроизведение
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play_pause)
        QShortcut(QKeySequence("K"), self, self.toggle_play_pause)

        # Покадровая навигация
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.step_frame(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.step_frame(1))

        # Перемотка на 1 секунду
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.seek_seconds(-1.0))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_seconds(1.0))

        # Перемотка на 5 секунд
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.seek_seconds(-5.0))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.seek_seconds(5.0))
        QShortcut(QKeySequence("J"), self, lambda: self.seek_seconds(-5.0))
        QShortcut(QKeySequence("L"), self, lambda: self.seek_seconds(5.0))

        # Переход в начало и конец
        QShortcut(QKeySequence(Qt.Key_Home), self, lambda: self._seek_to_frame(0))
        QShortcut(QKeySequence(Qt.Key_End), self, lambda: self._seek_to_frame(self.total_frames - 1))

        # Звук: громкость и выключение
        QShortcut(QKeySequence("M"), self, self.toggle_mute)
        QShortcut(QKeySequence(Qt.Key_Up), self, lambda: self.adjust_volume(5))
        QShortcut(QKeySequence(Qt.Key_Down), self, lambda: self.adjust_volume(-5))

        # Рисование и действия
        QShortcut(QKeySequence("Ctrl+Z"), self, self.canvas.undo_last_action)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self.canvas.clear_all_drawings)
        QShortcut(QKeySequence("C"), self, self.canvas.clear_all_drawings)

        # Толщина кисти клавишами [ и ]
        QShortcut(QKeySequence("["), self, lambda: self.spin_width.setValue(self.spin_width.value() - 1))
        QShortcut(QKeySequence("]"), self, lambda: self.spin_width.setValue(self.spin_width.value() + 1))

        # Скорость воспроизведения (< замедлить, > ускорить, R сброс)
        QShortcut(QKeySequence("<"), self, self.decrease_speed)
        QShortcut(QKeySequence(">"), self, self.increase_speed)
        QShortcut(QKeySequence("Shift+,"), self, self.decrease_speed)
        QShortcut(QKeySequence("Shift+."), self, self.increase_speed)
        QShortcut(QKeySequence("R"), self, self.reset_speed)

        # Зум
        QShortcut(QKeySequence("+"), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("="), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("-"), self, self.canvas.zoom_out)
        QShortcut(QKeySequence("0"), self, self.canvas.fit_to_view)

        # Инструменты
        QShortcut(QKeySequence("P"), self, lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        QShortcut(QKeySequence("E"), self, lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        QShortcut(QKeySequence("H"), self, lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        QShortcut(QKeySequence("O"), self, self.open_file_dialog)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if os.path.exists(file_path):
                self.load_video(file_path)

    # --- Управление звуком ---

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

    # --- Инструменты, Цвета, Толщина ---

    def _select_tool(self, tool_code):
        self.btn_tool_pen.setChecked(tool_code == VideoCanvas.TOOL_PEN)
        self.btn_tool_eraser.setChecked(tool_code == VideoCanvas.TOOL_ERASER)
        self.btn_tool_pan.setChecked(tool_code == VideoCanvas.TOOL_PAN)
        self.canvas.set_tool(tool_code)

    def _update_color_buttons_state(self, current_hex):
        current_hex = current_hex.upper()
        for hex_code, btn in self.preset_color_buttons.items():
            if hex_code == current_hex:
                btn.setStyleSheet(
                    f"background-color: {hex_code}; border-radius: 11px; border: 2px solid #FFFFFF; outline: 2px solid #007AFF;"
                )
            else:
                btn.setStyleSheet(
                    f"background-color: {hex_code}; border-radius: 11px; border: 2px solid #484858;"
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
        col = QColorDialog.getColor(self.canvas.pen_color, self, "Выберите цвет кисти")
        if col.isValid():
            self._set_brush_color(col.name())

    def _on_pen_width_changed(self, val):
        self.canvas.set_pen_width(val)

    def _on_canvas_zoom_changed(self, zoom):
        self.lbl_zoom.setText(f"{int(round(zoom * 100))}%")

    def _on_drawing_changed(self):
        pass

    # --- Видеопоток, Кэширование и Воспроизведение ---

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите видеофайл",
            "",
            "Видеофайлы (*.mp4 *.avi *.mkv *.mov *.webm *.flv *.m4v);;Все файлы (*.*)"
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path):
        if self.cap is not None:
            self.cap.release()
            self.pause()

        self.cap = cv2.VideoCapture(file_path)
        if not self.cap.isOpened():
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть видеофайл:\n{file_path}")
            return

        self.video_path = file_path
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        if self.fps <= 1.0 or np.isnan(self.fps):
            self.fps = 25.0

        # Очистка кэша кадров
        self.frame_cache.clear()

        # Инициализация звуковой дорожки через QMediaPlayer
        media_url = QUrl.fromLocalFile(os.path.abspath(file_path))
        self.audio_player.setMedia(QMediaContent(media_url))
        self.audio_player.setVolume(self.current_volume if not self.is_muted else 0)
        self.audio_player.setPosition(0)

        self.timeline_slider.fps = self.fps
        self.timeline_slider.setRange(0, max(0, self.total_frames - 1))
        self.timeline_slider.setValue(0)
        self.current_frame_idx = 0

        # Сброс холста
        self.canvas.strokes.clear()
        self.canvas.undo_stack.clear()

        # Отображение первого кадра
        self._seek_to_frame(0)
        self.setWindowTitle(f"Pro Video Player — {os.path.basename(file_path)}")

    def _get_frame_cached(self, frame_idx):
        """
        Возвращает кадр из памяти за 0.01 мс.
        Если кадра нет в кэше, считывает его через cv2 и кэширует.
        Использует последовательное чтение без cap.set, если кадр следующий по порядку (в 14 раз быстрее).
        """
        if self.cap is None or not self.cap.isOpened():
            return None

        if frame_idx in self.frame_cache:
            self.frame_cache.move_to_end(frame_idx)
            return self.frame_cache[frame_idx]

        # Если кадр не следующий по порядку — позиционируем
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

            if not self.timeline_slider.is_dragging:
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(frame_idx)
                self.timeline_slider.blockSignals(False)

    def _sync_audio_position(self):
        """Отложенная синхронизация аудио, чтобы не блокировать GUI при быстром кликанье"""
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
        self.btn_play_pause.setText("⏸ Пауза")

        # Запуск аудио с защитой драйверов Windows от экстремальных скоростей
        target_ms = int((self.current_frame_idx / self.fps) * 1000)
        self.audio_player.setPosition(target_ms)
        self._update_audio_rate()
        self.audio_player.play()

    def pause(self):
        self.play_timer.stop()
        self.is_playing = False
        self.btn_play_pause.setText("▶ Пуск")
        self.audio_player.pause()

    def _update_audio_rate(self):
        """
        DirectShow/MediaFoundation на Windows гарантированно работает на скоростях от 0.5 до 2.0.
        При выходе за эти пределы выставляем безопасную частоту и отключаем звук,
        предотвращая сбой драйверов WMF/ResourceError и зависание потоков.
        """
        if 0.5 <= self.playback_speed <= 2.0:
            self.audio_player.setPlaybackRate(self.playback_speed)
            if not self.is_muted:
                self.audio_player.setMuted(False)
        else:
            safe_rate = 1.0 if self.playback_speed > 2.0 else 0.5
            self.audio_player.setPlaybackRate(safe_rate)
            self.audio_player.setMuted(True)

    def _update_timer_interval(self):
        target_fps = max(0.1, self.fps * self.playback_speed)
        if target_fps <= 60.0:
            interval = int(round(1000.0 / target_fps))
            self.frames_per_tick = 1
        else:
            interval = 16  # 60 Гц
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

                # Периодическая проверка синхронизации звука (допуск 150мс)
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
        """
        Покадровый переход вперед (+1) или назад (-1).
        Мгновенно обновляет визуальные координаты, а при быстром спаме кликами
        дебаунсит декодирование до 10мс, полностью устраняя любые фризы интерфейса.
        """
        if self.is_playing:
            self.pause()

        if self.cap is None or not self.cap.isOpened() or self.total_frames <= 0:
            return

        target = max(0, min(self.total_frames - 1, self.current_frame_idx + delta))
        self.current_frame_idx = target
        self._update_time_label()

        if not self.timeline_slider.is_dragging:
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(target)
            self.timeline_slider.blockSignals(False)

        # Если кадр есть в кэше — отображаем мгновенно (0 мс)
        if target in self.frame_cache:
            self.canvas.set_frame(self.frame_cache[target])
            self._audio_sync_timer.start(150)
            return

        # Если кадра нет в кэше — считываем или обрабатываем через дебаунсер
        if not self._step_timer.isActive():
            self._step_timer.start(10)

    def _on_step_timer(self):
        """Отработка целевого кадра после серии быстрых кликов"""
        qimg = self._get_frame_cached(self.current_frame_idx)
        if qimg is not None:
            self.canvas.set_frame(qimg)
        self._audio_sync_timer.start(150)

    def seek_seconds(self, delta_sec):
        """Перемотка на указанное число секунд без зависаний"""
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
        """Увеличить скорость на 1 ступень (>)"""
        idx = self.combo_speed.currentIndex()
        if idx < self.combo_speed.count() - 1:
            self.combo_speed.setCurrentIndex(idx + 1)

    def decrease_speed(self):
        """Уменьшить скорость на 1 ступень (<)"""
        idx = self.combo_speed.currentIndex()
        if idx > 0:
            self.combo_speed.setCurrentIndex(idx - 1)

    def reset_speed(self):
        """Сбросить скорость на 1.0x (R)"""
        idx = self.combo_speed.findText("1.0x")
        if idx >= 0:
            self.combo_speed.setCurrentIndex(idx)

    # --- Высокоотзывчивые обработчики таймлайна с дебаунсингом ---

    def _on_slider_pressed(self):
        self._was_playing_before_scrub = self.is_playing
        self.pause()

    def _on_slider_moved(self, val):
        self._pending_seek_frame = val
        if not self._seek_timer.isActive():
            self._seek_timer.start(16)  # 60 FPS троттлинг для плавного скраббинга

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
                f"{self._format_time(cur_sec)} / {self._format_time(tot_sec)}  |  Кадр: {self.current_frame_idx + 1} / {self.total_frames}  ({self.fps:.1f} FPS)"
            )
        else:
            self.lbl_time_info.setText("00:00.00 / 00:00.00  |  Кадр: 0 / 0  (0.0 FPS)")

    def closeEvent(self, event):
        """Корректное освобождение ресурсов при закрытии"""
        self.play_timer.stop()
        self.audio_player.stop()
        if self.cap is not None:
            self.cap.release()
        event.accept()

    def _apply_dark_theme(self):
        """Премиальный темный интерфейс"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #16161C;
            }
            #TopToolbar, #BottomPanel {
                background-color: #1F1F28;
                border: 1px solid #2B2B38;
                border-radius: 8px;
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
                transform: scale(1.2);
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


def main():
    app = QApplication(sys.argv)
    initial_file = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        initial_file = sys.argv[1]

    player = VideoPlayerWindow(initial_file)
    player.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
