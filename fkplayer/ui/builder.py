#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI layout, toolbars, styling, shortcut bindings, and localization updating mixin for FKVideoPlayer.
Constructs the top toolbar, timeline slider, recording bar, tab bar, menus, and dark theme stylesheet.
"""

import os
import json
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QSpinBox, QFrame, QShortcut, QMenu, QAction,
    QTabWidget, QSizePolicy, QToolButton, QSlider
)

from fkplayer.ui.canvas import VideoCanvas
from fkplayer.ui.timeline import ClickableSlider, OverlayTrackContainer
from fkplayer.ui.dialogs import AutoAdjustTabBar, DEFAULT_HOTKEYS, HOTKEYS_CONFIG_PATH
from fkplayer.core.projects import ProjectManager
from fkplayer.core.geometry import resource_path
from fkplayer.core.i18n import tr, I18nManager
from fkplayer.core.logger import get_logger, open_log_file, open_logs_folder

logger = get_logger("UIBuilder")


class UIBuilderMixin:
    """Mixin constructing widgets, toolbars, menus, styling, and shortcuts."""

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
            btn.setObjectName("ColorPresetBtn")
            btn.setFixedSize(20, 20)
            btn.setToolTip(f"{name} ({hex_code})")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=hex_code: self._set_brush_color(c))
            layout.addWidget(btn)
            self.preset_color_buttons[hex_code.upper()] = btn

        self.btn_custom_color = QPushButton("🎨")
        self.btn_custom_color.setObjectName("ColorCustomBtn")
        self.btn_custom_color.setToolTip("Choose custom brush color...")
        self.btn_custom_color.setFixedSize(24, 24)
        self.btn_custom_color.clicked.connect(self._pick_custom_color)
        layout.addWidget(self.btn_custom_color)

        self.color_indicator = QFrame()
        self.color_indicator.setObjectName("ColorIndicator")
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
        self.btn_undo.clicked.connect(self.undo_last_action)
        layout.addWidget(self.btn_undo)

        self.btn_clear_all = QPushButton("🗑 Clear")
        self.btn_clear_all.setToolTip("Clear all drawings (Delete / C)")
        self.btn_clear_all.clicked.connect(self.clear_all_drawings)
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
        self.btn_zoom_out.clicked.connect(self.zoom_out)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Zoom in (+)")
        self.btn_zoom_in.clicked.connect(self.zoom_in)

        self.btn_zoom_reset = QPushButton("1:1")
        self.btn_zoom_reset.setToolTip("Fit to window (0)")
        self.btn_zoom_reset.clicked.connect(self.fit_to_view)

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

        reg_sc(cfg_hotkeys.get("undo", "Ctrl+Z"), self.undo_last_action)
        reg_sc(cfg_hotkeys.get("delete_overlay", "Delete"), self._on_delete_shortcut)
        reg_sc(Qt.Key_Backspace, self._on_delete_shortcut)
        reg_sc(cfg_hotkeys.get("duplicate_overlay", "Ctrl+D"), self._on_duplicate_shortcut)
        reg_sc("C", self.clear_all_drawings)

        reg_sc("[", lambda: self.spin_width.setValue(self.spin_width.value() - 1))
        reg_sc("]", lambda: self.spin_width.setValue(self.spin_width.value() + 1))

        reg_sc("<", self.decrease_speed)
        reg_sc(">", self.increase_speed)
        reg_sc("Shift+,", self.decrease_speed)
        reg_sc("Shift+.", self.increase_speed)
        reg_sc("R", self.reset_speed)

        reg_sc("+", self.zoom_in)
        reg_sc("=", self.zoom_in)
        reg_sc("-", self.zoom_out)
        reg_sc("0", self.fit_to_view)

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
            QPushButton#ColorPresetBtn {
                min-width: 16px;
                max-width: 16px;
                min-height: 16px;
                max-height: 16px;
                padding: 0px;
                margin: 0px;
                border-radius: 10px;
            }
            QPushButton#ColorCustomBtn {
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                padding: 0px;
                margin: 0px;
                border-radius: 4px;
                font-size: 13px;
            }
            QFrame#ColorIndicator {
                min-width: 14px;
                max-width: 14px;
                min-height: 14px;
                max-height: 14px;
                padding: 0px;
                margin: 0px;
                border-radius: 4px;
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

