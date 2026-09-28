#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный видеоплеер с покадровым воспроизведением, зумом, панорамированием
и интерактивным рисованием поверх видео.
"""

import sys
import os
import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QCursor, QFont, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QColorDialog,
    QToolBar, QAction, QActionGroup, QComboBox, QSpinBox,
    QFrame, QSizePolicy, QShortcut, QMessageBox, QStyle, QStyleOptionSlider
)


class ClickableSlider(QSlider):
    """Слайдер с мгновенным переходом по клику в любую точку шкалы"""
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            val = int(self.minimum() + (self.maximum() - self.minimum()) * (event.x() / max(1, self.width())))
            val = max(self.minimum(), min(self.maximum(), val))
            self.setValue(val)
            self.sliderMoved.emit(val)
        super().mousePressEvent(event)


class Stroke:
    """Одиночный штрих рисования в координатах видео"""
    def __init__(self, color, width, points=None):
        self.color = QColor(color)
        self.width = float(width)
        self.points = list(points) if points else []

    def add_point(self, pt: QPointF):
        self.points.append(QPointF(pt.x(), pt.y()))


class VideoCanvas(QWidget):
    """
    Холст отображения видеокадра, масштабирования (Zoom),
    панорамирования (Pan) и рисования поверх видео.
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
        self.pen_color = QColor("#FF3B30")  # Ярко-красный по умолчанию
        self.pen_width = 4.0
        self.eraser_radius = 16.0

        # Список штрихов и история действий
        self.strokes = []
        self.current_stroke = None
        self.undo_stack = []

        # Курсор
        self.update_cursor()

    def set_tool(self, tool_code):
        self.active_tool = tool_code
        self.update_cursor()

    def set_pen_color(self, color):
        self.pen_color = QColor(color)

    def set_pen_width(self, width):
        self.pen_width = max(1.0, float(width))

    def update_cursor(self):
        if self.active_tool == self.TOOL_PEN:
            self.setCursor(Qt.CrossCursor)
        elif self.active_tool == self.TOOL_ERASER:
            self.setCursor(Qt.PointingHandCursor)
        elif self.active_tool == self.TOOL_PAN:
            self.setCursor(Qt.OpenHandCursor)

    def set_frame(self, frame_bgr):
        """Обновление текущего кадра из формата OpenCV BGR"""
        if frame_bgr is None:
            self.current_qimage = None
            self.update()
            return

        h, w, ch = frame_bgr.shape
        first_time = (self.video_width != w or self.video_height != h)
        self.video_width = w
        self.video_height = h

        # Быстрая конвертация cv2 BGR в QImage RGB
        rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        bytes_per_line = ch * w
        self.current_qimage = QImage(
            rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888
        ).copy()

        if first_time:
            self.fit_to_view()
        else:
            self.update()

    def fit_to_view(self):
        """Вписать видео полностью в размер окна"""
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
        self.zoom_factor = min(scale_w, scale_h) * 0.95
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

        # Точка на видео под курсором
        video_x = (center_pt.x() - old_origin.x()) / old_zoom
        video_y = (center_pt.y() - old_origin.y()) / old_zoom

        # Корректируем смещение чтобы точка видео осталась под курсором
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

    # --- Рисование и Ластик ---

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
            # Если в стеке пусто, но штрихи есть — удалим последний штрих
            if self.strokes:
                self.strokes.pop()
                self.update()
                self.drawing_changed.emit()
            return

        action_type, payload = self.undo_stack.pop()
        if action_type == 'add':
            # Удаляем добавленный штрих
            if payload in self.strokes:
                self.strokes.remove(payload)
        elif action_type == 'erase':
            # Восстанавливаем стертый штрих
            stroke, original_idx = payload
            idx = min(original_idx, len(self.strokes))
            self.strokes.insert(idx, stroke)
        elif action_type == 'clear':
            # Восстанавливаем все стертые штрихи
            self.strokes = list(payload)

        self.update()
        self.drawing_changed.emit()

    def erase_strokes_at_video_pt(self, video_pt: QPointF):
        """Удаление штрихов, задетых ластиком"""
        erased_any = False
        radius_video = self.eraser_radius / max(0.1, self.zoom_factor)
        r2 = radius_video * radius_video

        indices_to_remove = []
        for i, stroke in enumerate(self.strokes):
            for pt in stroke.points:
                dx = pt.x() - video_pt.x()
                dy = pt.y() - video_pt.y()
                if (dx * dx + dy * dy) <= r2:
                    indices_to_remove.append(i)
                    break

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

        # Средняя или правая кнопка мыши ВСЕГДА выполняет панорамирование
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
                # Толщина штриха адаптируется к видео
                stroke_width_video = max(1.0, self.pen_width / max(0.1, self.zoom_factor))
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
        # Зум колесиком мыши в точку курсора
        num_degrees = event.angleDelta().y() / 8.0
        num_steps = num_degrees / 15.0
        factor = 1.15 ** num_steps
        self.apply_zoom_at(self.zoom_factor * factor, QPointF(event.pos()))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

    # --- Отрисовка ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 1. Заливка фона
        painter.fillRect(self.rect(), QColor("#121216"))

        origin = self._get_origin(self.zoom_factor, self.pan_offset)

        # 2. Отрисовка видеокадра
        if self.current_qimage is not None and not self.current_qimage.isNull():
            target_rect = QRectF(
                origin.x(),
                origin.y(),
                self.video_width * self.zoom_factor,
                self.video_height * self.zoom_factor
            )
            painter.drawImage(target_rect, self.current_qimage)
            # Тонкая рамка вокруг видео
            painter.setPen(QPen(QColor(60, 60, 75, 180), 1))
            painter.drawRect(target_rect)
        else:
            # Заглушка, если видео не загружено
            painter.setPen(QColor("#7E7E94"))
            painter.setFont(QFont("Segoe UI", 14, QFont.Bold))
            painter.drawText(
                self.rect(),
                Qt.AlignCenter,
                "Нажмите 'Открыть видео' или перетащите файл сюда"
            )

        # 3. Отрисовка всех нарисованных штрихов поверх видео
        for stroke in self.strokes:
            if len(stroke.points) == 0:
                continue

            screen_pen_width = max(1.0, stroke.width * self.zoom_factor)
            pen = QPen(stroke.color, screen_pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)

            if len(stroke.points) == 1:
                sp = self.video_to_screen(stroke.points[0])
                painter.setBrush(QBrush(stroke.color))
                radius = max(1.0, screen_pen_width / 2.0)
                painter.drawEllipse(sp, radius, radius)
            else:
                for i in range(len(stroke.points) - 1):
                    p1 = self.video_to_screen(stroke.points[i])
                    p2 = self.video_to_screen(stroke.points[i + 1])
                    painter.drawLine(p1, p2)

        # 4. Визуальный маркер ластика вокруг мыши при наведении
        if self.active_tool == self.TOOL_ERASER and self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            mouse_p = self.mapFromGlobal(QCursor.pos())
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1.5, Qt.DashLine))
            painter.setBrush(QBrush(QColor(255, 255, 255, 30)))
            painter.drawEllipse(mouse_p, int(self.eraser_radius), int(self.eraser_radius))


