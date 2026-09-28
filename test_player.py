#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматический тест функционала плеера:
- Загрузка видео
- Пауза / Воспроизведение
- Покадровая навигация (+1, -1)
- Перемотка времени (+5с, -5с, слайдер)
- Зум (Zoom In, Zoom Out, Reset 1:1, Fit to view)
- Рисование поверх видео
- Смена цвета и толщины кисти
- Стирание ластиком
- Кнопка 'Стереть на шаг назад' (Undo)
- Кнопка 'Стереть всё' (Clear all)
"""

import os
import sys
import unittest
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

# Установка offscreen для запуска без физического монитора при тестировании
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from player import VideoPlayerWindow, VideoCanvas, Stroke


class TestVideoPlayer(unittest.TestCase):
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
        """Проверка корректной загрузки видео"""
        self.assertIsNotNone(self.player.cap)
        self.assertTrue(self.player.cap.isOpened())
        self.assertGreater(self.player.total_frames, 0)
        self.assertGreater(self.player.fps, 0)
        self.assertEqual(self.player.current_frame_idx, 0)
        self.assertIsNotNone(self.player.canvas.current_qimage)
        print(f"[OK] Видео успешно загружено: {self.player.total_frames} кадров, {self.player.fps} FPS")

    def test_02_play_and_pause(self):
        """Проверка паузы и воспроизведения"""
        self.assertFalse(self.player.is_playing)
        self.player.play()
        self.assertTrue(self.player.is_playing)
        self.assertTrue(self.player.play_timer.isActive())

        # Симуляция тика воспроизведения
        self.player._on_play_tick()
        self.assertEqual(self.player.current_frame_idx, 1)

        self.player.pause()
        self.assertFalse(self.player.is_playing)
        self.assertFalse(self.player.play_timer.isActive())
        print("[OK] Воспроизведение и пауза работают штатно")

    def test_03_frame_stepping(self):
        """Проверка покадровой навигации вперед и назад"""
        self.player._seek_to_frame(10)
        self.assertEqual(self.player.current_frame_idx, 10)

        # Шаг вперед на 1 кадр
        self.player.step_frame(1)
        self.assertEqual(self.player.current_frame_idx, 11)

        # Шаг назад на 1 кадр
        self.player.step_frame(-1)
        self.assertEqual(self.player.current_frame_idx, 10)

        # Шаг назад еще раз
        self.player.step_frame(-1)
        self.assertEqual(self.player.current_frame_idx, 9)
        print("[OK] Покадровое переключение вперед (+1) и назад (-1) работает точно")

    def test_04_seeking_and_slider(self):
        """Проверка перемотки и слайдера"""
        # Перемотка на +1 секунду
        self.player._seek_to_frame(0)
        fps = int(self.player.fps)
        self.player.seek_seconds(1.0)
        self.assertEqual(self.player.current_frame_idx, fps)

        # Перемотка через слайдер
        self.player.timeline_slider.setValue(30)
        self.player._on_slider_moved(30)
        self.assertEqual(self.player.current_frame_idx, 30)

        # Перемотка на -5 секунд с ограничением на нулевой кадр
        self.player.seek_seconds(-5.0)
        self.assertEqual(self.player.current_frame_idx, 0)
        print("[OK] Перемотка по времени и перемещение по шкале работают корректно")

    def test_05_zoom_and_pan(self):
        """Проверка масштабирования (Zoom In, Zoom Out, Reset)"""
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

        # Проверка прямого и обратного преобразования координат
        test_pt = QPointF(100.0, 150.0)
        screen_pt = canvas.video_to_screen(test_pt)
        restored_pt = canvas.screen_to_video(screen_pt)
        self.assertAlmostEqual(test_pt.x(), restored_pt.x(), places=3)
        self.assertAlmostEqual(test_pt.y(), restored_pt.y(), places=3)
        print("[OK] Зум (приближение, отдаление, сброс) и проекция координат работают идеально")

    def test_06_drawing_and_undo_and_clear_all(self):
        """Проверка рисования, отмены шага (Undo) и полной очистки"""
        canvas = self.player.canvas
        self.assertEqual(len(canvas.strokes), 0)

        # 1. Рисуем штрих 1
        s1 = Stroke(QColor("#FF0000"), 4.0, [QPointF(50, 50), QPointF(60, 60), QPointF(70, 70)])
        canvas.strokes.append(s1)
        canvas.undo_stack.append(('add', s1))
        self.assertEqual(len(canvas.strokes), 1)

        # 2. Рисуем штрих 2
        s2 = Stroke(QColor("#00FF00"), 6.0, [QPointF(100, 100), QPointF(120, 120)])
        canvas.strokes.append(s2)
        canvas.undo_stack.append(('add', s2))
        self.assertEqual(len(canvas.strokes), 2)

        # 3. Кнопка 'Стереть на шаг назад' (Undo)
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        self.assertEqual(canvas.strokes[0], s1)

        # 4. Добавляем снова штрих
        canvas.strokes.append(s2)
        canvas.undo_stack.append(('add', s2))
        self.assertEqual(len(canvas.strokes), 2)

        # 5. Кнопка 'Стереть всё'
        canvas.clear_all_drawings()
        self.assertEqual(len(canvas.strokes), 0)

        # 6. 'Стереть на шаг назад' после 'Стереть всё' восстанавливает всё
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 2)
        print("[OK] Рисование, 'Стереть на шаг назад' и 'Стереть всё' работают безупречно")

    def test_07_eraser_tool(self):
        """Проверка работы инструмента 'Ластик'"""
        canvas = self.player.canvas
        canvas.strokes.clear()
        canvas.undo_stack.clear()

        # Создаем штрих в районе (200, 200)
        s = Stroke(QColor("#0000FF"), 4.0, [QPointF(200, 200), QPointF(205, 205)])
        canvas.strokes.append(s)
        canvas.undo_stack.append(('add', s))
        self.assertEqual(len(canvas.strokes), 1)

        # Применяем ластик рядом с точкой (202, 202)
        canvas.erase_strokes_at_video_pt(QPointF(202, 202))
        self.assertEqual(len(canvas.strokes), 0)

        # Проверяем отмену стирания ластиком
        canvas.undo_last_action()
        self.assertEqual(len(canvas.strokes), 1)
        print("[OK] Инструмент ластик стирает выбранный штрих и поддерживает шаг назад")

    def test_08_colors_and_thickness(self):
        """Проверка смены цвета и толщины"""
        self.player._set_brush_color("#34C759")
        self.assertEqual(self.player.canvas.pen_color.name().upper(), "#34C759")

        self.player.spin_width.setValue(12)
        self.assertEqual(self.player.canvas.pen_width, 12.0)
        print("[OK] Смена цвета и толщины кисти работает корректно")


if __name__ == "__main__":
    unittest.main()
