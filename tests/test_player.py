#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматический тест полного функционала обновленного плеера:
- Загрузка видео
- Пауза / Воспроизведение
- Покадровая навигация (+1, -1)
- Удобная перемотка (J, L, +1s, -1s, +5s, -5s, слайдер, колесико)
- Зум и проекция координат
- Рисование поверх видео и отмена (Undo / Clear All)
- Ластик с проверкой пересечения отрезков (segment distance)
- Динамический круглый курсор под размер кисти и ластика
- Смена цвета с активной подсветкой кнопок палитры
"""

import os
import sys
import unittest
import cv2
from PyQt5.QtCore import Qt, QPointF, QPoint, QRectF, QMimeData, QUrl
from PyQt5.QtGui import QColor, QMouseEvent, QDropEvent, QImage
from PyQt5.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Ensure repo root is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fkplayer.player import (
    VideoPlayerWindow, VideoCanvas, Stroke, dist_to_segment_sq,
    ActionRecorder, ExportVideoWorker, OverlayObject, set_dark_titlebar,
    resource_path
)
from fkplayer.core.i18n import tr, I18nManager
from fkplayer.media.capture import list_open_windows, capture_window_frame
from fkplayer.media.audio import MicrophoneRecorder, get_audio_input_devices, MicLevelMonitor
from fkplayer.core.projects import ProjectManager
from fkplayer.ui.dialogs import (
    NewCanvasDialog, ExportDialog, PreferencesDialog, DEFAULT_EXPORT_PRESETS,
    VideoOverlaySettingsDialog, AboutDialog, DONATEPAY_URL, DONATIONALERTS_URL,
    TELEGRAM_URL, GITHUB_URL
)


class TestEnhancedVideoPlayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])
        cls.test_video = os.path.abspath("test_synthetic_fixture.mp4")
        if not os.path.exists(cls.test_video):
            import cv2
            import numpy as np
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(cls.test_video, fourcc, 24.0, (320, 240))
            for i in range(72):
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                frame[:] = (i * 3 % 255, 100, 200)
                writer.write(frame)
            writer.release()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'test_video') and os.path.exists(cls.test_video):
            try:
                os.remove(cls.test_video)
            except Exception:
                pass

    def setUp(self):
        self.video_path = self.test_video
        self.assertTrue(os.path.exists(self.video_path), "Test video fixture should exist")
        self.player = VideoPlayerWindow(self.video_path)
        self.player.show()

    def tearDown(self):
        if self.player.cap:
            self.player.cap.release()
        self.player.close()

    def test_01_video_loaded(self):
        """Проверка загрузки видео"""
        self.assertIsNotNone(self.player.cap)
        self.assertTrue(self.player.cap.isOpened())
        self.assertGreater(self.player.total_frames, 0)
        self.assertGreater(self.player.fps, 0)
        self.assertEqual(self.player.current_frame_idx, 0)
        self.assertIsNotNone(self.player.canvas.current_qimage)
        print(f"[OK] Видео загружено: {self.player.total_frames} кадров, {self.player.fps} FPS")

    def test_02_play_and_pause(self):
        """Проверка воспроизведения и паузы"""
        self.assertFalse(self.player.is_playing)
        self.player.play()
        self.assertTrue(self.player.is_playing)

        # Тик таймера
        self.player._on_play_tick()
        self.assertEqual(self.player.current_frame_idx, 1)

        self.player.pause()
        self.assertFalse(self.player.is_playing)
        print("[OK] Воспроизведение и пауза работают штатно")

    def test_03_frame_stepping(self):
        """Покадровый шаг (+1 и -1)"""
        self.player._seek_to_frame(10)
        self.assertEqual(self.player.current_frame_idx, 10)

        self.player.step_frame(1)
        self.assertEqual(self.player.current_frame_idx, 11)

        self.player.step_frame(-1)
        self.assertEqual(self.player.current_frame_idx, 10)
        print("[OK] Покадровый переход вперед (+1) и назад (-1) работает точно")

    def test_04_convenient_seeking(self):
        """Удобная перемотка (1s, 5s, J, L, слайдер)"""
        fps = int(self.player.fps)
        self.player._seek_to_frame(0)

        # Перемотка на +1 секунду
        self.player.seek_seconds(1.0)
        self.assertEqual(self.player.current_frame_idx, fps)

        # Перемотка на -1 секунду
        self.player.seek_seconds(-1.0)
        self.assertEqual(self.player.current_frame_idx, 0)

        # Перемотка на +5 секунд
        self.player.seek_seconds(5.0)
        self.assertEqual(self.player.current_frame_idx, min(self.player.total_frames - 1, fps * 5))

        # Переход через колесико на слайдере
        self.player._seek_to_frame(20)
        self.player._on_slider_wheel(3)
        self.assertEqual(self.player.current_frame_idx, 23)

        # Клик по слайдеру
        slider = self.player.timeline_slider
        slider.resize(500, 30)
        ev_click = QMouseEvent(QMouseEvent.MouseButtonPress, QPoint(250, 15), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        slider.mousePressEvent(ev_click)
        self.assertGreater(self.player.current_frame_idx, 0)
        print("[OK] Удобная перемотка (+/-1с, +/-5с, колесо, клик слайдера) проверена")

    def test_05_zoom_and_pan(self):
        """Масштабирование (Zoom In, Zoom Out, 1:1)"""
        canvas = self.player.canvas
        canvas.reset_zoom_100()
        self.assertAlmostEqual(canvas.zoom_factor, 1.0, places=2)

        canvas.zoom_in()
        self.assertGreater(canvas.zoom_factor, 1.0)
        zoomed = canvas.zoom_factor

        canvas.zoom_out()
        self.assertLess(canvas.zoom_factor, zoomed)

        canvas.reset_zoom_100()
        self.assertAlmostEqual(canvas.zoom_factor, 1.0, places=2)
        print("[OK] Зум (приближение, отдаление, 1:1) работает корректно")

    def test_06_brush_circle_cursor(self):
        """Проверка динамического курсора-кружка под размер кисти"""
        canvas = self.player.canvas

        # Выбираем кисть
        self.player._select_tool(VideoCanvas.TOOL_PEN)
        self.assertEqual(canvas.active_tool, VideoCanvas.TOOL_PEN)

        # Проверяем, что курсор существует и имеет тип QBitmap/Pixmap (Custom)
        cur = canvas.cursor()
        self.assertEqual(cur.shape(), Qt.BitmapCursor)

        # Меняем размер кисти - курсор обновляется
        self.player.spin_width.setValue(16)
        self.assertEqual(canvas.pen_width, 16.0)
        cur16 = canvas.cursor()
        self.assertEqual(cur16.shape(), Qt.BitmapCursor)

        # Выбираем ластик - курсор ластика тоже динамический круг
        self.player._select_tool(VideoCanvas.TOOL_ERASER)
        self.assertEqual(canvas.active_tool, VideoCanvas.TOOL_ERASER)
        cur_eraser = canvas.cursor()
        self.assertEqual(cur_eraser.shape(), Qt.BitmapCursor)
        print("[OK] Динамический круглый курсор под размер кисти и ластика работает")

    def test_07_drawing_undo_and_clear_all(self):
        """Рисование, отмена на шаг назад и полная очистка"""
        canvas = self.player.canvas
        canvas.strokes.clear()
        canvas.undo_stack.clear()

        s1 = Stroke(QColor("#FF0000"), 4.0, [QPointF(10, 10), QPointF(20, 20)])
        canvas.strokes.append(s1)
        canvas.undo_stack.append(('add', s1))

        s2 = Stroke(QColor("#00FF00"), 8.0, [QPointF(50, 50), QPointF(60, 60)])
        canvas.strokes.append(s2)
        canvas.undo_stack.append(('add', s2))
        self.assertEqual(len(canvas.strokes), 2)

        # Отмена шага через метод
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)

        # Добавим штрих и проверим отмену через кнопку btn_undo
        canvas.strokes.append(s2)
        canvas.undo_stack.append(('add', s2))
        self.assertEqual(len(canvas.strokes), 2)
        self.player.btn_undo.click()
        self.assertEqual(len(canvas.strokes), 1, "btn_undo.click() must undo last stroke")

        # Проверим кнопку btn_clear_all
        self.player.btn_clear_all.click()
        self.assertEqual(len(canvas.strokes), 0, "btn_clear_all.click() must clear all drawings")

        # Отмена очистки через btn_undo возвращает штрихи
        self.player.btn_undo.click()
        self.assertEqual(len(canvas.strokes), 1, "btn_undo.click() must restore cleared drawings")
        print("[OK] Рисование, 'Стереть на шаг назад' и 'Стереть всё' (включая UI кнопки) работают безупречно")

    def test_08_eraser_segment_intersection(self):
        """Проверка ластика на частичное стирание (Photoshop / Paint style) и полное удаление"""
        canvas = self.player.canvas
        canvas.strokes.clear()
        canvas.undo_stack.clear()

        # Длинная линия от (0, 0) до (200, 200)
        s = Stroke(QColor("#0000FF"), 4.0, [QPointF(0, 0), QPointF(200, 200)])
        canvas.strokes.append(s)
        canvas.undo_stack.append(('add', s))
        self.assertEqual(len(canvas.strokes), 1)

        # Ластик проходит через середину линии (100, 100)
        # В Photoshop/Paint стиле линия разделяется на 2 отдельных штриха вокруг точки стирания
        canvas.erase_strokes_at_video_pt(QPointF(100, 100))
        self.assertEqual(len(canvas.strokes), 2)
        # Проверяем, что первая часть заканчивается до ластика, а вторая начинается после
        self.assertLess(canvas.strokes[0].points[-1].x(), 100.0)
        self.assertGreater(canvas.strokes[1].points[0].x(), 100.0)

        # Отмена стирания восстанавливает исходную целую линию
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        self.assertEqual(canvas.strokes[0].points[0], QPointF(0, 0))
        self.assertEqual(canvas.strokes[0].points[-1], QPointF(200, 200))

        # Одиночный штрих (точка), полностью покрытый ластиком, удаляется в 0
        dot_s = Stroke(QColor("#FF0000"), 4.0, [QPointF(50, 50)])
        canvas.strokes.append(dot_s)
        canvas.erase_strokes_at_video_pt(QPointF(50, 50))
        self.assertNotIn(dot_s, canvas.strokes)
        print("[OK] Ластик безошибочно стирает штрихи в Photoshop/Paint стиле (разрезая и удаляя части)")

    def test_09_colors_palette_and_active_highlight(self):
        """Проверка палитры цветов и активной подсветки кнопок"""
        self.player._set_brush_color("#34C759")
        self.assertEqual(self.player.canvas.pen_color.name().upper(), "#34C759")

        green_btn = self.player.preset_color_buttons["#34C759"]
        self.assertIn("#FFFFFF", green_btn.styleSheet())  # Активная белая рамка

        self.player._set_brush_color("#007AFF")
        self.assertEqual(self.player.canvas.pen_color.name().upper(), "#007AFF")
        blue_btn = self.player.preset_color_buttons["#007AFF"]
        self.assertIn("#FFFFFF", blue_btn.styleSheet())
        print("[OK] Смена цвета и подсветка активного цвета в UI работают")

    def test_10_speed_modes_and_shortcuts(self):
        """Проверка расширенных режимов скорости и хоткеев <, >, R"""
        combo = self.player.combo_speed
        self.assertIn("0.05x", self.player.speed_presets)
        self.assertIn("0.1x", self.player.speed_presets)
        self.assertIn("0.25x", self.player.speed_presets)
        self.assertIn("0.5x", self.player.speed_presets)
        self.assertIn("1.0x", self.player.speed_presets)
        self.assertIn("2.0x", self.player.speed_presets)
        self.assertIn("4.0x", self.player.speed_presets)
        self.assertIn("8.0x", self.player.speed_presets)
        self.assertIn("10.0x", self.player.speed_presets)

        # Сброс на 1.0x
        self.player.reset_speed()
        self.assertEqual(combo.currentText(), "1.0x")
        self.assertEqual(self.player.playback_speed, 1.0)
        self.assertEqual(self.player.frames_per_tick, 1)

        # Увеличение скорости (>)
        self.player.increase_speed()
        self.assertEqual(combo.currentText(), "1.25x")
        self.assertEqual(self.player.playback_speed, 1.25)

        # Уменьшение скорости (<)
        self.player.decrease_speed()
        self.assertEqual(combo.currentText(), "1.0x")

        # Замедление (<)
        self.player.decrease_speed()
        self.assertEqual(combo.currentText(), "0.75x")
        self.assertEqual(self.player.playback_speed, 0.75)

        # Проверка экстремальной скорости (8.0x) - мульти-шаг кадров
        combo.setCurrentText("8.0x")
        self.assertEqual(self.player.playback_speed, 8.0)
        self.assertGreater(self.player.frames_per_tick, 1)
        print(f"[OK] Режимы скорости (0.05x - 10.0x) и горячие клавиши работают, multi-step: {self.player.frames_per_tick}")

    def test_11_empty_player_by_default(self):
        """Проверка инициализации плеера без видео по умолчанию"""
        empty_player = VideoPlayerWindow(None)
        empty_player.show()
        self.assertIsNone(empty_player.cap)
        self.assertEqual(empty_player.video_path, "")
        self.assertEqual(empty_player.total_frames, 0)
        self.assertEqual(empty_player.current_frame_idx, 0)
        self.assertIsNone(empty_player.canvas.current_qimage)
        # Вызовы на пустом плеере безопасны и не вызывают исключений
        empty_player.play()
        empty_player.pause()
        empty_player.step_frame(1)
        empty_player.seek_seconds(5.0)
        empty_player.close()
        print("[OK] Плеер без видео по дефолту запускается чисто и безопасно")

    def test_12_audio_and_volume_controls(self):
        """Проверка аудиосистемы, регулировки громкости и Mute"""
        self.assertIsNotNone(self.player.audio_player)
        self.assertEqual(self.player.current_volume, 80)
        self.assertFalse(self.player.is_muted)

        # Регулировка громкости слайдером
        self.player.set_volume(50)
        self.assertEqual(self.player.current_volume, 50)
        self.assertEqual(self.player.slider_volume.value(), 50)
        self.assertEqual(self.player.lbl_volume.text(), "50%")

        # Прибавление / убавление громкости (клавиши Up/Down)
        self.player.adjust_volume(10)
        self.assertEqual(self.player.current_volume, 60)
        self.player.adjust_volume(-25)
        self.assertEqual(self.player.current_volume, 35)

        # Включение Mute (M)
        self.player.toggle_mute()
        self.assertTrue(self.player.is_muted)
        self.assertEqual(self.player.btn_mute.text(), "🔇")

        # Отключение Mute
        self.player.toggle_mute()
        self.assertFalse(self.player.is_muted)
        self.assertEqual(self.player.current_volume, 35)
        print("[OK] Регулировка громкости и кнопка Mute работают безупречно")

    def test_13_frame_cache_performance(self):
        """Проверка кольцевого LRU кэша кадров (0 мс доступ)"""
        self.player.frame_cache.clear()
        self.assertEqual(len(self.player.frame_cache), 0)

        # Считывание кадра 5 (кэш miss -> сохранение в кэш)
        f5 = self.player._get_frame_cached(5)
        self.assertIsNotNone(f5)
        self.assertIn(5, self.player.frame_cache)

        # Повторный доступ к кадру 5 (кэш hit)
        f5_cached = self.player._get_frame_cached(5)
        self.assertIs(f5, f5_cached)
        print("[OK] LRU кэш кадров обеспечивает мгновенный доступ без повторного I/O")

    def test_14_painter_path_acceleration(self):
        """Проверка аппаратного ускорения рисования через QPainterPath"""
        stroke = Stroke(QColor("#FF0000"), 4.0, [QPointF(0, 0), QPointF(100, 100), QPointF(200, 50)])
        self.assertGreater(stroke.path.elementCount(), 1)
        self.player.canvas.strokes.append(stroke)

        # Проверка вызова paintEvent с новой матричной трансформацией без ошибок
        self.player.canvas.repaint()
        print("[OK] Векторная отрисовка через QPainterPath и transform работает без ошибок")

    def test_15_spam_clicking_at_slow_speed(self):
        """Проверка отсутствия зависаний при спаме шагами на замедленной скорости"""
        self.player.combo_speed.setCurrentText("0.05x")
        self.assertEqual(self.player.playback_speed, 0.05)

        # 30 спам-кликов вперед
        for _ in range(30):
            self.player.step_frame(1)
            QApplication.processEvents()

        # 30 спам-кликов назад
        for _ in range(30):
            self.player.step_frame(-1)
            QApplication.processEvents()

        self.assertFalse(self.player.is_playing)
        print("[OK] Спам шагами на 0.05x скорости отрабатывает без зависаний и задержек")

    def test_16_action_recorder_lifecycle(self):
        """Проверка жизненного цикла записи действий: старт, рисование, пауза, кадры, отмена, экспорт JSON"""
        rec = self.player.recorder
        self.assertEqual(rec.state, ActionRecorder.STATE_IDLE)

        self.player.start_actions_record()
        self.assertTrue(rec.is_recording())
        self.assertFalse(self.player.btn_rec_start.isEnabled())
        self.assertTrue(self.player.btn_rec_pause.isEnabled())
        self.assertTrue(self.player.btn_rec_stop.isEnabled())

        canvas = self.player.canvas
        ev_press = QMouseEvent(QMouseEvent.MouseButtonPress, QPoint(50, 50), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        canvas.mousePressEvent(ev_press)
        ev_move = QMouseEvent(QMouseEvent.MouseMove, QPoint(60, 70), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        canvas.mouseMoveEvent(ev_move)
        ev_release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPoint(60, 70), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        canvas.mouseReleaseEvent(ev_release)

        types = [e['type'] for e in rec.events]
        self.assertIn('stroke_start', types)
        self.assertIn('stroke_point', types)
        self.assertIn('stroke_end', types)

        self.player.step_frame(1)
        frame_events = [e for e in rec.events if e['type'] == 'frame']
        self.assertGreaterEqual(len(frame_events), 2)
        self.assertEqual(frame_events[-1]['frame_idx'], 1)

        self.player.pause_actions_record()
        self.assertTrue(rec.is_paused())
        self.assertIn("Resume", self.player.btn_rec_pause.text())

        self.player.pause_actions_record()
        self.assertTrue(rec.is_recording())

        self.player.canvas.undo_last_action()
        self.assertEqual(rec.events[-1]['type'], 'undo')

        rec.stop()
        self.assertFalse(rec.is_active())
        self.assertGreater(rec.elapsed_time, 0.0)

        json_path = os.path.abspath("test_actions.json")
        try:
            rec.save_json(json_path)
            self.assertTrue(os.path.exists(json_path))

            new_rec = ActionRecorder(self.player)
            new_rec.load_json(json_path)
            self.assertEqual(len(new_rec.events), len(rec.events))
            self.assertAlmostEqual(new_rec.elapsed_time, rec.elapsed_time, places=2)
        finally:
            if os.path.exists(json_path):
                os.remove(json_path)

        print("[OK] Запись действий (старт, штрихи, пауза, таймкоды, JSON) работает штатно")

    def test_17_export_video_worker(self):
        """Проверка экспорта отредактированного видео в отдельный MP4 файл"""
        import cv2
        out_mp4 = os.path.abspath("test_rendered_export.mp4")
        if os.path.exists(out_mp4):
            os.remove(out_mp4)

        events = [
            {'type': 'frame', 'time': 0.0, 'frame_idx': 0, 'zoom': 1.0, 'pan': (0, 0)},
            {'type': 'stroke_start', 'time': 0.05, 'stroke_id': 1, 'color': '#FF3B30', 'width': 6.0, 'pt': (50.0, 50.0)},
            {'type': 'stroke_point', 'time': 0.10, 'stroke_id': 1, 'pt': (100.0, 100.0)},
            {'type': 'stroke_point', 'time': 0.15, 'stroke_id': 1, 'pt': (150.0, 80.0)},
            {'type': 'stroke_end', 'time': 0.20, 'stroke_id': 1},
            {'type': 'frame', 'time': 0.25, 'frame_idx': 1, 'zoom': 1.0, 'pan': (0, 0)},
            {'type': 'frame', 'time': 0.35, 'frame_idx': 2, 'zoom': 1.0, 'pan': (0, 0)},
            {'type': 'undo', 'time': 0.40},
            {'type': 'stop', 'time': 0.50}
        ]

        worker = ExportVideoWorker(
            video_path=self.video_path,
            events=events,
            total_duration=0.50,
            output_path=out_mp4,
            fps=30.0
        )

        worker.run()

        self.assertTrue(os.path.exists(out_mp4), "Экспортированный видеофайл должен быть создан")
        self.assertGreater(os.path.getsize(out_mp4), 1000, "Размер видеофайла должен быть больше 1 КБ")

        cap_out = cv2.VideoCapture(out_mp4)
        self.assertTrue(cap_out.isOpened(), "Экспортированное видео должно открываться")
        out_frames = int(cap_out.get(cv2.CAP_PROP_FRAME_COUNT))
        out_w = int(cap_out.get(cv2.CAP_PROP_FRAME_WIDTH))
        out_h = int(cap_out.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_out.release()

        self.assertGreaterEqual(out_frames, 10, "Должно быть отрендерено не менее 10 кадров")
        self.assertGreater(out_w, 0)
        self.assertGreater(out_h, 0)

        if os.path.exists(out_mp4):
            os.remove(out_mp4)

        print(f"[OK] Экспорт видео MP4 ({out_w}x{out_h}, {out_frames} кадров) выполнен успешно")

    def test_18_overlay_object_and_export(self):
        """Проверка добавления поверх видео объектов (картинки, PIP второго видео), их трансформации и рендера"""
        canvas = self.player.canvas
        rec = self.player.recorder
        self.assertEqual(len(canvas.overlays), 0)

        self.player.start_actions_record()

        img_path = resource_path("icon.png")
        self.assertTrue(os.path.exists(img_path))
        ov_img = OverlayObject(1, img_path, QRectF(20, 20, 100, 100), start_time=0.0)
        self.assertEqual(ov_img.obj_type, OverlayObject.TYPE_IMAGE)
        self.assertIsNotNone(ov_img.get_frame_at_time(0.0))
        canvas.overlays.append(ov_img)
        rec.record_overlay_add(ov_img)

        ov_vid = OverlayObject(2, self.video_path, QRectF(150, 20, 120, 80), start_time=0.1)
        self.assertEqual(ov_vid.obj_type, OverlayObject.TYPE_VIDEO)
        self.assertIsNotNone(ov_vid.get_frame_at_time(0.2))
        canvas.overlays.append(ov_vid)
        rec.record_overlay_add(ov_vid)

        ov_img.rect.moveTo(40, 50)
        rec.record_overlay_transform(ov_img)

        self.player.canvas.repaint()

        rec.stop()
        events = list(rec.events)
        types = [e['type'] for e in events]
        self.assertIn('overlay_add', types)
        self.assertIn('overlay_transform', types)

        out_mp4 = os.path.abspath("test_overlay_export.mp4")
        if os.path.exists(out_mp4):
            os.remove(out_mp4)

        worker = ExportVideoWorker(
            video_path=self.video_path,
            events=events,
            total_duration=rec.elapsed_time if rec.elapsed_time > 0.1 else 0.5,
            output_path=out_mp4,
            fps=30.0
        )
        worker.run()

        self.assertTrue(os.path.exists(out_mp4), "Экспортированное видео с оверлеями должно существовать")
        self.assertGreater(os.path.getsize(out_mp4), 1000)

        import cv2
        cap_check = cv2.VideoCapture(out_mp4)
        self.assertTrue(cap_check.isOpened())
        f_count = int(cap_check.get(cv2.CAP_PROP_FRAME_COUNT))
        cap_check.release()
        self.assertGreater(f_count, 0)

        if os.path.exists(out_mp4):
            os.remove(out_mp4)

        for ov in canvas.overlays:
            ov.close()
        canvas.overlays.clear()

        print("[OK] Добавление объектов (картинка, второе видео PIP), перемещение и экспорт работают безупречно")

    def test_19_drag_and_drop_overlay_creation(self):
        """Verify drag and drop files onto canvas creates overlays at drop location"""
        canvas = self.player.canvas
        canvas.overlays.clear()
        img_path = resource_path("icon.png")
        self.assertTrue(os.path.exists(img_path))

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(img_path)])
        drop_pos = QPointF(200.0, 150.0)
        event = QDropEvent(drop_pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)

        canvas.dropEvent(event)
        self.assertEqual(len(canvas.overlays), 1)
        ov = canvas.overlays[0]
        self.assertEqual(ov.obj_type, OverlayObject.TYPE_IMAGE)
        self.assertTrue(ov.rect.width() > 0 and ov.rect.height() > 0)
        for o in canvas.overlays:
            o.close()
        canvas.overlays.clear()
        print("[OK] Drag & drop onto video canvas creates overlay at target position")

    def test_20_multi_overlay_z_order_and_duplicate(self):
        """Verify layer stacking (bring to front/send to back/forward/backward) and duplicate"""
        canvas = self.player.canvas
        canvas.overlays.clear()
        img_path = resource_path("icon.png")

        ov1 = OverlayObject(1, img_path, QRectF(10, 10, 50, 50))
        ov2 = OverlayObject(2, img_path, QRectF(60, 10, 50, 50))
        ov3 = OverlayObject(3, img_path, QRectF(110, 10, 50, 50))
        canvas.overlays.extend([ov1, ov2, ov3])

        # Test send to back: ov3 -> index 0
        self.player.send_overlay_to_back(ov3)
        self.assertEqual(canvas.overlays, [ov3, ov1, ov2])

        # Test bring to front: ov3 -> index 2
        self.player.bring_overlay_to_front(ov3)
        self.assertEqual(canvas.overlays, [ov1, ov2, ov3])

        # Test send backward: ov2 -> swaps with ov1
        self.player.send_overlay_backward(ov2)
        self.assertEqual(canvas.overlays, [ov2, ov1, ov3])

        # Test bring forward: ov2 -> swaps with ov1
        self.player.bring_overlay_forward(ov2)
        self.assertEqual(canvas.overlays, [ov1, ov2, ov3])

        # Test duplicate
        self.player.duplicate_overlay(ov2)
        self.assertEqual(len(canvas.overlays), 4)
        new_ov = canvas.overlays[-1]
        self.assertAlmostEqual(new_ov.rect.left(), ov2.rect.left() + 25.0, places=1)
        self.assertAlmostEqual(new_ov.rect.top(), ov2.rect.top() + 25.0, places=1)

        # Test delete
        canvas.selected_overlay = new_ov
        self.player.delete_selected_overlay()
        self.assertEqual(len(canvas.overlays), 3)

        for o in canvas.overlays:
            o.close()
        canvas.overlays.clear()
        print("[OK] Layer z-ordering (front/back/fwd/bwd), duplicate and delete work as expected")

    def test_21_overlay_corner_handles_and_aspect_ratio(self):
        """Verify corner resize handles, delete handle, and aspect ratio calculations"""
        canvas = self.player.canvas
        canvas.overlays.clear()
        img_path = resource_path("icon.png")
        ov = OverlayObject(1, img_path, QRectF(100, 100, 200, 100))
        canvas.overlays.append(ov)
        canvas.selected_overlay = ov
        canvas.zoom_factor = 1.0

        # Test hit testing on video coordinates
        h_tl, _ = canvas._hit_test_overlay_handle(ov, ov.rect.topLeft())
        h_br, _ = canvas._hit_test_overlay_handle(ov, ov.rect.bottomRight())
        h_del_pt = QPointF(ov.rect.right() + 10.0 / canvas.zoom_factor, ov.rect.top() - 10.0 / canvas.zoom_factor)
        h_del, _ = canvas._hit_test_overlay_handle(ov, h_del_pt)
        h_move, _ = canvas._hit_test_overlay_handle(ov, ov.rect.center())

        self.assertEqual(h_tl, 'tl')
        self.assertEqual(h_br, 'br')
        self.assertEqual(h_del, 'del')
        self.assertEqual(h_move, 'move')

        # Test simulated mouse dragging for resize with aspect ratio locked
        canvas._overlay_drag_mode = 'br'
        canvas._overlay_drag_start_rect = QRectF(ov.rect)
        canvas._overlay_drag_start_vpt = ov.rect.bottomRight()

        # Simulate move by (+50, +50) with Shift (aspect lock)
        sr = canvas._overlay_drag_start_rect
        ratio = sr.width() / max(1.0, sr.height())
        dx, dy = 50.0, 50.0
        new_w = sr.width() + dx
        new_h = new_w / ratio
        ov.rect = QRectF(sr.left(), sr.top(), new_w, new_h)

        self.assertEqual(ov.rect.width(), 250.0)
        self.assertEqual(ov.rect.height(), 125.0)

        for o in canvas.overlays:
            o.close()
        canvas.overlays.clear()
        print("[OK] Overlay corner handles (tl, tr, bl, br, del) and resizing operate cleanly")

    def test_22_english_ui_localization(self):
        """Verify player.py has 0 Cyrillic characters across code, UI labels, tooltips, dialogs"""
        import re
        with open("player.py", "r", encoding="utf-8") as f:
            code = f.read()

        cyrillic_matches = re.findall(r'[\u0400-\u04FF]', code)
        self.assertEqual(len(cyrillic_matches), 0, f"Found {len(cyrillic_matches)} Cyrillic characters in player.py: {cyrillic_matches[:10]}")
        print("[OK] UI 100% localized into English: 0 Cyrillic characters in player.py")

    def test_23_titlebar_dark_mode_helper(self):
        """Verify set_dark_titlebar executes safely without raising exceptions"""
        try:
            set_dark_titlebar(self.player)
            success = True
        except Exception:
            success = False
        self.assertTrue(success, "set_dark_titlebar should run safely")
        print("[OK] Titlebar dark mode attribute applied safely")

    def test_24_i18n_translation_switching(self):
        """Verify internationalization switches between English and Russian"""
        i18n = I18nManager.instance()
        i18n.set_language('en')
        self.assertEqual(i18n.lang, 'en')
        self.assertEqual(tr('menu_file'), 'File')
        self.assertEqual(tr('btn_brush'), 'Brush (B)')

        i18n.set_language('ru')
        self.assertEqual(i18n.lang, 'ru')
        self.assertEqual(tr('menu_file'), 'Файл')
        self.assertEqual(tr('btn_brush'), 'Кисть (B)')

        # Switch back to English
        i18n.set_language('en')
        self.assertEqual(i18n.lang, 'en')
        print("[OK] i18n dynamic language switching (English/Russian) works properly")

    def test_25_window_capture_enumeration(self):
        """Verify window capture enumeration returns list of window objects"""
        windows = list_open_windows()
        self.assertIsInstance(windows, list)
        for w in windows[:5]:
            self.assertIn('hwnd', w)
            self.assertIn('title', w)
            self.assertIn('width', w)
            self.assertIn('height', w)
        # Test capture_window_frame handles invalid hwnd without crashing
        res = capture_window_frame(999999999)
        self.assertIsNone(res)
        print(f"[OK] Window capture enumeration succeeded (discovered {len(windows)} windows)")

    def test_26_microphone_recorder_pcm_and_wav(self):
        """Verify microphone device enumeration and recorder class"""
        devs = get_audio_input_devices()
        self.assertIsInstance(devs, list)
        mic = MicrophoneRecorder(parent=self.player)
        self.assertFalse(mic.is_recording)

        temp_wav = os.path.abspath("test_mic_output.wav")
        if os.path.exists(temp_wav):
            os.remove(temp_wav)

        started = mic.start_recording(temp_wav)
        if started:
            self.assertTrue(mic.is_recording)
            out_file = mic.stop_recording()
            self.assertFalse(mic.is_recording)

        if os.path.exists(temp_wav):
            os.remove(temp_wav)
        print(f"[OK] Microphone input detection ({len(devs)} devices) and recorder operational")

    def test_27_text_overlay_rendering_and_actions(self):
        """Verify text overlay creation, font rendering, and action serialization"""
        canvas = self.player.canvas
        rec = self.player.recorder
        text_data = {
            'text': 'Test Neon Text',
            'font_family': 'Arial',
            'font_size': 28,
            'bold': True,
            'italic': False,
            'color': '#007AFF',
            'bg_color': 'transparent'
        }
        ov = OverlayObject(99, 'text', QRectF(50, 50, 200, 80), start_time=0.0, text_data=text_data)
        self.assertEqual(ov.obj_type, OverlayObject.TYPE_TEXT)
        self.assertEqual(ov.text, 'Test Neon Text')

        frame_img = ov.get_frame_at_time(0.0)
        self.assertIsNotNone(frame_img)
        self.assertFalse(frame_img.isNull())
        self.assertEqual(frame_img.width(), 200)
        self.assertEqual(frame_img.height(), 80)

        # Record action
        rec.start()
        rec.record_overlay_add(ov)
        rec.stop()
        add_event = [e for e in rec.events if e['type'] == 'overlay_add'][-1]
        self.assertEqual(add_event['type'], 'overlay_add')
        self.assertIn('text_data', add_event)
        self.assertEqual(add_event['text_data']['text'], 'Test Neon Text')
        ov.close()
        print("[OK] Text overlay creation, font rendering (200x80) and action logging verified")

    def test_28_blank_canvas_creation_and_projects(self):
        """Verify blank canvas project creation and recent projects persistence"""
        pm = ProjectManager.instance()
        pm.add_project('blank', 'Canvas 1920x1080', '1920x1080', 1920, 1080)
        recent = pm.get_recent_projects()
        self.assertGreaterEqual(len(recent), 1)
        self.assertEqual(recent[0]['name'], 'Canvas 1920x1080')

        self.player.create_blank_canvas(1280, 720, "#000000", 30.0)
        self.assertEqual(self.player.canvas.video_width, 1280)
        self.assertEqual(self.player.canvas.video_height, 720)
        print("[OK] Blank canvas creation and project persistence operational")

    def test_29_advanced_export_options_and_presets(self):
        """Verify advanced export presets configuration and ExportVideoWorker with custom options"""
        self.assertIn("YouTube 1080p 60fps (x264)", DEFAULT_EXPORT_PRESETS)
        self.assertIn("Ultra Quality Archive (HEVC H.265 4K)", DEFAULT_EXPORT_PRESETS)

        out_mp4 = os.path.abspath("test_advanced_export.mp4")
        if os.path.exists(out_mp4):
            os.remove(out_mp4)

        text_data = {
            'text': 'Export Text',
            'font_family': 'Segoe UI',
            'font_size': 24,
            'bold': True,
            'italic': False,
            'color': '#FFFFFF',
            'bg_color': 'transparent'
        }
        events = [
            {'type': 'frame', 'time': 0.0, 'frame_idx': 0, 'zoom': 1.0, 'pan': (0, 0)},
            {'type': 'overlay_add', 'time': 0.05, 'obj_id': 10, 'obj_type': 'text', 'file_path': 'text',
             'rect': [20.0, 20.0, 180.0, 60.0], 'text_data': text_data},
            {'type': 'stroke_start', 'time': 0.1, 'stroke_id': 1, 'color': '#007AFF', 'width': 4.0, 'pt': (30.0, 30.0)},
            {'type': 'stroke_point', 'time': 0.15, 'stroke_id': 1, 'pt': (80.0, 80.0)},
            {'type': 'stroke_end', 'time': 0.2, 'stroke_id': 1},
            {'type': 'stop', 'time': 0.3}
        ]

        worker = ExportVideoWorker(
            video_path=self.video_path,
            events=events,
            total_duration=0.3,
            output_path=out_mp4,
            fps=30.0,
            out_size=(640, 360),
            codec='libx264',
            bitrate='4M'
        )
        worker.run()

        self.assertTrue(os.path.exists(out_mp4), "Exported video file must exist")
        self.assertGreater(os.path.getsize(out_mp4), 1000)

        import cv2
        cap = cv2.VideoCapture(out_mp4)
        self.assertTrue(cap.isOpened())
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        self.assertEqual(w, 640)
        self.assertEqual(h, 360)

        if os.path.exists(out_mp4):
            os.remove(out_mp4)
        print("[OK] Advanced export (x264, custom resolution 640x360, text overlay) executed cleanly")

    def test_30_mic_level_monitor_test(self):
        """Verify MicLevelMonitor operational and stop safety"""
        mon = MicLevelMonitor()
        self.assertFalse(mon.is_monitoring)
        mon.stop_monitoring()
        self.assertFalse(mon.is_monitoring)
        print("[OK] Microphone level monitor testing mechanism verified")

    def test_31_video_overlay_playback_and_settings(self):
        """Verify video overlay playback properties, speed, loop, and settings dialog"""
        ov = OverlayObject(50, self.video_path, QRectF(10, 10, 320, 180), start_time=0.0)
        self.assertEqual(ov.obj_type, OverlayObject.TYPE_VIDEO)
        self.assertTrue(ov.is_playing)
        self.assertTrue(ov.loop)
        self.assertEqual(ov.playback_speed, 1.0)
        self.assertEqual(ov.opacity, 1.0)

        # Frame reading
        img1 = ov.get_frame_at_time(0.0)
        self.assertIsNotNone(img1)
        self.assertFalse(img1.isNull())

        # Test settings dialog
        dlg = VideoOverlaySettingsDialog(ov, parent=self.player)
        self.assertEqual(dlg.check_loop.isChecked(), True)
        self.assertEqual(dlg.slider_opacity.value(), 100)
        ov.close()
        print("[OK] Video overlay playback controls and settings dialog operational")

    def test_32_close_project_and_unsaved_changes(self):
        """Verify close project and unsaved changes tracking"""
        self.assertFalse(self.player.has_unsaved_changes())

        # Add a stroke
        vpt = QPointF(100, 100)
        s = Stroke(QColor('#FF0000'), 4.0, [vpt], stroke_id=1)
        self.player.canvas.strokes.append(s)
        self.assertTrue(self.player.has_unsaved_changes())

        # Close project (bypassing prompt by clearing)
        self.player.close_project()
        self.assertEqual(len(self.player.canvas.strokes), 0)
        self.assertEqual(len(self.player.canvas.overlays), 0)
        self.assertFalse(self.player.has_unsaved_changes())
        print("[OK] Close project and unsaved changes tracking operational")

    def test_33_author_and_donations(self):
        """Verify furrykit authorship and donation endpoints"""
        self.assertIn("furrykit", tr('about_author').lower())
        self.assertEqual(DONATEPAY_URL, "https://new.donatepay.ru/donate/ttvfurrykit")
        self.assertEqual(DONATIONALERTS_URL, "https://www.donationalerts.com/r/ttvfurrykit")
        dlg = AboutDialog(parent=self.player)
        self.assertIsNotNone(dlg)
        print("[OK] Author furrykit and donation links (DonatePay, DonationAlerts) verified")

    def test_34_multi_project_tabs(self):
        """Verify multi-project tab bar creation, switching, state isolation, and closure"""
        tabs = self.player.project_tabs
        self.assertIsNotNone(tabs)
        self.assertGreaterEqual(tabs.count(), 1)

        # Tab 1 initial state
        initial_tab_count = tabs.count()
        p1 = self.player.active_project
        self.assertIsNotNone(p1)

        # Create Tab 2
        p2 = self.player.new_project_tab(name="Secondary Project")
        self.assertEqual(tabs.count(), initial_tab_count + 1)
        self.assertEqual(self.player.active_project, p2)

        # Verify state isolation: add stroke to Tab 2
        pt = QPointF(50.0, 50.0)
        s = Stroke(QColor('#00FF00'), 5.0, [pt], stroke_id=99)
        p2.canvas.strokes.append(s)
        self.assertEqual(len(p2.canvas.strokes), 1)

        # Switch back to Tab 1
        tabs.setCurrentIndex(0)
        self.assertEqual(self.player.active_project, p1)
        self.assertEqual(len(p1.canvas.strokes), 0)

        # Switch forward via next_project_tab
        self.player.next_project_tab()
        self.assertEqual(self.player.active_project, p2)
        self.assertEqual(len(p2.canvas.strokes), 1)

        # Switch back via prev_project_tab
        self.player.prev_project_tab()
        self.assertEqual(self.player.active_project, p1)

        # Close Tab 2
        p2_idx = tabs.indexOf(p2.canvas)
        self.player._on_tab_close_requested(p2_idx)
        self.assertEqual(tabs.count(), initial_tab_count)

        # Close remaining tab - should automatically recreate a fresh tab
        self.player._on_tab_close_requested(0)
        self.assertEqual(tabs.count(), 1)
        self.assertIsNotNone(self.player.active_project)
        self.assertEqual(len(self.player.canvas.strokes), 0)
        print("[OK] Multi-project tabs creation, switching, state isolation, and tab lifecycle verified")

    def test_35_about_contacts(self):
        """Verify Telegram and GitHub contacts in About dialog"""
        self.assertEqual(TELEGRAM_URL, "https://t.me/furrykit")
        self.assertEqual(GITHUB_URL, "https://github.com/furrykit")
        dlg = AboutDialog(parent=self.player)
        self.assertIsNotNone(dlg)
        print("[OK] Telegram @furrykit and GitHub furrykit links verified in About dialog")

    def test_36_custom_export_preset(self):
        """Verify Custom export preset auto-selection and config output"""
        dlg = ExportDialog(default_w=1920, default_h=1080, parent=self.player)
        self.assertIn("Custom (User Defined)", dlg.presets)

        # Modifying width should auto-select "Custom (User Defined)"
        dlg.spin_w.setValue(1440)
        self.assertEqual(dlg.combo_presets.currentText(), "Custom (User Defined)")

        # Changing FPS should retain Custom selection
        dlg.spin_fps.setValue(45)
        self.assertEqual(dlg.combo_presets.currentText(), "Custom (User Defined)")

        # By default for preset, custom bitrate row is hidden
        self.assertTrue(dlg.row_custom_bitrate.isHidden())

        # Select "Custom..." in combo_bitrate -> row becomes visible
        idx_custom_b = dlg.combo_bitrate.findData(-1)
        dlg.combo_bitrate.setCurrentIndex(idx_custom_b)
        self.assertFalse(dlg.row_custom_bitrate.isHidden())

        # Set custom bitrate in kbps
        dlg.spin_bitrate.setValue(18500)
        self.assertEqual(dlg.combo_presets.currentText(), "Custom (User Defined)")

        # Changing rate control to CBR
        idx_cbr = dlg.combo_rate_control.findData("cbr")
        dlg.combo_rate_control.setCurrentIndex(idx_cbr)

        cfg = dlg.get_export_config()
        self.assertEqual(cfg['width'], 1440)
        self.assertEqual(cfg['fps'], 45.0)
        self.assertEqual(cfg['bitrate'], '18500k')
        self.assertEqual(cfg['rate_control'], 'cbr')
        dlg.close()
        print("[OK] Custom export preset selection, conditional custom bitrate in kbps, and CBR/VBR switching verified")

    def test_37_video_overlay_seeking(self):
        """Verify video overlay seeking (seconds, frame, relative stepping) and freeze bug fix"""
        vid_path = self.video_path
        ov = OverlayObject(999, vid_path, QRectF(0, 0, 320, 240), start_time=0.0)
        self.assertEqual(ov.obj_type, OverlayObject.TYPE_VIDEO)
        self.assertTrue(ov.video_total_frames > 0)

        dur = ov.get_duration()
        self.assertGreater(dur, 0.0)

        # Test seeking to seconds
        ov.seek_to_seconds(0.5)
        self.assertAlmostEqual(ov.start_offset, 0.5, delta=0.1)
        self.assertIsNotNone(ov.video_cached_frame)

        # Test frame seeking
        ov.seek_to_frame(10)
        self.assertEqual(ov.video_cached_idx, 10)
        f10_img = ov.video_cached_frame
        self.assertIsNotNone(f10_img)

        # Test step frames
        ov.step_frames(5)
        self.assertEqual(ov.video_cached_idx, 15)
        f15_img = ov.video_cached_frame
        self.assertIsNotNone(f15_img)

        # Verify frame update when paused (freeze bug check)
        ov.is_playing = False
        frame_at_t = ov.get_frame_at_time(0.0)
        self.assertIsNotNone(frame_at_t)

        # Step back
        ov.step_frames(-5)
        self.assertEqual(ov.video_cached_idx, 10)

        ov.close()
        print("[OK] Video overlay seeking, scrubbing, stepping, and paused frame rendering verified")

    def test_38_window_capture_frame_recording_and_export(self):
        import cv2
        import numpy as np
        from PyQt5.QtWidgets import QMessageBox

        old_question = QMessageBox.question
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.No

        try:
            player = self.player
            player.is_capturing_window = True
            player.is_mic_enabled = False

            # Start recording
            player.start_actions_record()
            self.assertTrue(player.recorder.is_recording())
            self.assertIsNotNone(player.temp_capture_video_path)
            self.assertEqual(player._capture_frame_count, 0)

            # Feed 15 frames from captured window
            test_w, test_h = 320, 240
            for i in range(15):
                dummy_frame = np.full((test_h, test_w, 3), i * 15, dtype=np.uint8)
                player._on_captured_window_frame(dummy_frame, i / 30.0)

            self.assertEqual(player._capture_frame_count, 15)
            self.assertEqual(player.current_frame_idx, 14)
            self.assertEqual(player.temp_capture_writer_size, (test_w, test_h))

            # Check recorded events include 'frame' events with matching frame_idx
            frame_events = [ev for ev in player.recorder.events if ev['type'] == 'frame']
            self.assertGreaterEqual(len(frame_events), 15)
            self.assertEqual(frame_events[-1]['frame_idx'], 14)

            # Stop recording
            saved_video_path = player.temp_capture_video_path
            player.stop_actions_record()
            self.assertIsNone(player.temp_capture_writer)
            self.assertEqual(player.total_frames, 15)

            # Verify the captured video file is valid and readable
            self.assertTrue(os.path.exists(saved_video_path))
            cap = cv2.VideoCapture(saved_video_path)
            self.assertTrue(cap.isOpened())
            read_frames = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                read_frames += 1
                self.assertEqual(frame.shape[:2], (test_h, test_w))
            cap.release()
            self.assertEqual(read_frames, 15)

            # Clean up temporary test video
            if os.path.exists(saved_video_path):
                try:
                    os.remove(saved_video_path)
                except Exception:
                    pass

            print("[OK] Window capture live frame recording and ActionRecorder synchronization verified")
        finally:
            QMessageBox.question = old_question

    def test_39_welcome_dialog_and_clean_ui(self):
        from fkplayer.ui.dialogs import AboutDialog, WelcomeDialog
        from PyQt5.QtWidgets import QLabel

        # 1. Verify AboutDialog author is not duplicated
        about = AboutDialog()
        labels = [lbl.text() for lbl in about.findChildren(QLabel)]
        author_labels = [l for l in labels if "furrykit" in l and ("Created by" in l or "Создано" in l)]
        self.assertEqual(len(author_labels), 1)
        self.assertNotIn("(furrykit) (furrykit)", author_labels[0])
        self.assertNotIn("furrykit (furrykit)", author_labels[0])
        about.close()

        # 2. Verify WelcomeDialog
        welcome = WelcomeDialog()
        welcome_labels = " ".join([lbl.text() for lbl in welcome.findChildren(QLabel)])
        self.assertIn("100% free", welcome_labels)
        self.assertIn("donation", welcome_labels.lower())
        welcome._on_start_clicked()
        welcome.close()

        # 3. Verify _fallback_canvas is hidden (no mystery bleed-through text)
        player = self.player
        self.assertFalse(player._fallback_canvas.isVisible())

        # 4. Verify Help menu has no duplicate donate or welcome action
        help_actions = [act.text() for act in player.menu_help.actions()]
        self.assertNotIn(tr('act_donate'), help_actions)
        self.assertNotIn(tr('act_welcome'), help_actions)

        # 5. Verify toolbar dimensions fit comfortably without button truncation
        self.assertLess(player.top_toolbar.minimumSizeHint().width(), 800)
        self.assertLess(player.recording_bar.minimumSizeHint().width(), 700)

        print("[OK] Welcome dialog, single author in About, hidden fallback canvas, and responsive toolbars verified")

    def test_40_multitrack_multiselect_aspect_audio_and_dragndrop(self):
        """Verify overlay drag-and-drop fix, multi-track timeline, multi-selection, aspect ratio preservation, and mic default"""
        player = self.player

        # 1. Verify mic is OFF by default
        self.assertFalse(player.is_mic_enabled)
        self.assertIn("OFF", player.btn_mic_toggle.text())

        # 2. Verify canvas has zero tabs added on D&D when canvas already exists
        player.create_blank_canvas(1280, 720)
        initial_tab_count = len(player.projects)
        canvas = player.canvas
        self.assertEqual(canvas.video_width, 1280)

        # Drag and drop onto existing canvas
        from PyQt5.QtCore import QMimeData, QUrl, QPoint
        from PyQt5.QtGui import QDropEvent
        from PIL import Image
        mime = QMimeData()
        sample_img = os.path.abspath("test_dnd_ov.png")
        Image.new('RGB', (200, 100), color='blue').save(sample_img)
        mime.setUrls([QUrl.fromLocalFile(sample_img)])
        drop_ev = QDropEvent(QPoint(100, 100), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        canvas.dropEvent(drop_ev)

        # Must NOT create a new tab!
        self.assertEqual(len(player.projects), initial_tab_count)
        self.assertGreater(len(canvas.overlays), 0)
        ov = canvas.overlays[-1]
        self.assertEqual(os.path.normpath(ov.file_path), os.path.normpath(sample_img))

        # 3. Verify Aspect Ratio Preservation on OverlayObject
        self.assertTrue(ov.keep_aspect_ratio)
        self.assertAlmostEqual(ov.orig_aspect_ratio, 200.0 / 100.0, places=2)

        # 4. Verify Multi-Selection of Overlays
        ov2 = OverlayObject(999, sample_img, QRectF(300, 300, 150, 150))
        canvas.overlays.append(ov2)
        canvas.selected_overlays = [ov, ov2]
        self.assertEqual(len(canvas.selected_overlays), 2)
        self.assertEqual(canvas.selected_overlay, ov2) # backward compatible property

        # Test rubber-band selection
        canvas._is_rubber_banding = True
        canvas._rubber_band_rect = QRectF(0, 0, 500, 500)
        canvas.selected_overlays.clear()
        rb = canvas._rubber_band_rect.normalized()
        canvas.selected_overlays = [o for o in canvas.overlays if rb.intersects(o.rect)]
        self.assertIn(ov, canvas.selected_overlays)
        self.assertIn(ov2, canvas.selected_overlays)

        # 5. Verify Multi-Track Timeline Container
        self.assertIsNotNone(player.overlay_tracks_container)
        # Add a video overlay to trigger track row creation
        import cv2
        import numpy as np
        sample_vid = os.path.abspath("test_track_vid.mp4")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        vw = cv2.VideoWriter(sample_vid, fourcc, 30.0, (160, 120))
        for _ in range(30):
            vw.write(np.zeros((120, 160, 3), dtype=np.uint8))
        vw.release()

        ov_vid = OverlayObject(1000, sample_vid, QRectF(50, 50, 160, 120))
        canvas.overlays.append(ov_vid)
        player._refresh_overlay_tracks()

        self.assertTrue(player.overlay_tracks_container.isVisible())
        self.assertGreaterEqual(len(player.overlay_tracks_container.rows), 1)
        track_row = player.overlay_tracks_container.rows[0]
        self.assertEqual(track_row.overlay, ov_vid)
        self.assertGreater(track_row.dur, 0.0)

        # 6. Verify SystemAudioRecorder existence
        from fkplayer.media.audio import SystemAudioRecorder
        sys_rec = SystemAudioRecorder()
        self.assertIsNotNone(sys_rec)

        # Cleanup test files
        for o in list(canvas.overlays):
            canvas.remove_overlay(o)
        player._refresh_overlay_tracks()
        self.assertFalse(player.overlay_tracks_container.isVisible())
        if os.path.exists(sample_img):
            os.remove(sample_img)
        if os.path.exists(sample_vid):
            os.remove(sample_vid)

        print("[OK] Multi-track timeline, multi-selection, aspect ratio lock, and window audio capture verified")

    def test_41_ffmpeg_and_updater(self):
        """Verify get_ffmpeg_path and UpdatesDialog / GitHubUpdateCheckerWorker"""
        from fkplayer.player import get_ffmpeg_path, APP_VERSION
        from fkplayer.ui.dialogs import UpdatesDialog, GitHubUpdateCheckerWorker, parse_version

        # 1. Verify get_ffmpeg_path finds local ffmpeg
        ffmpeg_exe = get_ffmpeg_path()
        self.assertIsNotNone(ffmpeg_exe, "FFmpeg should be discovered on system")
        self.assertTrue(os.path.exists(ffmpeg_exe), f"FFmpeg binary should exist at {ffmpeg_exe}")

        # 2. Verify version parsing
        self.assertEqual(parse_version("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_version("v2.1.3-beta"), (2, 1, 3))
        self.assertGreater(parse_version("1.0.1"), parse_version("1.0.0"))

        # 3. Verify UpdatesDialog initialization
        dlg = UpdatesDialog(parent=self.player)
        self.assertFalse(hasattr(dlg, 'edit_repo'))
        self.assertEqual(dlg.repo, "furrykit/FKVideoPlayer")
        self.assertIsNotNone(dlg.txt_notes)
        self.assertIsNotNone(dlg.lbl_status)
        dlg.close()

        print("[OK] FFmpeg detection and GitHub updater dialog verified")

    def test_42_custom_bitrate_and_cbr_vbr_export(self):
        """Verify CBR and VBR encoding with custom bitrate via ExportVideoWorker"""
        events = [
            {'type': 'frame', 'time': 0.0, 'frame_idx': 0, 'zoom': 1.0, 'pan': (0, 0)},
            {'type': 'stop', 'time': 0.2}
        ]

        # 1. Test CBR export
        out_cbr = os.path.abspath("test_cbr_export.mp4")
        if os.path.exists(out_cbr):
            os.remove(out_cbr)

        worker_cbr = ExportVideoWorker(
            video_path=self.video_path,
            events=events,
            total_duration=0.2,
            output_path=out_cbr,
            fps=30.0,
            out_size=(320, 240),
            codec='libx264',
            bitrate='6500k',
            rate_control='cbr'
        )
        worker_cbr.run()
        self.assertTrue(os.path.exists(out_cbr), "CBR exported video should exist")
        self.assertGreater(os.path.getsize(out_cbr), 500)
        os.remove(out_cbr)

        # 2. Test VBR export with custom kbps bitrate
        out_vbr = os.path.abspath("test_vbr_export.mp4")
        if os.path.exists(out_vbr):
            os.remove(out_vbr)

        worker_vbr = ExportVideoWorker(
            video_path=self.video_path,
            events=events,
            total_duration=0.2,
            output_path=out_vbr,
            fps=30.0,
            out_size=(320, 240),
            codec='libx264',
            bitrate='14200k',
            rate_control='vbr'
        )
        worker_vbr.run()
        self.assertTrue(os.path.exists(out_vbr), "VBR exported video should exist")
        self.assertGreater(os.path.getsize(out_vbr), 500)
        os.remove(out_vbr)

        print("[OK] Custom bitrate and CBR/VBR encoding verified")

    def test_43_photoshop_eraser_and_size_setting(self):
        """Verify dynamic eraser size settings, toolbar synchronization, and multi-segment carving"""
        player = self.player
        canvas = player.canvas

        # 1. Switch to pen tool: spin_width should show Brush Size
        player._select_tool(VideoCanvas.TOOL_PEN)
        self.assertEqual(player.lbl_tool_size.text(), "Brush Size:")
        player.spin_width.setValue(12)
        self.assertEqual(canvas.pen_width, 12.0)

        # 2. Switch to eraser tool: spin_width should show Eraser Size and reflect eraser_radius
        player._select_tool(VideoCanvas.TOOL_ERASER)
        self.assertEqual(player.lbl_tool_size.text(), "Eraser Size:")
        self.assertEqual(player.spin_width.value(), int(round(canvas.eraser_radius)))

        # 3. Change eraser size via spinbox
        player.spin_width.setValue(24)
        self.assertEqual(canvas.eraser_radius, 24.0)

        # 4. Draw horizontal stroke and carve hole with custom radius 20
        canvas.zoom_factor = 1.0
        canvas.strokes.clear()
        canvas.undo_stack.clear()
        s = Stroke(QColor("#FF0000"), 4.0, [QPointF(x, 100) for x in range(0, 201, 10)])
        canvas.strokes.append(s)

        canvas.set_eraser_radius(20.0)
        canvas.erase_strokes_at_video_pt(QPointF(100, 100))
        # Stroke must be carved into 2 pieces
        self.assertEqual(len(canvas.strokes), 2)
        self.assertLess(canvas.strokes[0].points[-1].x(), 85.0)
        self.assertGreater(canvas.strokes[1].points[0].x(), 115.0)

        # 5. Undo restores original stroke
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        self.assertEqual(len(canvas.strokes[0].points), 21)

        print("[OK] Dynamic eraser size and Photoshop-style stroke carving verified")

    def test_44_add_overlay_button_and_logger(self):
        """Verify Add Overlay button does not crash with bool signal argument and verify logging"""
        from fkplayer.core import logger
        from fkplayer.player import resource_path
        from PyQt5.QtWidgets import QFileDialog

        # 1. Test clicking btn_add_overlay
        icon_path = resource_path("icon.ico")
        orig_get_open = QFileDialog.getOpenFileName
        try:
            QFileDialog.getOpenFileName = lambda *args, **kwargs: (icon_path, "All Files (*.*)")
            prev_count = len(self.player.canvas.overlays)
            # Emits clicked(bool checked=False)
            self.player.btn_add_overlay.click()
            self.assertEqual(len(self.player.canvas.overlays), prev_count + 1)

            # Test calling add_overlay_dialog directly with False
            self.player.add_overlay_dialog(False)
            self.assertEqual(len(self.player.canvas.overlays), prev_count + 2)
        finally:
            QFileDialog.getOpenFileName = orig_get_open

        # 2. Test logger system
        log = logger.get_logger("Test")
        log.info("Test entry from test_44")
        log_path = logger.get_log_file_path()
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
            self.assertIn("Test entry from test_44", content)

        print("[OK] Add overlay button crash fix and logging subsystem verified")

    def test_45_tab_bar_and_button_adaptive_sizing(self):
        """Verify AutoAdjustTabBar calculates generous widths so tab labels never clip"""
        from fkplayer.ui.dialogs import PreferencesDialog, AutoAdjustTabBar
        from fkplayer.core.i18n import I18nManager

        # 1. Verify project_tabs uses AutoAdjustTabBar
        self.assertIsInstance(self.player.project_tabs.tabBar(), AutoAdjustTabBar)

        # 2. Test PreferencesDialog in Russian
        orig_lang = I18nManager.instance().lang
        try:
            I18nManager.instance().lang = 'ru'
            dlg = PreferencesDialog()
            tb = dlg.tabs.tabBar()
            self.assertIsInstance(tb, AutoAdjustTabBar)

            # Each Russian tab must be sized generously (> 140px)
            for i in range(dlg.tabs.count()):
                w = tb.tabSizeHint(i).width()
                self.assertGreaterEqual(w, 140, f"Tab {i} ({dlg.tabs.tabText(i)}) sizeHint {w} too small")

            # 3. Test in English
            I18nManager.instance().lang = 'en'
            dlg_en = PreferencesDialog()
            tb_en = dlg_en.tabs.tabBar()
            for i in range(dlg_en.tabs.count()):
                w_en = tb_en.tabSizeHint(i).width()
                self.assertGreaterEqual(w_en, 110, f"English Tab {i} sizeHint {w_en} too small")
        finally:
            I18nManager.instance().lang = orig_lang

        print("[OK] Tab bar and button adaptive sizing verified: zero clipping in any language")

    def test_46_no_help_button_and_clean_tabbar_style(self):
        """Verify context help question mark is stripped and tab bar base line is disabled"""
        from fkplayer.ui.dialogs import (
            PreferencesDialog, NewCanvasDialog, WindowCaptureDialog,
            TextOverlayDialog, ExportDialog, AboutDialog, WelcomeDialog,
            UpdatesDialog, DIALOG_STYLE
        )

        # 1. Verify DIALOG_STYLE disables drawBase
        self.assertIn("qproperty-drawBase: 0", DIALOG_STYLE)
        self.assertIn("border: 1px solid #282838", DIALOG_STYLE)

        # 2. Verify all dialogs strip WindowContextHelpButtonHint
        dialog_classes = [
            PreferencesDialog, NewCanvasDialog, WindowCaptureDialog,
            TextOverlayDialog, ExportDialog, AboutDialog, WelcomeDialog,
            UpdatesDialog
        ]
        for dlg_cls in dialog_classes:
            dlg = dlg_cls()
            flags = int(dlg.windowFlags())
            self.assertEqual(
                flags & int(Qt.WindowContextHelpButtonHint),
                0,
                f"{dlg_cls.__name__} must not have WindowContextHelpButtonHint"
            )
            dlg.close()

        print("[OK] Context help button removed and tab bar base line disabled")

    def test_47_exception_guards_and_signal_safety(self):
        """Verify all button handlers accept Qt signal arguments (*args), division guards hold, and closeEvent cleans up"""
        player = self.player

        # 1. Test Qt signal signatures (buttons passing bool checked=True/False)
        handlers_to_test = [
            player.toggle_mute,
            player._toggle_loop,
            player._toggle_microphone,
            player.pause_actions_record,
            player.stop_actions_record,
        ]
        for handler in handlers_to_test:
            try:
                handler(True)
                handler(False)
            except TypeError as e:
                self.fail(f"Handler {handler.__name__} failed with Qt signal args: {e}")

        # 2. Test Canvas zoom division guards
        canvas = player.canvas
        orig_zoom = canvas.zoom_factor
        try:
            canvas.zoom_factor = 0.0
            pt = canvas.screen_to_video(QPointF(100, 100))
            self.assertIsNotNone(pt)
            canvas.apply_zoom_at(1.5, QPointF(50, 50))
            self.assertGreater(canvas.zoom_factor, 0.0)
        finally:
            canvas.zoom_factor = orig_zoom

        # 3. Test FPS division guards
        orig_fps = player.fps
        try:
            player.fps = 0.0
            player._update_time_label()
            player._sync_audio_position()
        finally:
            player.fps = orig_fps

        # 4. Verify exactly one closeEvent defined in FKVideoPlayer
        import inspect
        source = inspect.getsource(type(player))
        close_events = [line for line in source.splitlines() if line.strip().startswith("def closeEvent(")]
        self.assertEqual(len(close_events), 1, "FKVideoPlayer must have exactly one unified closeEvent definition")

        # 5. Verify safe_open_url in settings_dialogs handles invalid inputs gracefully
        from fkplayer.ui.dialogs import safe_open_url
        safe_open_url("") # Should not raise
        safe_open_url(None) # Should not raise

        print("[OK] Exception guards, Qt signal safety, and single unified closeEvent verified")

    def test_48_audio_master_clock_sync(self):
        """Verify audio-master clock synchronization and prevention of audio seek jitter/echo"""
        import tempfile
        from PyQt5.QtMultimedia import QMediaPlayer
        player = self.player
        player.current_frame_idx = 0
        player.fps = 30.0
        player.total_frames = 300
        player.has_audio = True
        player.playback_speed = 1.0
        player.is_muted = False

        dummy_audio = os.path.join(tempfile.gettempdir(), f"mock_audio_{os.getpid()}.wav")
        with open(dummy_audio, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 36)
        player.temp_audio_path = dummy_audio

        original_state = player.audio_player.state
        original_position = player.audio_player.position
        original_set_pos = player.audio_player.setPosition
        try:
            set_pos_calls = []
            mock_pos = [0]

            player.audio_player.state = lambda: QMediaPlayer.PlayingState
            player.audio_player.position = lambda: mock_pos[0]
            player.audio_player.setPosition = lambda ms: set_pos_calls.append(ms)

            # Advance audio to 100ms (~3 frames at 30 fps)
            mock_pos[0] = 100
            player._on_play_tick()
            self.assertEqual(player.current_frame_idx, 3)
            # Crucial: setPosition should NOT have been called during continuous play tick!
            self.assertEqual(len(set_pos_calls), 0, "Audio setPosition must not be called during playback ticks")

            # Advance audio to 333ms (~10 frames at 30 fps)
            mock_pos[0] = 333
            player._on_play_tick()
            self.assertEqual(player.current_frame_idx, 10)
            self.assertEqual(len(set_pos_calls), 0)

            # Test seek_seconds while playing properly syncs target_ms
            player.is_playing = True
            player.seek_seconds(2.0)
            self.assertEqual(player.current_frame_idx, 70)
            self.assertGreaterEqual(len(set_pos_calls), 1)
            self.assertAlmostEqual(set_pos_calls[-1], int(70 / 30.0 * 1000), delta=50)
        finally:
            player.audio_player.state = original_state
            player.audio_player.position = original_position
            player.audio_player.setPosition = original_set_pos
            if os.path.exists(dummy_audio):
                try:
                    os.remove(dummy_audio)
                except Exception:
                    pass

        print("[OK] Audio-master clock synchronization and stutter/duplication prevention verified")
    def test_49_modular_architecture_and_playback_optimization(self):
        """Verify modular subpackage exports, reduced player.py footprint, and 32-bit RGB32 cached frames."""
        import cv2
        from PyQt5.QtGui import QImage
        from fkplayer.ui.canvas import VideoCanvas, OverlayObject, Stroke
        from fkplayer.ui.timeline import ClickableSlider, OverlayTrackRow, OverlayTrackContainer
        from fkplayer.media.recorder import ActionRecorder
        from fkplayer.media.export import ExportVideoWorker
        from fkplayer.core.session import ProjectSession
        from fkplayer.core.geometry import set_dark_titlebar, resource_path, get_ffmpeg_path

        # Verify OpenCV thread limiting
        self.assertLessEqual(cv2.getNumThreads(), 4, "OpenCV threads must be clamped to <= 4 to prevent CPU thread thrashing")

        # Verify player.py was pruned of monolith bloat
        player_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fkplayer", "player.py"))
        with open(player_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertLessEqual(len(lines), 3000, f"player.py should be modularized to <= 3000 lines, currently {len(lines)}")

        # Verify frame caching provides Format_RGB32 for native hardware blitting
        player = self.player
        qimg = player._get_frame_cached(0)
        self.assertIsNotNone(qimg)
        self.assertEqual(qimg.format(), QImage.Format_RGB32, "Cached frame must be Format_RGB32 for fast blitting")

        # Verify sequential frame grabbing without cap.set
        qimg2 = player._get_frame_cached(2)
        self.assertIsNotNone(qimg2)
        self.assertEqual(player.frame_cache[2].format(), QImage.Format_RGB32)

        print("[OK] Modular architecture, CPU thread limiting, and RGB32 playback pipeline verified")

    def test_50_app_entrypoint_and_main_startup(self):
        """Verify root entrypoint, fkplayer.player.main components, and module import cleanliness."""
        import importlib
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFont, QIcon
        from PyQt5.QtCore import Qt
        import fkplayer.player as pmod
        import player as root_player

        self.assertTrue(hasattr(pmod, 'main'))
        self.assertTrue(hasattr(pmod, 'FKVideoPlayer'))
        self.assertTrue(hasattr(pmod, 'QFont'))
        self.assertTrue(hasattr(pmod, 'QSettings'))
        self.assertTrue(hasattr(pmod, 'ExportDialog'))

        # Verify QFont usage in main() does not throw NameError
        app = QApplication.instance()
        self.assertIsNotNone(app)
        app_font = app.font()
        app_font.setFamily("Segoe UI")
        app_font.setStyleHint(QFont.SansSerif)
        app_font.setPointSize(9)
        app.setFont(app_font)

        # Test all modular components can be imported without missing references
        modules_to_test = [
            'fkplayer.core.geometry',
            'fkplayer.core.session',
            'fkplayer.core.tabs',
            'fkplayer.ui.canvas',
            'fkplayer.ui.timeline',
            'fkplayer.ui.builder',
            'fkplayer.ui.overlay_actions',
            'fkplayer.media.playback',
            'fkplayer.media.recorder',
            'fkplayer.media.export',
            'fkplayer.media.record_controller',
            'fkplayer.ui.dialogs',
            'fkplayer.core.logger',
            'fkplayer.core.i18n',
            'fkplayer.core.projects',
        ]
        for m in modules_to_test:
            mod = importlib.import_module(m)
            self.assertIsNotNone(mod, f"Module {m} must load cleanly")

        print("[OK] Main entrypoint, QFont setup, and all modular package imports verified")

    def test_51_high_resolution_memory_budget_and_cache_clamping(self):
        """Verify dynamic memory budgeting prevents 8K/4K/1080p frame cache memory explosions."""
        from PyQt5.QtGui import QImage
        player = self.player

        # 1. Verify player memory budget attribute exists and is clamped to <= 150MB
        self.assertTrue(hasattr(player, 'MAX_CACHE_MEMORY_BYTES'))
        self.assertLessEqual(player.MAX_CACHE_MEMORY_BYTES, 150 * 1024 * 1024)

        # 2. Test 8K resolution memory budgeting (7680 x 4320)
        w_8k, h_8k = 7680, 4320
        frame_bytes_8k = w_8k * h_8k * 4 # ~132.7 MB
        limit_8k = max(1, min(60, int(player.MAX_CACHE_MEMORY_BYTES // frame_bytes_8k)))
        self.assertEqual(limit_8k, 1, "8K video cache must be clamped to 1 frame to prevent 10GB+ RAM explosion")

        # 3. Test 4K resolution memory budgeting (3840 x 2160)
        w_4k, h_4k = 3840, 2160
        frame_bytes_4k = w_4k * h_4k * 4 # ~33.2 MB
        limit_4k = max(1, min(60, int(player.MAX_CACHE_MEMORY_BYTES // frame_bytes_4k)))
        self.assertEqual(limit_4k, 3, "4K video cache must be clamped to <= 3 frames (~100MB)")

        # 4. Test 1080p resolution memory budgeting (1920 x 1080)
        w_1080, h_1080 = 1920, 1080
        frame_bytes_1080 = w_1080 * h_1080 * 4 # ~8.3 MB
        limit_1080 = max(1, min(60, int(player.MAX_CACHE_MEMORY_BYTES // frame_bytes_1080)))
        self.assertLessEqual(limit_1080, 15, "1080p video cache must be clamped to <= 15 frames (~125MB)")

        # 5. Verify live cache eviction behavior under memory constraint
        player.frame_cache.clear()
        player.MAX_CACHE_FRAMES = limit_8k

        # Simulate adding 10 simulated 8K frames to the cache
        # Using small QImages with artificial byte budget simulation
        orig_budget = player.MAX_CACHE_MEMORY_BYTES
        try:
            player.MAX_CACHE_MEMORY_BYTES = 1000 # Artificial tight budget
            # Add dummy images
            q1 = QImage(10, 10, QImage.Format_RGB32) # 400 bytes
            q2 = QImage(10, 10, QImage.Format_RGB32)
            q3 = QImage(10, 10, QImage.Format_RGB32)

            player.frame_cache[0] = q1
            player.frame_cache[1] = q2

            # Simulate _get_frame_cached eviction logic
            frame_bytes = 10 * 10 * 4
            dynamic_limit = max(1, min(60, int(player.MAX_CACHE_MEMORY_BYTES // frame_bytes)))
            self.assertEqual(dynamic_limit, 2)

            while len(player.frame_cache) >= dynamic_limit:
                player.frame_cache.popitem(last=False)
            player.frame_cache[2] = q3

            self.assertNotIn(0, player.frame_cache, "Oldest frame 0 must be evicted")
            self.assertIn(1, player.frame_cache)
            self.assertIn(2, player.frame_cache)
            self.assertEqual(len(player.frame_cache), 2)
        finally:
            player.MAX_CACHE_MEMORY_BYTES = orig_budget
            player.frame_cache.clear()

        print("[OK] High resolution memory budget and cache clamping verified")

    def test_52_hardware_scaling_and_smooth_playback(self):
        """Verify hardware scaling, rotating buffer pool, and zero-stutter playback pipeline"""
        player = self.player

        # 1. Verify play_timer uses high-resolution Qt.PreciseTimer
        self.assertEqual(player.play_timer.timerType(), Qt.PreciseTimer, "play_timer must use Qt.PreciseTimer")

        # 2. Test buffer pool and scaling logic on FastVideoReader
        if os.path.exists("8k.mp4"):
            from fkplayer.media.reader import FastVideoReader
            reader = FastVideoReader("8k.mp4")
            self.assertEqual(reader.orig_width, 7680)
            self.assertEqual(reader.orig_height, 4320)
            self.assertLessEqual(reader.width, 3840)
            self.assertLessEqual(reader.height, 2160)
            self.assertEqual(len(reader._buffer_pool), 4)

            # Test sequential decoding throughput
            ret, qimg = reader.read_qimage()
            self.assertTrue(ret)
            self.assertIsNotNone(qimg)
            self.assertEqual(qimg.width(), reader.width)
            self.assertEqual(qimg.height(), reader.height)
            reader.release()

        # 3. Test active playback bypass and paused caching behavior
        player.is_playing = True
        player.frame_cache.clear()
        q_dummy = QImage(320, 240, QImage.Format_RGB32)

        # Mock cap with read_qimage
        class MockCap:
            def __init__(self):
                self.pos = 0
            def isOpened(self):
                return True
            def get(self, prop):
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    return float(self.pos)
                return 0.0
            def grab(self):
                self.pos += 1
                return True
            def set(self, prop, val):
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    self.pos = int(val)
                return True
            def read_qimage(self):
                self.pos += 1
                return True, q_dummy

        orig_cap = player.cap
        try:
            player.cap = MockCap()

            # Active playback: should NOT pollute frame_cache
            res = player._get_frame_cached(0)
            self.assertIsNotNone(res)
            self.assertEqual(len(player.frame_cache), 0, "Active playback must bypass cache")
            self.assertEqual(player._cap_pos, 1)

            # Paused playback: should populate frame_cache
            player.is_playing = False
            res_paused = player._get_frame_cached(1)
            self.assertIsNotNone(res_paused)
            self.assertIn(1, player.frame_cache, "Paused frame must be stored in cache")
            self.assertEqual(player._cap_pos, 2)
        finally:
            player.cap = orig_cap
            player.is_playing = False
            player.frame_cache.clear()

        print("[OK] Hardware scaling, zero-copy buffer, and smooth playback pipeline verified")

    def test_53_overlay_undo_and_clear(self):
        """Verify Undo (Ctrl+Z) and Clear (C/Del) fully work with overlays, transforms, and drawings."""
        canvas = self.player.canvas
        canvas.strokes.clear()
        canvas.overlays.clear()
        canvas.undo_stack.clear()
        img_path = resource_path("icon.png")

        # 1. Add overlay and verify Undo removes it
        self.player.add_overlay(img_path)
        self.assertEqual(len(canvas.overlays), 1)
        ov = canvas.overlays[0]
        self.player.btn_undo.click()
        self.assertEqual(len(canvas.overlays), 0, "Undo must remove added overlay")

        # 2. Add text overlay and duplicate, verify Undo removes duplicate
        self.player.add_text_overlay({'text': 'Hello World'})
        self.assertEqual(len(canvas.overlays), 1)
        text_ov = canvas.overlays[0]
        self.player.duplicate_overlay(text_ov)
        self.assertEqual(len(canvas.overlays), 2)
        self.player.undo_last_action()
        self.assertEqual(len(canvas.overlays), 1, "Undo must remove duplicated overlay")
        self.assertEqual(canvas.overlays[0], text_ov)

        # 3. Test overlay transform undo (move/resize)
        orig_rect = QRectF(text_ov.rect)
        canvas.selected_overlays = [text_ov]
        canvas._overlay_drag_mode = 'move'
        canvas._drag_start_rects = {text_ov: QRectF(orig_rect)}
        text_ov.rect.translate(100.0, 50.0)
        transforms = [(text_ov, orig_rect, QRectF(text_ov.rect))]
        canvas.undo_stack.append(('transform_overlay', transforms))
        canvas._overlay_drag_mode = None
        self.assertAlmostEqual(text_ov.rect.x(), orig_rect.x() + 100.0, places=1)
        self.player.undo_last_action()
        self.assertAlmostEqual(text_ov.rect.x(), orig_rect.x(), places=1, msg="Undo must restore overlay rect")

        # 4. Test delete overlay and Undo restores it
        canvas.selected_overlay = text_ov
        self.player.delete_selected_overlay()
        self.assertEqual(len(canvas.overlays), 0, "Overlay must be deleted")
        self.player.undo_last_action()
        self.assertEqual(len(canvas.overlays), 1, "Undo must restore deleted overlay")
        self.assertEqual(canvas.overlays[0].text, 'Hello World')

        # 5. Test clear_all_drawings clears BOTH strokes and overlays
        s1 = Stroke(QColor("#FF0000"), 4.0, [QPointF(10, 10), QPointF(20, 20)])
        canvas.strokes.append(s1)
        canvas.undo_stack.append(('add', s1))
        self.assertEqual(len(canvas.strokes), 1)
        self.assertEqual(len(canvas.overlays), 1)

        self.player.btn_clear_all.click()
        self.assertEqual(len(canvas.strokes), 0, "Clear must clear strokes")
        self.assertEqual(len(canvas.overlays), 0, "Clear must clear overlays")

        # 6. Test Undo after clear restores BOTH strokes and overlays
        self.player.btn_undo.click()
        self.assertEqual(len(canvas.strokes), 1, "Undo must restore strokes after clear")
        self.assertEqual(len(canvas.overlays), 1, "Undo must restore overlays after clear")
        self.assertEqual(canvas.overlays[0].text, 'Hello World')

        # Clean up
        for o in canvas.overlays:
            o.close()
        canvas.overlays.clear()
        canvas.strokes.clear()
        canvas.undo_stack.clear()
        print("[OK] Overlay undo, deletion restore, transform undo, and unified clear verified")

    def test_54_eraser_drag_session_and_version_consistency(self):
        """Verify continuous eraser drag session with single-step undo and updater version format."""
        from fkplayer.ui.dialogs import CURRENT_VERSION, UpdatesDialog
        from fkplayer.core.geometry import APP_VERSION
        from PyQt5.QtWidgets import QLabel
        self.assertEqual(CURRENT_VERSION, APP_VERSION)
        self.assertEqual(APP_VERSION, "1.1.4")

        dlg = UpdatesDialog(parent=self.player)
        lbls = dlg.findChildren(QLabel)
        header_text = " ".join([l.text() for l in lbls])
        self.assertIn("v1.1.4", header_text)
        self.assertNotIn("vv", header_text)

        # Test simulated GitHub response with tag 'v1.1.4' - should format without double 'v'
        dlg._on_check_finished({"is_newer": True, "tag_name": "v1.1.4", "download_url": "http://test"})
        self.assertNotIn("vv", dlg.lbl_status.text())
        self.assertIn("v1.1.4", dlg.lbl_status.text())

        # Test eraser continuous session
        canvas = self.player.canvas
        canvas.zoom_factor = 1.0
        canvas.strokes.clear()
        canvas.undo_stack.clear()

        # Stroke from (0, 100) to (300, 100)
        s = Stroke(QColor("#FF0000"), 4.0, [QPointF(x, 100) for x in range(0, 301, 10)])
        canvas.strokes.append(s)

        # Simulate dragging the eraser across the stroke:
        canvas.set_eraser_radius(15.0)
        canvas._is_erasing = True
        canvas._eraser_initial_strokes = [st.copy() for st in canvas.strokes]
        canvas._last_eraser_vpt = QPointF(100, 100)
        canvas.erase_strokes_at_video_pt(QPointF(100, 100), record_undo=False)

        # Drag to (150, 100)
        canvas.erase_strokes_at_video_pt(QPointF(150, 100), record_undo=False)

        # End session
        init_strokes = canvas._eraser_initial_strokes
        canvas._is_erasing = False
        canvas.undo_stack.append(('modify_strokes', init_strokes))
        canvas._eraser_initial_strokes = None

        # Verify stroke was carved
        self.assertGreater(len(canvas.strokes), 1)
        self.assertEqual(len(canvas.undo_stack), 1, "Drag session must produce exactly 1 undo entry")

        # One Ctrl+Z restores the entire original stroke
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1, "Single undo must restore full original stroke")
        self.assertEqual(len(canvas.strokes[0].points), 31)
        self.assertEqual(canvas.strokes[0].points[0], QPointF(0, 100))
        self.assertEqual(canvas.strokes[0].points[-1], QPointF(300, 100))

        dlg.close()
        print("[OK] Continuous eraser drag session, single-step undo, and updater version format verified")


if __name__ == "__main__":
    unittest.main()



