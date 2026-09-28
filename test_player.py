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
from PyQt5.QtCore import Qt, QPointF, QPoint
from PyQt5.QtGui import QColor, QMouseEvent
from PyQt5.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from player import (
    VideoPlayerWindow, VideoCanvas, Stroke, dist_to_segment_sq,
    ActionRecorder, ExportVideoWorker
)


class TestEnhancedVideoPlayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self):
        self.video_path = os.path.abspath("swapped_preview_test.mp4")
        self.assertTrue(os.path.exists(self.video_path), "Тестовое видео должно существовать")
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

        # Отмена шага
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)

        # Стереть всё
        canvas.clear_all_drawings()
        self.assertEqual(len(canvas.strokes), 0)

        # Отмена очистки возвращает штрихи
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        print("[OK] Рисование, 'Стереть на шаг назад' и 'Стереть всё' работают безупречно")

    def test_08_eraser_segment_intersection(self):
        """Проверка ластика на пересечение отрезков линий"""
        canvas = self.player.canvas
        canvas.strokes.clear()
        canvas.undo_stack.clear()

        # Длинная линия от (0, 0) до (200, 200) всего из 2 точек
        s = Stroke(QColor("#0000FF"), 4.0, [QPointF(0, 0), QPointF(200, 200)])
        canvas.strokes.append(s)
        canvas.undo_stack.append(('add', s))
        self.assertEqual(len(canvas.strokes), 1)

        # Ластик проходит через середину линии (100, 100), где нет явной вершины
        canvas.erase_strokes_at_video_pt(QPointF(100, 100))
        self.assertEqual(len(canvas.strokes), 0)

        # Отмена стирания восстанавливает линию
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        print("[OK] Ластик безошибочно стирает штрихи при пересечении отрезков")

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


if __name__ == "__main__":
    unittest.main()