class VideoPlayerWindow(QMainWindow):
    """Главное окно видеоплеера с расширенным управлением"""

    def __init__(self, initial_video_path=None):
        super().__init__()
        self.setWindowTitle("Pro Video Player & Telestrator")
        self.resize(1100, 750)
        self.setMinimumSize(700, 500)

        # Настройка окна и перетаскивания файлов
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

        # Таймер воспроизведения
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._on_play_tick)

        # Создание интерфейса
        self._init_ui()
        self._apply_dark_theme()
        self._setup_shortcuts()

        # Открытие начального видео при наличии
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

        # 2. Верхняя панель инструментов (Рисование, Цвета, Зум)
        self._create_top_toolbar()
        main_layout.addWidget(self.top_toolbar)

        # Добавляем холст
        main_layout.addWidget(self.canvas, stretch=1)

        # 3. Нижняя панель таймлайна и управления воспроизведением
        bottom_panel = self._create_bottom_controls()
        main_layout.addWidget(bottom_panel)

    def _create_top_toolbar(self):
        self.top_toolbar = QFrame()
        self.top_toolbar.setObjectName("TopToolbar")
        layout = QHBoxLayout(self.top_toolbar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        # Кнопка открытия файла
        self.btn_open = QPushButton("📂 Открыть видео")
        self.btn_open.clicked.connect(self.open_file_dialog)
        layout.addWidget(self.btn_open)

        self._add_separator(layout)

        # Инструменты: Карандаш, Ластик, Перемещение
        self.btn_tool_pen = QPushButton("✏️ Карандаш")
        self.btn_tool_pen.setCheckable(True)
        self.btn_tool_pen.setChecked(True)
        self.btn_tool_pen.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        layout.addWidget(self.btn_tool_pen)

        self.btn_tool_eraser = QPushButton("🧹 Ластик")
        self.btn_tool_eraser.setCheckable(True)
        self.btn_tool_eraser.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        layout.addWidget(self.btn_tool_eraser)

        self.btn_tool_pan = QPushButton("✋ Рука (Pan)")
        self.btn_tool_pan.setCheckable(True)
        self.btn_tool_pan.clicked.connect(lambda: self._select_tool(VideoCanvas.TOOL_PAN))
        layout.addWidget(self.btn_tool_pan)

        self._add_separator(layout)

        # Палитра быстрых цветов
        self.color_buttons = []
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
            btn.setFixedSize(24, 24)
            btn.setToolTip(name)
            btn.setStyleSheet(
                f"background-color: {hex_code}; border-radius: 12px; border: 2px solid #555;"
            )
            btn.clicked.connect(lambda _, c=hex_code: self._set_brush_color(c))
            layout.addWidget(btn)
            self.color_buttons.append(btn)

        self.btn_custom_color = QPushButton("🎨")
        self.btn_custom_color.setToolTip("Выбрать произвольный цвет")
        self.btn_custom_color.setFixedSize(28, 28)
        self.btn_custom_color.clicked.connect(self._pick_custom_color)
        layout.addWidget(self.btn_custom_color)

        # Индикатор текущего цвета
        self.color_indicator = QFrame()
        self.color_indicator.setFixedSize(20, 20)
        self.color_indicator.setStyleSheet(
            f"background-color: {self.canvas.pen_color.name()}; border: 1px solid #FFF; border-radius: 4px;"
        )
        layout.addWidget(self.color_indicator)

        # Толщина кисти
        layout.addWidget(QLabel("Толщина:"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 40)
        self.spin_width.setValue(4)
        self.spin_width.setSuffix(" px")
        self.spin_width.valueChanged.connect(self._on_pen_width_changed)
        layout.addWidget(self.spin_width)

        self._add_separator(layout)

        # Кнопки: Стереть на шаг назад и Стереть всё
        self.btn_undo = QPushButton("↩ Стереть на шаг назад")
        self.btn_undo.setToolTip("Отменить последнее рисование или стирание (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.canvas.undo_last_action)
        layout.addWidget(self.btn_undo)

        self.btn_clear_all = QPushButton("🗑 Стереть всё")
        self.btn_clear_all.setToolTip("Очистить все рисунки с видео (Delete)")
        self.btn_clear_all.clicked.connect(self.canvas.clear_all_drawings)
        layout.addWidget(self.btn_clear_all)

        layout.addStretch(1)

        # Управление зумом
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(50)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)

        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.setToolTip("Отдалить видео (-)")
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Приблизить видео (+)")
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)

        self.btn_zoom_reset = QPushButton("1:1 Сброс")
        self.btn_zoom_reset.setToolTip("Вписать видео в окно (0)")
        self.btn_zoom_reset.clicked.connect(self.canvas.fit_to_view)

        layout.addWidget(self.btn_zoom_out)
        layout.addWidget(self.lbl_zoom)
        layout.addWidget(self.btn_zoom_in)
        layout.addWidget(self.btn_zoom_reset)

    def _add_separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #3E3E50; margin: 2px 4px;")
        layout.addWidget(line)

    def _create_bottom_controls(self):
        panel = QFrame()
        panel.setObjectName("BottomPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # 1. Полоса перемотки (Slider) с мгновенным кликом
        self.timeline_slider = ClickableSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self.timeline_slider)

        # 2. Кнопки воспроизведения, покадровой навигации, времени
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(8)

        # Перемотка на -5 секунд
        self.btn_rewind_5s = QPushButton("⏮ -5с")
        self.btn_rewind_5s.setToolTip("Перемотка на 5 секунд назад")
        self.btn_rewind_5s.clicked.connect(lambda: self.seek_seconds(-5.0))
        ctrl_layout.addWidget(self.btn_rewind_5s)

        # Покадрово назад (-1 кадр)
        self.btn_prev_frame = QPushButton("◀| Кадр -1")
        self.btn_prev_frame.setToolTip("Шаг назад на один кадр (Стрелка влево)")
        self.btn_prev_frame.clicked.connect(lambda: self.step_frame(-1))
        ctrl_layout.addWidget(self.btn_prev_frame)

        # Воспроизведение / Пауза
        self.btn_play_pause = QPushButton("▶ Пуск")
        self.btn_play_pause.setObjectName("PlayButton")
        self.btn_play_pause.setToolTip("Воспроизведение / Пауза (Пробел)")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        ctrl_layout.addWidget(self.btn_play_pause)

        # Покадрово вперед (+1 кадр)
        self.btn_next_frame = QPushButton("|▶ Кадр +1")
        self.btn_next_frame.setToolTip("Шаг вперед на один кадр (Стрелка вправо)")
        self.btn_next_frame.clicked.connect(lambda: self.step_frame(1))
        ctrl_layout.addWidget(self.btn_next_frame)

        # Перемотка на +5 секунд
        self.btn_forward_5s = QPushButton("+5с ⏭")
        self.btn_forward_5s.setToolTip("Перемотка на 5 секунд вперед")
        self.btn_forward_5s.clicked.connect(lambda: self.seek_seconds(5.0))
        ctrl_layout.addWidget(self.btn_forward_5s)

        # Зацикливание
        self.btn_loop = QPushButton("🔁 Цикл")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        self.btn_loop.clicked.connect(self._toggle_loop)
        ctrl_layout.addWidget(self.btn_loop)

        # Выбор скорости воспроизведения
        ctrl_layout.addWidget(QLabel("Скорость:"))
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.25x", "0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentText("1.0x")
        self.combo_speed.currentTextChanged.connect(self._on_speed_changed)
        ctrl_layout.addWidget(self.combo_speed)

        ctrl_layout.addStretch(1)

        # Метка текущего времени и номера кадра
        self.lbl_time_info = QLabel("00:00.00 / 00:00.00  |  Кадр: 0 / 0")
        self.lbl_time_info.setStyleSheet("font-family: Consolas, monospace; font-size: 13px; color: #A0A5B5;")
        ctrl_layout.addWidget(self.lbl_time_info)

        layout.addLayout(ctrl_layout)
        return panel

    def _setup_shortcuts(self):
        """Горячие клавиши для мгновенного контроля"""
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play_pause)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.step_frame(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.step_frame(1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.seek_seconds(-1.0))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.seek_seconds(1.0))
        QShortcut(QKeySequence("Ctrl+Z"), self, self.canvas.undo_last_action)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self.canvas.clear_all_drawings)
        QShortcut(QKeySequence("+"), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("="), self, self.canvas.zoom_in)
        QShortcut(QKeySequence("-"), self, self.canvas.zoom_out)
        QShortcut(QKeySequence("0"), self, self.canvas.fit_to_view)
        QShortcut(QKeySequence("P"), self, lambda: self._select_tool(VideoCanvas.TOOL_PEN))
        QShortcut(QKeySequence("E"), self, lambda: self._select_tool(VideoCanvas.TOOL_ERASER))
        QShortcut(QKeySequence("H"), self, lambda: self._select_tool(VideoCanvas.TOOL_PAN))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if os.path.exists(file_path):
                self.load_video(file_path)

    # --- Обработчики инструментов и рисования ---

    def _select_tool(self, tool_code):
        self.btn_tool_pen.setChecked(tool_code == VideoCanvas.TOOL_PEN)
        self.btn_tool_eraser.setChecked(tool_code == VideoCanvas.TOOL_ERASER)
        self.btn_tool_pan.setChecked(tool_code == VideoCanvas.TOOL_PAN)
        self.canvas.set_tool(tool_code)

    def _set_brush_color(self, hex_color):
        self.canvas.set_pen_color(hex_color)
        self.color_indicator.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #FFF; border-radius: 4px;"
        )
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

    # --- Видеопоток и Воспроизведение ---

    def open_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите видеофайл",
            "",
            "Видеофайлы (*.mp4 *.avi *.mkv *.mov *.webm *.flv);;Все файлы (*.*)"
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

        self.timeline_slider.setRange(0, max(0, self.total_frames - 1))
        self.timeline_slider.setValue(0)
        self.current_frame_idx = 0

        # Сброс холста
        self.canvas.strokes.clear()
        self.canvas.undo_stack.clear()

        # Отображение первого кадра
        self._seek_to_frame(0)
        self.setWindowTitle(f"Pro Video Player — {os.path.basename(file_path)}")

    def _seek_to_frame(self, frame_idx):
        if self.cap is None or not self.cap.isOpened():
            return

        frame_idx = max(0, min(self.total_frames - 1, frame_idx))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.cap.read()
        if ret and frame is not None:
            self.current_frame_idx = frame_idx
            self.canvas.set_frame(frame)
            self._update_time_label()
            if not self.timeline_slider.isSliderDown():
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(frame_idx)
                self.timeline_slider.blockSignals(False)

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

        interval = int(1000.0 / (self.fps * self.playback_speed))
        interval = max(5, interval)
        self.play_timer.start(interval)
        self.is_playing = True
        self.btn_play_pause.setText("⏸ Пауза")

    def pause(self):
        self.play_timer.stop()
        self.is_playing = False
        self.btn_play_pause.setText("▶ Пуск")

    def _on_play_tick(self):
        if self.cap is None or not self.cap.isOpened():
            self.pause()
            return

        next_frame = self.current_frame_idx + 1
        if next_frame >= self.total_frames:
            if self.is_looping:
                next_frame = 0
            else:
                self.pause()
                return

        ret, frame = self.cap.read()
        if ret and frame is not None:
            self.current_frame_idx = next_frame
            self.canvas.set_frame(frame)
            self._update_time_label()
            self.timeline_slider.blockSignals(True)
            self.timeline_slider.setValue(next_frame)
            self.timeline_slider.blockSignals(False)
        else:
            if self.is_looping:
                self._seek_to_frame(0)
            else:
                self.pause()

    def step_frame(self, delta):
        """Покадровый переход вперед (+1) или назад (-1)"""
        self.pause()
        target = self.current_frame_idx + delta
        self._seek_to_frame(target)

    def seek_seconds(self, delta_sec):
        """Перемотка на указанное число секунд"""
        delta_frames = int(delta_sec * self.fps)
        self._seek_to_frame(self.current_frame_idx + delta_frames)

    def _toggle_loop(self):
        self.is_looping = self.btn_loop.isChecked()

    def _on_speed_changed(self, text):
        speed_str = text.replace("x", "")
        try:
            self.playback_speed = float(speed_str)
            if self.is_playing:
                interval = int(1000.0 / (self.fps * self.playback_speed))
                self.play_timer.setInterval(max(5, interval))
        except ValueError:
            pass

    # --- Обработчики таймлайна ---

    def _on_slider_pressed(self):
        self._was_playing_before_scrub = self.is_playing
        self.pause()

    def _on_slider_moved(self, val):
        self._seek_to_frame(val)

    def _on_slider_released(self):
        val = self.timeline_slider.value()
        self._seek_to_frame(val)
        if getattr(self, '_was_playing_before_scrub', False):
            self.play()

    def _format_time(self, seconds):
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins:02d}:{secs:05.2f}"

    def _update_time_label(self):
        if self.fps > 0 and self.total_frames > 0:
            cur_sec = self.current_frame_idx / self.fps
            tot_sec = self.total_frames / self.fps
            self.lbl_time_info.setText(
                f"{self._format_time(cur_sec)} / {self._format_time(tot_sec)}  |  Кадр: {self.current_frame_idx + 1} / {self.total_frames}"
            )
        else:
            self.lbl_time_info.setText("00:00.00 / 00:00.00  |  Кадр: 0 / 0")

    def _apply_dark_theme(self):
        """Премиальный темный интерфейс"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #18181E;
            }
            #TopToolbar, #BottomPanel {
                background-color: #21212B;
                border: 1px solid #2D2D3B;
                border-radius: 8px;
            }
            QLabel {
                color: #D3D3E0;
                font-size: 12px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QPushButton {
                background-color: #2C2C3A;
                color: #E2E2EC;
                border: 1px solid #3E3E50;
                border-radius: 5px;
                padding: 5px 10px;
                font-weight: 500;
                font-size: 12px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QPushButton:hover {
                background-color: #39394B;
                border-color: #55556B;
            }
            QPushButton:pressed {
                background-color: #1F1F29;
            }
            QPushButton:checked {
                background-color: #007AFF;
                color: #FFFFFF;
                border-color: #2B8CFF;
            }
            #PlayButton {
                background-color: #248A3D;
                font-weight: bold;
                min-width: 80px;
            }
            #PlayButton:hover {
                background-color: #2DAC4D;
            }
            QSpinBox, QComboBox {
                background-color: #2C2C3A;
                color: #E2E2EC;
                border: 1px solid #3E3E50;
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 12px;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #2C2C3A;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #007AFF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 1px solid #007AFF;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #007AFF;
            }
        """)


def main():
    app = QApplication(sys.argv)
    initial_file = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        initial_file = sys.argv[1]
    elif os.path.exists("swapped_preview_test.mp4"):
        initial_file = "swapped_preview_test.mp4"

    player = VideoPlayerWindow(initial_file)
    player.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
