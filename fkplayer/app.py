import sys
import os
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import QApplication


def resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller bundle."""
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    candidate = os.path.join(base_path, relative_path)
    if os.path.exists(candidate):
        return candidate
    # Fallback to assets directory
    assets_candidate = os.path.join(base_path, "assets", "branding", relative_path)
    if os.path.exists(assets_candidate):
        return assets_candidate
    return candidate


def set_dark_titlebar(window):
    """Enable native dark titlebar and caption color on Windows 10/11."""
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
