#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timeline slider, overlay track rows, and multitrack timeline widgets.
"""

import os
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QSlider, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QToolTip

try:
    from fkplayer.ui.dialogs import VideoOverlaySettingsDialog
    from fkplayer.ui.canvas import OverlayObject
except (ImportError, ValueError):
    from .dialogs import VideoOverlaySettingsDialog
    from .canvas import OverlayObject


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
        from .canvas import OverlayObject
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

    def _toggle_play(self, *args):
        if hasattr(self.overlay, 'is_playing'):
            self.overlay.is_playing = not getattr(self.overlay, 'is_playing', True)
            if hasattr(self, 'btn_play'):
                self.btn_play.setText("❚❚" if self.overlay.is_playing else "▶")
            self.player.canvas.update()

    def _toggle_aspect(self, *args):
        cur = getattr(self.overlay, 'keep_aspect_ratio', True)
        self.overlay.keep_aspect_ratio = not cur
        self.btn_aspect.setText("🔗" if self.overlay.keep_aspect_ratio else "🔓")
        self.player.canvas.update()

    def _toggle_visibility(self, *args):
        cur = getattr(self.overlay, 'is_visible', True)
        self.overlay.is_visible = not cur
        self.btn_vis.setText("👁️" if self.overlay.is_visible else "🚫")
        self.player.canvas.update()

    def _open_settings(self, *args):
        dlg = VideoOverlaySettingsDialog(self.overlay, parent=self.player)
        dlg.exec_()
        self.player.canvas.update()

    def _delete_overlay(self, *args):
        self.player.canvas.remove_overlay(self.overlay)

    def _on_slider_pressed(self):
        self._is_scrubbing = True

    def _on_slider_moved(self, val):
        from .canvas import OverlayObject
        sec = val / 100.0
        if self.overlay.obj_type == OverlayObject.TYPE_VIDEO:
            self.overlay.seek_to_seconds(sec)
        self.lbl_time.setText(f"{self._fmt_time(sec)} / {self._fmt_time(self.dur)}")
        self.player.canvas.update()

    def _on_slider_released(self):
        from .canvas import OverlayObject
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
        from .canvas import OverlayObject
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
