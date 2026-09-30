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
from PyQt5.QtCore import Qt, QSize, pyqtSignal, QThread
from PyQt5.QtGui import QFont, QColor, QIcon, QKeySequence, QFontMetrics
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QSlider, QCheckBox, QTabWidget, QTabBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QFontDialog, QColorDialog, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QRadioButton, QButtonGroup, QWidget, QTextEdit,
    QInputDialog
)

try:
    from fkplayer.core.i18n import tr, I18nManager
    from fkplayer.media.capture import list_open_windows
    from fkplayer.media.audio import get_audio_input_devices, MicLevelMonitor
    from fkplayer.core.logger import get_logger
except (ImportError, ValueError):
    from ...core.i18n import tr, I18nManager
    from ...media.capture import list_open_windows
    from ...media.audio import get_audio_input_devices, MicLevelMonitor
    from ...core.logger import get_logger

logger = get_logger("Settings")

def safe_open_url(url: str):
    if not url:
        return
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.error(f"Failed to open URL '{url}': {e}")

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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #222230;
    color: #FFFFFF;
    border: 1px solid #36364A;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: #007AFF;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #007AFF;
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
    font-size: 13px;
    spacing: 8px;
}
QCheckBox:hover, QRadioButton:hover {
    color: #FFFFFF;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
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
    width: 17px;
    height: 17px;
    border: 1px solid #3E3E54;
    border-radius: 9px;
    background-color: #222230;
}
QRadioButton::indicator:hover {
    border-color: #007AFF;
}
QRadioButton::indicator:checked {
    background-color: #007AFF;
    border-color: #007AFF;
}
QPushButton {
    background-color: #282838;
    color: #FFFFFF;
    border: 1px solid #3E3E52;
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 500;
    min-height: 24px;
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
QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
    border: none;
}
QTabBar::tab {
    background: #222230;
    color: #A0A0B8;
    border: 1px solid #282838;
    border-bottom: none;
    padding: 6px 16px;
    font-size: 12px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background: #2E2E40;
    color: #FFFFFF;
    font-weight: bold;
    border: 1px solid #007AFF;
    border-bottom: 2px solid #007AFF;
}
"""


class AutoAdjustTabBar(QTabBar):
    """QTabBar that dynamically calculates tabSizeHint based on the actual
    text font metrics (including bold state for selected tab and extra horizontal margin)
    so tab titles are never cut off in any language, font, or DPI scaling."""

    def __init__(self, parent=None, extra_padding=48, min_tab_width=110):
        super().__init__(parent)
        self.extra_padding = extra_padding
        self.min_tab_width = min_tab_width

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        text = self.tabText(index)
        if not text:
            return hint

        # Use bold font metrics to ensure tab has enough room even when selected and bold
        f = self.font()
        f.setBold(True)
        if f.pointSize() < 9:
            f.setPointSize(10)
        fm = QFontMetrics(f)

        text_w = fm.horizontalAdvance(text) if hasattr(fm, 'horizontalAdvance') else fm.width(text)
        icon = self.tabIcon(index)
        icon_w = (self.iconSize().width() + 8) if not icon.isNull() else 0
        close_btn_w = 26 if self.tabsClosable() else 0

        target_w = max(self.min_tab_width, text_w + icon_w + close_btn_w + self.extra_padding)
        target_h = max(hint.height(), fm.height() + 16)
        return QSize(target_w, target_h)


# =========================================================================
# 1. New Canvas Dialog
# =========================================================================
class NewCanvasDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_new_canvas_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(450, 380)
        self.setMinimumSize(430, 360)

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
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
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
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_text_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(480, 400)
        self.setMinimumSize(460, 380)

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
        "codec": "libx264", "format": "mp4", "width": 1920, "height": 1080, "fps": 60, "bitrate": "12000k", "rate_control": "vbr"
    },
    "Twitch / Stream Highlight (1080p 30fps)": {
        "codec": "libx264", "format": "mp4", "width": 1920, "height": 1080, "fps": 30, "bitrate": "8000k", "rate_control": "cbr"
    },
    "TikTok / Shorts (1080x1920 Vertical)": {
        "codec": "libx264", "format": "mp4", "width": 1080, "height": 1920, "fps": 30, "bitrate": "10000k", "rate_control": "vbr"
    },
    "Ultra Quality Archive (HEVC H.265 4K)": {
        "codec": "libx265", "format": "mp4", "width": 3840, "height": 2160, "fps": 60, "bitrate": "25000k", "rate_control": "vbr"
    },
    "Fast / Draft (720p 30fps)": {
        "codec": "libx264", "format": "mp4", "width": 1280, "height": 720, "fps": 30, "bitrate": "4000k", "rate_control": "vbr"
    },
    "Apple ProRes High-Fidelity": {
        "codec": "prores_ks", "format": "mov", "width": 1920, "height": 1080, "fps": 30, "bitrate": "50000k", "rate_control": "vbr"
    },
    "Custom (User Defined)": {
        "codec": "libx264", "format": "mp4", "width": 1920, "height": 1080, "fps": 30, "bitrate": "10000k", "rate_control": "vbr"
    }
}

class ExportDialog(QDialog):
    def __init__(self, default_w=1920, default_h=1080, has_audio=False, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_export_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(580, 590)
        self.setMinimumSize(560, 560)

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

        # Rate Control & FPS
        h_rc = QHBoxLayout()
        h_rc.addWidget(QLabel(tr('dlg_export_rate_control')))
        self.combo_rate_control = QComboBox()
        self.combo_rate_control.addItem(tr('rate_control_vbr'), "vbr")
        self.combo_rate_control.addItem(tr('rate_control_cbr'), "cbr")
        h_rc.addWidget(self.combo_rate_control)

        h_rc.addWidget(QLabel(tr('dlg_export_fps')))
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(15, 120)
        self.spin_fps.setValue(30)
        h_rc.addWidget(self.spin_fps)
        layout.addLayout(h_rc)

        # Bitrate preset combo
        h_bf = QHBoxLayout()
        h_bf.addWidget(QLabel(tr('dlg_export_bitrate')))
        self.combo_bitrate = QComboBox()
        self.combo_bitrate.addItem("4,000 kbps (Draft)", 4000)
        self.combo_bitrate.addItem("8,000 kbps (Standard HD)", 8000)
        self.combo_bitrate.addItem("12,000 kbps (High 1080p)", 12000)
        self.combo_bitrate.addItem("20,000 kbps (Ultra HD)", 20000)
        self.combo_bitrate.addItem("50,000 kbps (Master Quality)", 50000)
        self.combo_bitrate.addItem(tr('dlg_bitrate_custom'), -1)
        self.combo_bitrate.setCurrentIndex(2)
        h_bf.addWidget(self.combo_bitrate)
        layout.addLayout(h_bf)

        # Custom Bitrate SpinBox row (ONLY visible if Custom is chosen!)
        self.row_custom_bitrate = QWidget()
        h_cb = QHBoxLayout(self.row_custom_bitrate)
        h_cb.setContentsMargins(0, 0, 0, 0)
        h_cb.addWidget(QLabel(tr('dlg_export_custom_bitrate')))
        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(250, 300000)
        self.spin_bitrate.setSingleStep(500)
        self.spin_bitrate.setValue(12000)
        self.spin_bitrate.setSuffix(" kbps")
        h_cb.addWidget(self.spin_bitrate)
        layout.addWidget(self.row_custom_bitrate)
        self.row_custom_bitrate.setVisible(False)

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

        # Preset action buttons (Save / Delete)
        h_pres_act = QHBoxLayout()
        btn_save_preset = QPushButton(tr('dlg_export_save_preset'))
        btn_save_preset.clicked.connect(self._save_custom_preset)
        h_pres_act.addWidget(btn_save_preset)

        self.btn_del_preset = QPushButton("🗑 Delete Preset")
        self.btn_del_preset.clicked.connect(self._delete_custom_preset)
        h_pres_act.addWidget(self.btn_del_preset)
        layout.addLayout(h_pres_act)

        # Connect controls to switch to Custom when modified
        self._updating_preset = False
        self._syncing_bitrate = False
        self.combo_codec.currentIndexChanged.connect(self._on_param_changed)
        self.combo_format.currentIndexChanged.connect(self._on_param_changed)
        self.spin_w.valueChanged.connect(self._on_param_changed)
        self.spin_h.valueChanged.connect(self._on_param_changed)
        self.combo_rate_control.currentIndexChanged.connect(self._on_param_changed)
        self.spin_fps.valueChanged.connect(self._on_param_changed)
        self.combo_bitrate.currentIndexChanged.connect(self._on_bitrate_combo_changed)
        self.spin_bitrate.valueChanged.connect(self._on_bitrate_spin_changed)

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

    def _on_param_changed(self):
        if getattr(self, '_updating_preset', False):
            return
        idx = self.combo_presets.findText("Custom (User Defined)")
        if idx >= 0 and self.combo_presets.currentIndex() != idx:
            self.combo_presets.blockSignals(True)
            self.combo_presets.setCurrentIndex(idx)
            self.combo_presets.blockSignals(False)

    def _delete_custom_preset(self):
        name = self.combo_presets.currentText()
        if name in DEFAULT_EXPORT_PRESETS:
            QMessageBox.information(self, "Cannot Delete", "Built-in default presets cannot be deleted.")
            return
        reply = QMessageBox.question(self, "Delete Preset", f"Are you sure you want to delete preset '{name}'?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.presets.pop(name, None)
            idx = self.combo_presets.findText(name)
            if idx >= 0:
                self.combo_presets.removeItem(idx)
            try:
                # Save remaining custom presets
                custom_to_save = {k: v for k, v in self.presets.items() if k not in DEFAULT_EXPORT_PRESETS}
                with open(PRESETS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(custom_to_save, f, indent=2)
            except Exception:
                pass
            self.combo_presets.setCurrentIndex(0)

    def _on_bitrate_combo_changed(self):
        val = self.combo_bitrate.currentData()
        is_custom = (val == -1 or val is None)
        self.row_custom_bitrate.setVisible(is_custom)
        if not is_custom and val is not None and int(val) > 0:
            self._syncing_bitrate = True
            self.spin_bitrate.setValue(int(val))
            self._syncing_bitrate = False
        self._on_param_changed()

    def _on_bitrate_spin_changed(self):
        self._on_param_changed()

    def _get_bitrate_str(self):
        val = self.combo_bitrate.currentData()
        if val is not None and int(val) > 0:
            return f"{int(val)}k"
        return f"{self.spin_bitrate.value()}k"

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
                "bitrate": self._get_bitrate_str(),
                "rate_control": self.combo_rate_control.currentData()
            }
            self.presets[name.strip()] = preset_data
            self.combo_presets.addItem(name.strip())
            self.combo_presets.setCurrentText(name.strip())
            try:
                custom_to_save = {k: v for k, v in self.presets.items() if k not in DEFAULT_EXPORT_PRESETS}
                with open(PRESETS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(custom_to_save, f, indent=2)
                QMessageBox.information(self, "Preset Saved", f"Preset '{name.strip()}' saved successfully!")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save preset: {e}")

    def _on_preset_selected(self):
        txt = self.combo_presets.currentText()
        p = self.presets.get(txt)
        if not p:
            return
        self._updating_preset = True
        try:
            idx_c = self.combo_codec.findData(p.get('codec'))
            if idx_c >= 0:
                self.combo_codec.setCurrentIndex(idx_c)
            idx_f = self.combo_format.findData(p.get('format'))
            if idx_f >= 0:
                self.combo_format.setCurrentIndex(idx_f)
            self.spin_w.setValue(p.get('width', 1920))
            self.spin_h.setValue(p.get('height', 1080))
            self.spin_fps.setValue(p.get('fps', 30))

            rc = str(p.get('rate_control', 'vbr')).lower()
            idx_rc = self.combo_rate_control.findData(rc)
            if idx_rc >= 0:
                self.combo_rate_control.setCurrentIndex(idx_rc)

            b_raw = str(p.get('bitrate', '12000k')).upper().strip()
            try:
                if b_raw.endswith('M'):
                    b_num = int(float(b_raw[:-1]) * 1000)
                elif b_raw.endswith('K'):
                    b_num = int(float(b_raw[:-1]))
                else:
                    b_num = int(float(b_raw) / 1000)
            except Exception:
                b_num = 12000

            self._syncing_bitrate = True
            self.spin_bitrate.setValue(b_num)
            matched = False
            for i in range(self.combo_bitrate.count() - 1):
                d = self.combo_bitrate.itemData(i)
                if d is not None and abs(int(d) - b_num) < 10:
                    self.combo_bitrate.setCurrentIndex(i)
                    self.row_custom_bitrate.setVisible(False)
                    matched = True
                    break
            if not matched:
                self.combo_bitrate.setCurrentIndex(self.combo_bitrate.count() - 1)
                self.row_custom_bitrate.setVisible(True)
            self._syncing_bitrate = False
        finally:
            self._updating_preset = False

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
            'bitrate': self._get_bitrate_str(),
            'rate_control': self.combo_rate_control.currentData(),
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
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_prefs_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(680, 540)
        self.setMinimumSize(660, 500)

        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self._load_hotkeys()

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.setTabBar(AutoAdjustTabBar(self.tabs, extra_padding=48, min_tab_width=110))
        self.tabs.setUsesScrollButtons(True)
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

        self.mic_monitor = MicLevelMonitor(self)
        self.mic_monitor.level_changed.connect(self._on_mic_level)
        self.btn_test_mic.clicked.connect(self._toggle_mic_test)

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

    def _on_mic_level(self, level: int):
        self.mic_bar.setValue(level)

    def _toggle_mic_test(self):
        if self.mic_monitor.is_monitoring:
            self.mic_monitor.stop_monitoring()
            self.btn_test_mic.setText(tr('btn_test_mic'))
            self.mic_bar.setValue(0)
        else:
            dev_name = self.combo_mic.currentText().strip()
            ok = self.mic_monitor.start_monitoring(dev_name)
            if ok:
                self.btn_test_mic.setText("Stop Test 🛑")
            else:
                QMessageBox.warning(self, "Mic Error", "Unable to start microphone testing on this device.")

    def closeEvent(self, event):
        if hasattr(self, 'mic_monitor'):
            self.mic_monitor.stop_monitoring()
        super().closeEvent(event)

    def reject(self):
        if hasattr(self, 'mic_monitor'):
            self.mic_monitor.stop_monitoring()
        super().reject()

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
        if hasattr(self, 'mic_monitor'):
            self.mic_monitor.stop_monitoring()

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
# 6. Video Overlay Settings Dialog
# =========================================================================
class VideoOverlaySettingsDialog(QDialog):
    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.overlay = overlay
        self.setWindowTitle(tr('dlg_overlay_video_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(520, 460)
        self.setMinimumSize(490, 440)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        fname = os.path.basename(getattr(self.overlay, 'file_path', 'video'))
        lbl_info = QLabel(f"Video: {fname}")
        lbl_info.setStyleSheet("font-weight: bold; color: #00E5FF; font-size: 14px;")
        layout.addWidget(lbl_info)

        # Controls & Stepping Row
        h_play = QHBoxLayout()
        is_playing = getattr(self.overlay, 'is_playing', True)
        self.btn_play_pause = QPushButton("❚❚ Pause" if is_playing else "▶ Play")
        self.btn_play_pause.clicked.connect(self._toggle_play)
        h_play.addWidget(self.btn_play_pause)

        self.btn_step_back_1s = QPushButton("-1s")
        self.btn_step_back_1s.setToolTip("Rewind 1 second")
        self.btn_step_back_1s.clicked.connect(lambda: self._seek_relative(-1.0))
        h_play.addWidget(self.btn_step_back_1s)

        self.btn_step_back_1f = QPushButton("-1f")
        self.btn_step_back_1f.setToolTip("Step back 1 frame")
        self.btn_step_back_1f.clicked.connect(lambda: self._step_frame(-1))
        h_play.addWidget(self.btn_step_back_1f)

        self.btn_step_fwd_1f = QPushButton("+1f")
        self.btn_step_fwd_1f.setToolTip("Step forward 1 frame")
        self.btn_step_fwd_1f.clicked.connect(lambda: self._step_frame(1))
        h_play.addWidget(self.btn_step_fwd_1f)

        self.btn_step_fwd_1s = QPushButton("+1s")
        self.btn_step_fwd_1s.setToolTip("Forward 1 second")
        self.btn_step_fwd_1s.clicked.connect(lambda: self._seek_relative(1.0))
        h_play.addWidget(self.btn_step_fwd_1s)

        self.btn_restart = QPushButton("⏮ 0s")
        self.btn_restart.setToolTip("Restart overlay video from 0s")
        self.btn_restart.clicked.connect(self._restart_overlay)
        h_play.addWidget(self.btn_restart)
        layout.addLayout(h_play)

        # Scrubber Slider & Time Label
        h_scrub = QHBoxLayout()
        self.slider_scrub = QSlider(Qt.Horizontal)
        tot_frames = getattr(self.overlay, 'video_total_frames', 0)
        self.slider_scrub.setRange(0, max(0, tot_frames - 1))
        cur_f = getattr(self.overlay, 'video_cached_idx', 0)
        self.slider_scrub.setValue(max(0, cur_f))
        self.slider_scrub.valueChanged.connect(self._on_scrubber_changed)
        h_scrub.addWidget(self.slider_scrub)

        cur_t = self.overlay.get_current_time()
        tot_dur = self.overlay.get_duration()
        self.lbl_time = QLabel(f"{cur_t:.1f}s / {tot_dur:.1f}s")
        self.lbl_time.setStyleSheet("font-family: Consolas, monospace; font-size: 11px; color: #9EABB8;")
        h_scrub.addWidget(self.lbl_time)
        layout.addLayout(h_scrub)

        # Loop & Sync
        self.check_loop = QCheckBox(tr('lbl_overlay_loop'))
        self.check_loop.setChecked(getattr(self.overlay, 'loop', True))
        self.check_loop.toggled.connect(self._on_loop_toggled)
        layout.addWidget(self.check_loop)

        self.check_sync = QCheckBox(tr('lbl_overlay_sync'))
        self.check_sync.setChecked(getattr(self.overlay, 'sync_with_timeline', True))
        self.check_sync.toggled.connect(self._on_sync_toggled)
        layout.addWidget(self.check_sync)

        # Playback speed
        h_spd = QHBoxLayout()
        h_spd.addWidget(QLabel(tr('lbl_overlay_speed')))
        self.combo_speed = QComboBox()
        self.speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        for sp in self.speeds:
            self.combo_speed.addItem(f"{sp:.2f}x", sp)
        cur_spd = getattr(self.overlay, 'playback_speed', 1.0)
        idx = self.combo_speed.findData(cur_spd)
        if idx >= 0:
            self.combo_speed.setCurrentIndex(idx)
        self.combo_speed.currentIndexChanged.connect(self._on_speed_changed)
        h_spd.addWidget(self.combo_speed)
        layout.addLayout(h_spd)

        # Opacity slider
        h_op = QHBoxLayout()
        h_op.addWidget(QLabel(tr('lbl_overlay_opacity')))
        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(10, 100)
        cur_op = int(getattr(self.overlay, 'opacity', 1.0) * 100)
        self.slider_opacity.setValue(cur_op)
        self.lbl_op_val = QLabel(f"{cur_op}%")
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        h_op.addWidget(self.slider_opacity)
        h_op.addWidget(self.lbl_op_val)
        layout.addLayout(h_op)

        # Start offset
        h_off = QHBoxLayout()
        h_off.addWidget(QLabel(tr('lbl_overlay_offset')))
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(0.0, 3600.0)
        self.spin_offset.setSingleStep(0.5)
        self.spin_offset.setValue(getattr(self.overlay, 'start_offset', 0.0))
        self.spin_offset.valueChanged.connect(self._on_offset_changed)
        h_off.addWidget(self.spin_offset)
        # Aspect Ratio Preservation
        self.check_aspect = QCheckBox("Preserve Aspect Ratio (Lock)")
        self.check_aspect.setChecked(getattr(self.overlay, 'keep_aspect_ratio', True))
        self.check_aspect.toggled.connect(lambda v: setattr(self.overlay, 'keep_aspect_ratio', v))
        layout.addWidget(self.check_aspect)

        layout.addStretch()

        btn_close = QPushButton("OK")
        btn_close.setObjectName("PrimaryBtn")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _update_time_label(self):
        cur_t = self.overlay.get_current_time()
        tot_dur = self.overlay.get_duration()
        cur_f = getattr(self.overlay, 'video_cached_idx', 0)
        self.lbl_time.setText(f"{cur_t:.1f}s / {tot_dur:.1f}s [F:{cur_f}]")

    def _toggle_play(self):
        cur = getattr(self.overlay, 'is_playing', True)
        self.overlay.is_playing = not cur
        self.btn_play_pause.setText("❚❚ Pause" if self.overlay.is_playing else "▶ Play")
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _restart_overlay(self):
        self.overlay.seek_to_seconds(0.0)
        self.spin_offset.setValue(0.0)
        self.slider_scrub.blockSignals(True)
        self.slider_scrub.setValue(0)
        self.slider_scrub.blockSignals(False)
        self._update_time_label()
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _seek_relative(self, delta_sec: float):
        cur_t = self.overlay.get_current_time()
        new_t = max(0.0, cur_t + delta_sec)
        self.overlay.seek_to_seconds(new_t)
        self.spin_offset.setValue(self.overlay.start_offset)
        self.slider_scrub.blockSignals(True)
        self.slider_scrub.setValue(getattr(self.overlay, 'video_cached_idx', 0))
        self.slider_scrub.blockSignals(False)
        self._update_time_label()
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _step_frame(self, delta: int):
        self.overlay.step_frames(delta)
        self.spin_offset.setValue(self.overlay.start_offset)
        self.slider_scrub.blockSignals(True)
        self.slider_scrub.setValue(getattr(self.overlay, 'video_cached_idx', 0))
        self.slider_scrub.blockSignals(False)
        self._update_time_label()
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _on_scrubber_changed(self, frame_idx: int):
        self.overlay.seek_to_frame(frame_idx)
        self.spin_offset.setValue(self.overlay.start_offset)
        self._update_time_label()
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _on_loop_toggled(self, val):
        self.overlay.loop = val

    def _on_sync_toggled(self, val):
        self.overlay.sync_with_timeline = val

    def _on_speed_changed(self):
        spd = self.combo_speed.currentData()
        if spd:
            self.overlay.playback_speed = float(spd)

    def _on_opacity_changed(self, val):
        self.overlay.opacity = float(val) / 100.0
        self.lbl_op_val.setText(f"{val}%")
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()

    def _on_offset_changed(self, val):
        self.overlay.seek_to_seconds(float(val))
        self.slider_scrub.blockSignals(True)
        self.slider_scrub.setValue(getattr(self.overlay, 'video_cached_idx', 0))
        self.slider_scrub.blockSignals(False)
        self._update_time_label()
        if self.parent() and hasattr(self.parent(), 'update'):
            self.parent().update()


# =========================================================================
# 7. About & Donations Dialog
# =========================================================================
DONATEPAY_URL = "https://new.donatepay.ru/donate/ttvfurrykit"
DONATIONALERTS_URL = "https://www.donationalerts.com/r/ttvfurrykit"
TELEGRAM_URL = "https://t.me/furrykit"
GITHUB_URL = "https://github.com/furrykit"

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_about_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(520, 440)
        self.setMinimumSize(490, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_title = QLabel("FKVideoPlayer")
        lbl_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #007AFF;")
        lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_title)

        lbl_ver = QLabel(tr('about_version'))
        lbl_ver.setStyleSheet("font-size: 13px; color: #8888A0;")
        lbl_ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_ver)

        lbl_desc = QLabel(tr('about_desc'))
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignCenter)
        lbl_desc.setStyleSheet("font-size: 13px; color: #C0C0D4; margin: 4px 0;")
        layout.addWidget(lbl_desc)

        lbl_author = QLabel(tr('about_author'))
        lbl_author.setStyleSheet("font-size: 13px; color: #00E5FF; font-weight: bold;")
        lbl_author.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_author)

        # Contacts Row
        lbl_contact = QLabel("Contacts & Links:")
        lbl_contact.setStyleSheet("color: #A0A0B8; font-size: 12px; font-weight: 500;")
        lbl_contact.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_contact)

        h_contact = QHBoxLayout()
        btn_tg = QPushButton("💬 Telegram: @furrykit")
        btn_tg.setStyleSheet("background-color: #229ED9; color: #FFFFFF; font-weight: bold; border: 1px solid #1E88BD;")
        btn_tg.clicked.connect(lambda: safe_open_url(TELEGRAM_URL))
        h_contact.addWidget(btn_tg)

        btn_gh = QPushButton("🐙 GitHub: furrykit")
        btn_gh.setStyleSheet("background-color: #24292E; color: #FFFFFF; font-weight: bold; border: 1px solid #3F4448;")
        btn_gh.clicked.connect(lambda: safe_open_url(GITHUB_URL))
        h_contact.addWidget(btn_gh)
        layout.addLayout(h_contact)

        # Support Row
        lbl_sup = QLabel("Support Creator:")
        lbl_sup.setStyleSheet("color: #A0A0B8; font-size: 12px; font-weight: 500;")
        lbl_sup.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_sup)

        h_don = QHBoxLayout()
        btn_dp = QPushButton("DonatePay")
        btn_dp.setObjectName("PrimaryBtn")
        btn_dp.clicked.connect(lambda: safe_open_url(DONATEPAY_URL))
        h_don.addWidget(btn_dp)

        btn_da = QPushButton("DonationAlerts")
        btn_da.setStyleSheet("background-color: #E85D04; color: #FFFFFF; font-weight: bold; border: 1px solid #DC2F02;")
        btn_da.clicked.connect(lambda: safe_open_url(DONATIONALERTS_URL))
        h_don.addWidget(btn_da)
        layout.addLayout(h_don)

        layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)


# =========================================================================
# 6.5 Welcome & Donation Dialog
# =========================================================================
class WelcomeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('welcome_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(560, 480)
        self.setMinimumSize(530, 450)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_title = QLabel(tr('welcome_heading'))
        lbl_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #007AFF;")
        lbl_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_title)

        lbl_desc = QLabel(tr('welcome_desc'))
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("font-size: 13px; color: #D0D0E2; line-height: 1.5; margin: 4px 6px;")
        lbl_desc.setAlignment(Qt.AlignLeft)
        layout.addWidget(lbl_desc)

        # Support Section Box
        box_sup = QGroupBox(tr('welcome_support_title'))
        l_sup = QVBoxLayout(box_sup)
        l_sup.setSpacing(8)

        h_don = QHBoxLayout()
        btn_dp = QPushButton("💸 DonatePay")
        btn_dp.setObjectName("PrimaryBtn")
        btn_dp.setStyleSheet("font-size: 13px; font-weight: bold; padding: 8px 16px;")
        btn_dp.clicked.connect(lambda: safe_open_url(DONATEPAY_URL))
        h_don.addWidget(btn_dp)

        btn_da = QPushButton("☕ DonationAlerts")
        btn_da.setStyleSheet("background-color: #E85D04; color: #FFFFFF; font-size: 13px; font-weight: bold; border: 1px solid #DC2F02; padding: 8px 16px;")
        btn_da.clicked.connect(lambda: safe_open_url(DONATIONALERTS_URL))
        h_don.addWidget(btn_da)
        l_sup.addLayout(h_don)

        h_social = QHBoxLayout()
        btn_tg = QPushButton("💬 Telegram: @furrykit")
        btn_tg.setStyleSheet("background-color: #229ED9; color: #FFFFFF; font-weight: bold; border: 1px solid #1E88BD; padding: 6px 12px;")
        btn_tg.clicked.connect(lambda: safe_open_url(TELEGRAM_URL))
        h_social.addWidget(btn_tg)

        btn_gh = QPushButton("🐙 GitHub: furrykit")
        btn_gh.setStyleSheet("background-color: #24292E; color: #FFFFFF; font-weight: bold; border: 1px solid #3F4448; padding: 6px 12px;")
        btn_gh.clicked.connect(lambda: safe_open_url(GITHUB_URL))
        h_social.addWidget(btn_gh)
        l_sup.addLayout(h_social)

        layout.addWidget(box_sup)

        layout.addStretch()

        # Don't show again checkbox & Start button
        h_bot = QHBoxLayout()
        self.check_dont_show = QCheckBox(tr('welcome_dont_show'))
        from PyQt5.QtCore import QSettings
        settings = QSettings("furrykit", "FKVideoPlayer")
        dont_show = not settings.value("show_welcome", True, type=bool)
        self.check_dont_show.setChecked(dont_show)
        h_bot.addWidget(self.check_dont_show)

        h_bot.addStretch()

        btn_start = QPushButton(tr('welcome_start_btn'))
        btn_start.setObjectName("PrimaryBtn")
        btn_start.setStyleSheet("font-size: 13px; font-weight: bold; padding: 8px 24px;")
        btn_start.clicked.connect(self._on_start_clicked)
        h_bot.addWidget(btn_start)
        layout.addLayout(h_bot)

    def _on_start_clicked(self):
        from PyQt5.QtCore import QSettings
        settings = QSettings("furrykit", "FKVideoPlayer")
        show_welcome = not self.check_dont_show.isChecked()
        settings.setValue("show_welcome", show_welcome)
        self.accept()


# =========================================================================
# 7. Updates Dialog & Real GitHub Release Checker
# =========================================================================
DEFAULT_REPO_URL = "https://github.com/furrykit/FKVideoPlayer"
CURRENT_VERSION = "1.1.3"


def parse_version(v_str: str) -> tuple:
    import re
    clean = re.sub(r'^[^\d]*', '', str(v_str).strip())
    parts = re.split(r'[\.\-_]', clean)
    res = []
    for p in parts:
        try:
            res.append(int(p))
        except ValueError:
            break
    return tuple(res) if res else (0,)


class GitHubUpdateCheckerWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, repo="furrykit/FKVideoPlayer", current_version=CURRENT_VERSION, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current_version = current_version

    def run(self):
        import urllib.request
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "FKVideoPlayer-App"
        })
        try:
            # Bypass potential broken local proxy in environment
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception:
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
            except Exception as e:
                self.error.emit(str(e))
                return

        tag = data.get("tag_name", "").strip()
        latest_ver = parse_version(tag)
        curr_ver = parse_version(self.current_version)
        is_newer = latest_ver > curr_ver

        assets = data.get("assets", [])
        download_url = data.get("html_url", f"https://github.com/{self.repo}/releases")
        for a in assets:
            name = a.get("name", "").lower()
            if name.endswith(".zip") or name.endswith(".exe"):
                download_url = a.get("browser_download_url", download_url)
                break

        self.finished.emit({
            "is_newer": is_newer,
            "tag_name": tag,
            "name": data.get("name", tag),
            "body": data.get("body", ""),
            "html_url": data.get("html_url", f"https://github.com/{self.repo}/releases"),
            "download_url": download_url,
            "current_version": self.current_version
        })


class UpdatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        if hasattr(Qt, 'WindowContextHelpButtonHint'):
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowTitle(tr('dlg_updates_title'))
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(540, 400)
        self.setMinimumSize(500, 360)

        self._worker = None
        self._download_url = DEFAULT_REPO_URL + "/releases"

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header info
        h_ver = QHBoxLayout()
        lbl_cur = QLabel(f"Current version: v{CURRENT_VERSION}")
        lbl_cur.setStyleSheet("color: #8E8EA0; font-weight: bold;")
        h_ver.addWidget(lbl_cur)
        h_ver.addStretch()
        layout.addLayout(h_ver)

        self.repo = "furrykit/FKVideoPlayer"

        self.lbl_status = QLabel(tr('updates_checking'))
        self.lbl_status.setStyleSheet("color: #007AFF; font-weight: bold; font-size: 14px;")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)

        # Changelog area
        self.txt_notes = QTextEdit()
        self.txt_notes.setReadOnly(True)
        self.txt_notes.setPlaceholderText("Release notes will appear here...")
        self.txt_notes.setStyleSheet("background-color: #121218; border: 1px solid #282836; border-radius: 6px; padding: 6px; color: #D0D0E0;")
        layout.addWidget(self.txt_notes)

        h_btn = QHBoxLayout()
        self.btn_check = QPushButton(tr('btn_check_now'))
        self.btn_check.clicked.connect(self._check_updates)
        h_btn.addWidget(self.btn_check)

        self.btn_dl = QPushButton(tr('btn_download_update'))
        self.btn_dl.setObjectName("PrimaryBtn")
        self.btn_dl.setEnabled(False)
        self.btn_dl.clicked.connect(self._on_download_clicked)
        h_btn.addWidget(self.btn_dl)
        layout.addLayout(h_btn)

        # Auto-trigger check on opening
        self._check_updates()

    def _check_updates(self):
        self.lbl_status.setText(tr('updates_checking'))
        self.lbl_status.setStyleSheet("color: #007AFF; font-weight: bold; font-size: 14px;")
        self.btn_check.setEnabled(False)
        self.btn_dl.setEnabled(False)
        self.txt_notes.clear()

        repo = getattr(self, "repo", "furrykit/FKVideoPlayer")

        self._worker = GitHubUpdateCheckerWorker(repo=repo, current_version=CURRENT_VERSION, parent=self)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.error.connect(self._on_check_error)
        self._worker.start()

    def _on_check_finished(self, data: dict):
        self.btn_check.setEnabled(True)
        is_newer = data.get("is_newer", False)
        tag = data.get("tag_name", "")
        self._download_url = data.get("download_url") or data.get("html_url") or DEFAULT_REPO_URL + "/releases"

        if is_newer:
            self.lbl_status.setText(f"{tr('updates_available')} (v{tag})")
            self.lbl_status.setStyleSheet("color: #34C759; font-weight: bold; font-size: 14px;")
            self.btn_dl.setEnabled(True)
            body = data.get("body", "").strip() or f"New release {tag} is ready for download."
            self.txt_notes.setPlainText(body)
        else:
            self.lbl_status.setText(f"{tr('updates_up_to_date')} (v{CURRENT_VERSION})")
            self.lbl_status.setStyleSheet("color: #8E8EA0; font-weight: bold; font-size: 14px;")
            self.btn_dl.setEnabled(False)
            self.txt_notes.setPlainText(f"FKVideoPlayer is up to date (version {CURRENT_VERSION}).")

    def _on_check_error(self, err_msg: str):
        self.btn_check.setEnabled(True)
        self.lbl_status.setText("Could not reach update server. Check your connection.")
        self.lbl_status.setStyleSheet("color: #FF3B30; font-weight: bold; font-size: 13px;")
        self.txt_notes.setPlainText(f"Error details: {err_msg}")

    def _on_download_clicked(self):
        if self._download_url:
            safe_open_url(self._download_url)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            if not self._worker.wait(300):
                self._worker.terminate()
                self._worker.wait(300)
        super().closeEvent(event)

    def __del__(self):
        try:
            if hasattr(self, '_worker') and self._worker and self._worker.isRunning():
                self._worker.quit()
                if not self._worker.wait(300):
                    self._worker.terminate()
                    self._worker.wait(300)
        except Exception:
            pass


