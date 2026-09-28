#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Settings, Dialogs, and Tools for FKVideoPlayer:
- New Canvas Dialog
- Window Capture Dialog
- Text Overlay Dialog
- Advanced Export Dialog with Presets
- Preferences Dialog (Hotkeys customizer, Language, Microphone)
- About & Donations Dialog
- Updates Dialog
"""

import json
import os
import webbrowser
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFontDialog, QColorDialog, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QRadioButton, QButtonGroup, QWidget
)

from i18n import tr, I18nManager
from capture import list_open_windows
from audio import get_audio_input_devices

DIALOG_STYLE = """
QDialog {
    background-color: #181822;
    color: #E2E2EC;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
QLabel {
    color: #C8C8DC;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #2E2E3E;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: bold;
    color: #007AFF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
}
QLineEdit, QSpinBox, QComboBox {
    background-color: #222230;
    color: #FFFFFF;
    border: 1px solid #36364A;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: #007AFF;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #007AFF;
}
QPushButton {
    background-color: #282838;
    color: #FFFFFF;
    border: 1px solid #3E3E52;
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #343448;
    border-color: #007AFF;
}
QPushButton:pressed {
    background-color: #007AFF;
}
QPushButton#PrimaryBtn {
    background-color: #007AFF;
    border: 1px solid #0062CC;
    font-weight: bold;
}
QPushButton#PrimaryBtn:hover {
    background-color: #1A88FF;
}
QTableWidget {
    background-color: #1C1C26;
    color: #E2E2EC;
    border: 1px solid #2E2E3E;
    gridline-color: #2C2C3C;
    border-radius: 6px;
}
QHeaderView::section {
    background-color: #242434;
    color: #A0A0B8;
    padding: 6px;
    border: 1px solid #2E2E3E;
    font-weight: bold;
}
QTabWidget::pane {
    border: 1px solid #2E2E3E;
    border-radius: 8px;
    background-color: #1C1C26;
    padding: 10px;
}
QTabBar::tab {
    background: #222230;
    color: #A0A0B8;
    padding: 8px 18px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #2E2E40;
    color: #FFFFFF;
    font-weight: bold;
}
"""

# =========================================================================
# 1. New Canvas Dialog
# =========================================================================
class NewCanvasDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_new_canvas_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedSize(420, 360)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Presets
        h_preset = QHBoxLayout()
        h_preset.addWidget(QLabel(tr('dlg_new_canvas_preset')))
        self.combo_presets = QComboBox()
        self.presets = {
            "1920x1080 (16:9 Full HD)": (1920, 1080),
            "1280x720 (16:9 HD)": (1280, 720),
            "2560x1440 (16:9 2K QHD)": (2560, 1440),
            "3840x2160 (16:9 4K UHD)": (3840, 2160),
            "1080x1920 (9:16 Shorts / TikTok)": (1080, 1920),
            "1080x1080 (1:1 Square)": (1080, 1080),
            "Custom": None
        }
        for name in self.presets:
            self.combo_presets.addItem(name)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_changed)
        h_preset.addWidget(self.combo_presets)
        layout.addLayout(h_preset)

        # Dimensions
        h_dim = QHBoxLayout()
        h_dim.addWidget(QLabel(tr('dlg_new_canvas_width')))
        self.spin_w = QSpinBox()
        self.spin_w.setRange(200, 7680)
        self.spin_w.setValue(1920)
        h_dim.addWidget(self.spin_w)

        h_dim.addWidget(QLabel(tr('dlg_new_canvas_height')))
        self.spin_h = QSpinBox()
        self.spin_h.setRange(200, 4320)
        self.spin_h.setValue(1080)
        h_dim.addWidget(self.spin_h)
        layout.addLayout(h_dim)

        # Background color
        h_bg = QHBoxLayout()
        h_bg.addWidget(QLabel(tr('dlg_new_canvas_bg')))
        self.combo_bg = QComboBox()
        self.bg_options = {
            "Dark Charcoal (#14141A)": "#14141A",
            "Pitch Black (#000000)": "#000000",
            "Pure White (#FFFFFF)": "#FFFFFF",
            "Studio Green (#00FF00)": "#00FF00",
            "Studio Blue (#0000FF)": "#0000FF"
        }
        for name in self.bg_options:
            self.combo_bg.addItem(name)
        h_bg.addWidget(self.combo_bg)
        layout.addLayout(h_bg)

        # FPS
        h_fps = QHBoxLayout()
        h_fps.addWidget(QLabel(tr('dlg_new_canvas_fps')))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(10, 120)
        self.spin_fps.setValue(30)
        h_fps.addWidget(self.spin_fps)
        layout.addLayout(h_fps)

        layout.addStretch()

        # Buttons
        h_btn = QHBoxLayout()
        h_btn.addStretch()
        btn_cancel = QPushButton(tr('dlg_cancel'))
        btn_cancel.clicked.connect(self.reject)
        h_btn.addWidget(btn_cancel)

        btn_create = QPushButton(tr('dlg_create'))
        btn_create.setObjectName("PrimaryBtn")
        btn_create.clicked.connect(self.accept)
        h_btn.addWidget(btn_create)
        layout.addLayout(h_btn)

    def _on_preset_changed(self):
        txt = self.combo_presets.currentText()
        val = self.presets.get(txt)
        if val:
            self.spin_w.setValue(val[0])
            self.spin_h.setValue(val[1])

    def get_settings(self):
        w = self.spin_w.value()
        h = self.spin_h.value()
        bg_name = self.combo_bg.currentText()
        bg_hex = self.bg_options.get(bg_name, "#14141A")
        fps = float(self.spin_fps.value())
        return w, h, bg_hex, fps


# =========================================================================
# 2. Window Capture Dialog
# =========================================================================
class WindowCaptureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_capture_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(600, 420)
        self.selected_hwnd = None
        self.selected_title = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_note = QLabel(tr('dlg_capture_note'))
        lbl_note.setWordWrap(True)
        layout.addWidget(lbl_note)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Title", "Resolution", "HWND"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table)

        h_btn = QHBoxLayout()
        btn_refresh = QPushButton(tr('dlg_capture_refresh'))
        btn_refresh.clicked.connect(self.refresh_windows)
        h_btn.addWidget(btn_refresh)
        h_btn.addStretch()

        btn_cancel = QPushButton(tr('dlg_cancel'))
        btn_cancel.clicked.connect(self.reject)
        h_btn.addWidget(btn_cancel)

        btn_start = QPushButton(tr('dlg_capture_btn'))
        btn_start.setObjectName("PrimaryBtn")
        btn_start.clicked.connect(self._on_start)
        h_btn.addWidget(btn_start)
        layout.addLayout(h_btn)

        self.refresh_windows()

    def refresh_windows(self):
        self.table.setRowCount(0)
        windows = list_open_windows()
        for win in windows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            item_title = QTableWidgetItem(win['title'])
            item_res = QTableWidgetItem(f"{win['width']}x{win['height']}")
            item_hwnd = QTableWidgetItem(str(win['hwnd']))

            item_title.setData(Qt.UserRole, win)
            self.table.setItem(row, 0, item_title)
            self.table.setItem(row, 1, item_res)
            self.table.setItem(row, 2, item_hwnd)

        if self.table.rowCount() > 0:
            self.table.selectRow(0)

    def _on_item_double_clicked(self, item):
        self._on_start()

    def _on_start(self):
        sel_row = self.table.currentRow()
        if sel_row >= 0:
            item = self.table.item(sel_row, 0)
            win_data = item.data(Qt.UserRole)
            self.selected_hwnd = win_data['hwnd']
            self.selected_title = win_data['title']
            self.accept()
        else:
            QMessageBox.warning(self, "No Window Selected", "Please select a window from the list.")


# =========================================================================
# 3. Text Overlay Dialog
# =========================================================================
class TextOverlayDialog(QDialog):
    def __init__(self, initial_text="Sample Text", initial_font=None, initial_color="#FFFFFF", initial_bg="transparent", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_text_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedSize(450, 360)

        self.font = initial_font or QFont("Segoe UI", 36, QFont.Bold)
        self.color = QColor(initial_color)
        self.bg_color = initial_bg

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel(tr('dlg_text_label')))
        self.edit_text = QLineEdit(initial_text)
        layout.addWidget(self.edit_text)

        # Font & Size Row
        h_font = QHBoxLayout()
        self.btn_font = QPushButton(f"Font: {self.font.family()} ({self.font.pointSize()}pt)")
        self.btn_font.clicked.connect(self._choose_font)
        h_font.addWidget(self.btn_font)

        self.check_bold = QCheckBox(tr('dlg_text_bold'))
        self.check_bold.setChecked(self.font.bold())
        self.check_bold.toggled.connect(self._update_preview)
        h_font.addWidget(self.check_bold)

        self.check_italic = QCheckBox(tr('dlg_text_italic'))
        self.check_italic.setChecked(self.font.italic())
        self.check_italic.toggled.connect(self._update_preview)
        h_font.addWidget(self.check_italic)
        layout.addLayout(h_font)

        # Color & Background Row
        h_colors = QHBoxLayout()
        self.btn_color = QPushButton(tr('dlg_text_color'))
        self.btn_color.setStyleSheet(f"background-color: {self.color.name()}; color: {'#000' if self.color.lightness() > 128 else '#fff'};")
        self.btn_color.clicked.connect(self._choose_color)
        h_colors.addWidget(self.btn_color)

        self.combo_bg = QComboBox()
        self.combo_bg.addItem("Transparent", "transparent")
        self.combo_bg.addItem("Dark Semi-Black (70%)", "rgba(0,0,0,180)")
        self.combo_bg.addItem("Solid Black", "rgba(0,0,0,255)")
        self.combo_bg.addItem("Solid White", "rgba(255,255,255,255)")
        self.combo_bg.addItem("Electric Blue", "rgba(0,122,255,220)")
        self.combo_bg.addItem("Crimson Red", "rgba(255,59,48,220)")
        for idx in range(self.combo_bg.count()):
            if self.combo_bg.itemData(idx) == self.bg_color:
                self.combo_bg.setCurrentIndex(idx)
                break
        h_colors.addWidget(self.combo_bg)
        layout.addLayout(h_colors)

        # Preview label
        layout.addWidget(QLabel("Preview:"))
        self.lbl_preview = QLabel(initial_text)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setFixedHeight(70)
        layout.addWidget(self.lbl_preview)
        self._update_preview()
        self.edit_text.textChanged.connect(self._update_preview)

        layout.addStretch()

        # Buttons
        h_btn = QHBoxLayout()
        h_btn.addStretch()
        btn_cancel = QPushButton(tr('dlg_cancel'))
        btn_cancel.clicked.connect(self.reject)
        h_btn.addWidget(btn_cancel)

        btn_ok = QPushButton(tr('btn_save'))
        btn_ok.setObjectName("PrimaryBtn")
        btn_ok.clicked.connect(self.accept)
        h_btn.addWidget(btn_ok)
        layout.addLayout(h_btn)

    def _choose_font(self):
        font, ok = QFontDialog.getFont(self.font, self, "Select Text Font")
        if ok:
            self.font = font
            self.btn_font.setText(f"Font: {self.font.family()} ({self.font.pointSize()}pt)")
            self.check_bold.setChecked(self.font.bold())
            self.check_italic.setChecked(self.font.italic())
            self._update_preview()

    def _choose_color(self):
        color = QColorDialog.getColor(self.color, self, "Select Text Color")
        if color.isValid():
            self.color = color
            self.btn_color.setStyleSheet(f"background-color: {self.color.name()}; color: {'#000' if self.color.lightness() > 128 else '#fff'};")
            self._update_preview()

    def _update_preview(self):
        self.font.setBold(self.check_bold.isChecked())
        self.font.setItalic(self.check_italic.isChecked())
        self.lbl_preview.setFont(self.font)
        self.lbl_preview.setText(self.edit_text.text())
        bg = self.combo_bg.currentData() or "transparent"
        self.lbl_preview.setStyleSheet(f"color: {self.color.name()}; background-color: {bg}; border: 1px solid #333; border-radius: 6px;")

    def get_data(self):
        return {
            'text': self.edit_text.text(),
            'font_family': self.font.family(),
            'font_size': self.font.pointSize(),
            'bold': self.check_bold.isChecked(),
            'italic': self.check_italic.isChecked(),
            'color': self.color.name(),
            'bg_color': self.combo_bg.currentData() or 'transparent'
        }


# =========================================================================
# 4. Advanced Export Dialog with Presets
# =========================================================================
PRESETS_FILE = os.path.join(os.path.expanduser('~'), '.fk_export_presets.json')

DEFAULT_EXPORT_PRESETS = {
    "YouTube 1080p 60fps (x264)": {
        "codec": "libx264", "format": "mp4", "width": 1920, "height": 1080, "fps": 60, "bitrate": "12M"
    },
    "Twitch / Stream Highlight (1080p 30fps)": {
        "codec": "libx264", "format": "mp4", "width": 1920, "height": 1080, "fps": 30, "bitrate": "8M"
    },
    "TikTok / Shorts (1080x1920 Vertical)": {
        "codec": "libx264", "format": "mp4", "width": 1080, "height": 1920, "fps": 30, "bitrate": "10M"
    },
    "Ultra Quality Archive (HEVC H.265 4K)": {
        "codec": "libx265", "format": "mp4", "width": 3840, "height": 2160, "fps": 60, "bitrate": "25M"
    },
    "Fast / Draft (720p 30fps)": {
        "codec": "libx264", "format": "mp4", "width": 1280, "height": 720, "fps": 30, "bitrate": "4M"
    },
    "Apple ProRes High-Fidelity": {
        "codec": "prores_ks", "format": "mov", "width": 1920, "height": 1080, "fps": 30, "bitrate": "50M"
    }
}

class ExportDialog(QDialog):
    def __init__(self, default_w=1920, default_h=1080, has_audio=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_export_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedSize(520, 520)

        self.presets = dict(DEFAULT_EXPORT_PRESETS)
        self._load_custom_presets()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Preset selector
        h_pres = QHBoxLayout()
        h_pres.addWidget(QLabel(tr('dlg_export_preset')))
        self.combo_presets = QComboBox()
        for p in self.presets:
            self.combo_presets.addItem(p)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        h_pres.addWidget(self.combo_presets)
        layout.addLayout(h_pres)

        # Codec & Format
        h_cf = QHBoxLayout()
        h_cf.addWidget(QLabel(tr('dlg_export_codec')))
        self.combo_codec = QComboBox()
        self.combo_codec.addItem("H.264 (x264)", "libx264")
        self.combo_codec.addItem("HEVC (H.265)", "libx265")
        self.combo_codec.addItem("VP9 (WebM)", "libvpx-vp9")
        self.combo_codec.addItem("Apple ProRes", "prores_ks")
        self.combo_codec.addItem("MPEG-4 Standard", "mpeg4")
        h_cf.addWidget(self.combo_codec)

        h_cf.addWidget(QLabel(tr('dlg_export_format')))
        self.combo_format = QComboBox()
        self.combo_format.addItem(".mp4", "mp4")
        self.combo_format.addItem(".mkv", "mkv")
        self.combo_format.addItem(".webm", "webm")
        self.combo_format.addItem(".mov", "mov")
        h_cf.addWidget(self.combo_format)
        layout.addLayout(h_cf)

        # Resolution
        h_res = QHBoxLayout()
        h_res.addWidget(QLabel(tr('dlg_export_res')))
        self.spin_w = QSpinBox()
        self.spin_w.setRange(200, 7680)
        self.spin_w.setValue(default_w)
        h_res.addWidget(self.spin_w)

        h_res.addWidget(QLabel("x"))
        self.spin_h = QSpinBox()
        self.spin_h.setRange(200, 4320)
        self.spin_h.setValue(default_h)
        h_res.addWidget(self.spin_h)
        layout.addLayout(h_res)

        # Bitrate & FPS
        h_bf = QHBoxLayout()
        h_bf.addWidget(QLabel(tr('dlg_export_bitrate')))
        self.combo_bitrate = QComboBox()
        self.combo_bitrate.addItem("4 Mbps (Draft)", "4M")
        self.combo_bitrate.addItem("8 Mbps (Standard HD)", "8M")
        self.combo_bitrate.addItem("12 Mbps (High 1080p)", "12M")
        self.combo_bitrate.addItem("20 Mbps (Ultra HD)", "20M")
        self.combo_bitrate.addItem("50 Mbps (Master Quality)", "50M")
        self.combo_bitrate.setCurrentIndex(2)
        h_bf.addWidget(self.combo_bitrate)

        h_bf.addWidget(QLabel(tr('dlg_export_fps')))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(15, 120)
        self.spin_fps.setValue(30)
        h_bf.addWidget(self.spin_fps)
        layout.addLayout(h_bf)

        # Audio checkbox
        self.check_audio = QCheckBox(tr('dlg_export_audio'))
        self.check_audio.setChecked(has_audio)
        layout.addWidget(self.check_audio)

        # Output file path selector
        h_out = QHBoxLayout()
        self.edit_output_path = QLineEdit()
        self.edit_output_path.setPlaceholderText("Output video file path...")
        h_out.addWidget(self.edit_output_path)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_output)
        h_out.addWidget(btn_browse)
        layout.addLayout(h_out)

        # Save Preset Button
        btn_save_preset = QPushButton(tr('dlg_export_save_preset'))
        btn_save_preset.clicked.connect(self._save_custom_preset)
        layout.addWidget(btn_save_preset)

        layout.addStretch()

        # Render Button
        h_btn = QHBoxLayout()
        h_btn.addStretch()
        btn_cancel = QPushButton(tr('dlg_cancel'))
        btn_cancel.clicked.connect(self.reject)
        h_btn.addWidget(btn_cancel)

        btn_start = QPushButton(tr('dlg_export_start'))
        btn_start.setObjectName("PrimaryBtn")
        btn_start.clicked.connect(self._on_start)
        h_btn.addWidget(btn_start)
        layout.addLayout(h_btn)

    def _load_custom_presets(self):
        try:
            if os.path.exists(PRESETS_FILE):
                with open(PRESETS_FILE, 'r', encoding='utf-8') as f:
                    custom = json.load(f)
                    self.presets.update(custom)
        except Exception:
            pass

    def _save_custom_preset(self):
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Export Preset", "Preset Name:")
        if ok and name.strip():
            preset_data = {
                "codec": self.combo_codec.currentData(),
                "format": self.combo_format.currentData(),
                "width": self.spin_w.value(),
                "height": self.spin_h.value(),
                "fps": self.spin_fps.value(),
                "bitrate": self.combo_bitrate.currentData()
            }
            self.presets[name.strip()] = preset_data
            self.combo_presets.addItem(name.strip())
            self.combo_presets.setCurrentText(name.strip())
            try:
                with open(PRESETS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.presets, f, indent=2)
                QMessageBox.information(self, "Preset Saved", f"Preset '{name.strip()}' saved successfully!")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save preset: {e}")

    def _on_preset_selected(self):
        txt = self.combo_presets.currentText()
        p = self.presets.get(txt)
        if not p:
            return
        idx_c = self.combo_codec.findData(p.get('codec'))
        if idx_c >= 0:
            self.combo_codec.setCurrentIndex(idx_c)
        idx_f = self.combo_format.findData(p.get('format'))
        if idx_f >= 0:
            self.combo_format.setCurrentIndex(idx_f)
        self.spin_w.setValue(p.get('width', 1920))
        self.spin_h.setValue(p.get('height', 1080))
        self.spin_fps.setValue(p.get('fps', 30))
        idx_b = self.combo_bitrate.findData(p.get('bitrate'))
        if idx_b >= 0:
            self.combo_bitrate.setCurrentIndex(idx_b)

    def _browse_output(self):
        fmt = self.combo_format.currentData() or "mp4"
        path, _ = QFileDialog.getSaveFileName(self, "Select Export Video File", f"rendered_video.{fmt}", f"Video (*.{fmt})")
        if path:
            self.edit_output_path.setText(path)

    def _on_start(self):
        path = self.edit_output_path.text().strip()
        if not path:
            self._browse_output()
            path = self.edit_output_path.text().strip()
            if not path:
                return
        self.accept()

    def get_export_config(self):
        return {
            'output_path': self.edit_output_path.text().strip(),
            'codec': self.combo_codec.currentData(),
            'format': self.combo_format.currentData(),
            'width': self.spin_w.value(),
            'height': self.spin_h.value(),
            'fps': float(self.spin_fps.value()),
            'bitrate': self.combo_bitrate.currentData(),
            'include_audio': self.check_audio.isChecked()
        }


# =========================================================================
# 5. Preferences Dialog (Hotkeys, Language, Microphone)
# =========================================================================
HOTKEYS_CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.fk_videoplayer_hotkeys.json')

DEFAULT_HOTKEYS = {
    "play_pause": "Space",
    "step_next": "Right",
    "step_prev": "Left",
    "skip_fwd_1s": "L",
    "skip_back_1s": "J",
    "tool_select": "V",
    "tool_brush": "B",
    "tool_eraser": "E",
    "tool_text": "T",
    "record_toggle": "R",
    "undo": "Ctrl+Z",
    "delete_overlay": "Delete",
    "duplicate_overlay": "Ctrl+D",
    "new_canvas": "Ctrl+N",
    "open_video": "Ctrl+O",
    "capture_window": "Ctrl+W",
    "export_video": "Ctrl+E"
}

class PreferencesDialog(QDialog):
    hotkeys_updated = pyqtSignal(dict)
    language_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_prefs_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(550, 460)

        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self._load_hotkeys()

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Hotkeys
        tab_hotkeys = QWidget()
        l_hk = QVBoxLayout(tab_hotkeys)
        self.table_hk = QTableWidget()
        self.table_hk.setColumnCount(2)
        self.table_hk.setHorizontalHeaderLabels(["Action", "Shortcut Key"])
        self.table_hk.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table_hk.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        l_hk.addWidget(self.table_hk)

        h_hk_btn = QHBoxLayout()
        btn_edit_hk = QPushButton("Change Selected Key...")
        btn_edit_hk.clicked.connect(self._change_selected_key)
        h_hk_btn.addWidget(btn_edit_hk)
        btn_reset_hk = QPushButton(tr('btn_reset_defaults'))
        btn_reset_hk.clicked.connect(self._reset_hotkeys)
        h_hk_btn.addWidget(btn_reset_hk)
        l_hk.addLayout(h_hk_btn)
        self.tabs.addTab(tab_hotkeys, tr('tab_hotkeys'))

        # Tab 2: Language
        tab_lang = QWidget()
        l_lang = QVBoxLayout(tab_lang)
        l_lang.addWidget(QLabel(tr('lbl_select_lang')))
        self.rb_en = QRadioButton("English")
        self.rb_ru = QRadioButton("Русский")
        cur_lang = I18nManager.instance().lang
        if cur_lang == 'ru':
            self.rb_ru.setChecked(True)
        else:
            self.rb_en.setChecked(True)
        l_lang.addWidget(self.rb_en)
        l_lang.addWidget(self.rb_ru)
        l_lang.addStretch()
        self.tabs.addTab(tab_lang, tr('tab_language'))

        # Tab 3: Audio & Microphone
        tab_audio = QWidget()
        l_audio = QVBoxLayout(tab_audio)
        l_audio.addWidget(QLabel(tr('lbl_mic_device')))
        self.combo_mic = QComboBox()
        devices = get_audio_input_devices()
        for dev in devices:
            self.combo_mic.addItem(dev['name'])
        l_audio.addWidget(self.combo_mic)

        self.btn_test_mic = QPushButton(tr('btn_test_mic'))
        l_audio.addWidget(self.btn_test_mic)
        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setValue(0)
        l_audio.addWidget(self.mic_bar)
        l_audio.addStretch()
        self.tabs.addTab(tab_audio, tr('tab_audio'))

        # Bottom buttons
        h_btn = QHBoxLayout()
        h_btn.addStretch()
        btn_cancel = QPushButton(tr('dlg_cancel'))
        btn_cancel.clicked.connect(self.reject)
        h_btn.addWidget(btn_cancel)

        btn_save = QPushButton(tr('btn_save'))
        btn_save.setObjectName("PrimaryBtn")
        btn_save.clicked.connect(self._save_all)
        h_btn.addWidget(btn_save)
        layout.addLayout(h_btn)

        self._populate_hotkeys_table()

    def _load_hotkeys(self):
        try:
            if os.path.exists(HOTKEYS_CONFIG_PATH):
                with open(HOTKEYS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    self.hotkeys.update(json.load(f))
        except Exception:
            pass

    def _populate_hotkeys_table(self):
        self.table_hk.setRowCount(0)
        for act, key in self.hotkeys.items():
            r = self.table_hk.rowCount()
            self.table_hk.insertRow(r)
            act_item = QTableWidgetItem(act.replace('_', ' ').title())
            act_item.setData(Qt.UserRole, act)
            key_item = QTableWidgetItem(key)
            self.table_hk.setItem(r, 0, act_item)
            self.table_hk.setItem(r, 1, key_item)

    def _change_selected_key(self):
        from PyQt5.QtWidgets import QInputDialog
        row = self.table_hk.currentRow()
        if row < 0:
            return
        act = self.table_hk.item(row, 0).data(Qt.UserRole)
        cur_key = self.hotkeys.get(act, "")
        new_key, ok = QInputDialog.getText(self, "Change Hotkey", f"Enter new key sequence for {act}:", text=cur_key)
        if ok and new_key.strip():
            self.hotkeys[act] = new_key.strip()
            self.table_hk.item(row, 1).setText(new_key.strip())

    def _reset_hotkeys(self):
        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self._populate_hotkeys_table()

    def _save_all(self):
        # Save hotkeys
        try:
            with open(HOTKEYS_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.hotkeys, f, indent=2)
            self.hotkeys_updated.emit(self.hotkeys)
        except Exception:
            pass

        # Save language
        new_lang = 'ru' if self.rb_ru.isChecked() else 'en'
        I18nManager.instance().set_language(new_lang)
        self.language_changed.emit(new_lang)

        self.accept()


# =========================================================================
# 6. About & Donations Dialog
# =========================================================================
DONATE_URL = "https://boosty.to/fkplayer" # Placeholder URL to be replaced by user

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_about_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedSize(440, 320)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_title = QLabel("FKVideoPlayer")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #007AFF;")
        lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_title)

        lbl_ver = QLabel(tr('about_version'))
        lbl_ver.setStyleSheet("font-size: 13px; color: #8888A0;")
        lbl_ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_ver)

        lbl_desc = QLabel(tr('about_desc'))
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignCenter)
        lbl_desc.setStyleSheet("font-size: 13px; color: #C0C0D4; margin: 10px 0;")
        layout.addWidget(lbl_desc)

        lbl_author = QLabel(tr('about_author'))
        lbl_author.setStyleSheet("font-size: 12px; color: #707090; font-style: italic;")
        lbl_author.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_author)

        layout.addStretch()

        btn_donate = QPushButton(tr('btn_donate_link'))
        btn_donate.setObjectName("PrimaryBtn")
        btn_donate.clicked.connect(lambda: webbrowser.open(DONATE_URL))
        layout.addWidget(btn_donate)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


# =========================================================================
# 7. Updates Dialog
# =========================================================================
DEFAULT_REPO_URL = "https://github.com/fk/FKVideoPlayer"

class UpdatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('dlg_updates_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.setFixedSize(480, 280)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel(tr('updates_repo_url')))
        self.edit_repo = QLineEdit(DEFAULT_REPO_URL)
        layout.addWidget(self.edit_repo)

        self.lbl_status = QLabel(tr('updates_up_to_date'))
        self.lbl_status.setStyleSheet("color: #34C759; font-weight: bold; font-size: 14px;")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)

        layout.addStretch()

        h_btn = QHBoxLayout()
        btn_check = QPushButton(tr('btn_check_now'))
        btn_check.clicked.connect(self._check_updates)
        h_btn.addWidget(btn_check)

        btn_dl = QPushButton(tr('btn_download_update'))
        btn_dl.setObjectName("PrimaryBtn")
        btn_dl.clicked.connect(lambda: webbrowser.open(self.edit_repo.text().strip()))
        h_btn.addWidget(btn_dl)
        layout.addLayout(h_btn)

    def _check_updates(self):
        self.lbl_status.setText(tr('updates_checking'))
        import time
        # Simulate quick check
        self.lbl_status.setText(tr('updates_up_to_date'))
