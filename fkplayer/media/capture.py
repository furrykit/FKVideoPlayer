#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
High-performance Windows window capture engine for FKVideoPlayer:
- Enumerates interactive top-level windows (browsers, Twitch, YouTube, Discord, games, etc.)
- Captures frames via Win32 PrintWindow (PW_RENDERFULLCONTENT) with BitBlt fallback
- Background QThread frame grabber delivering smooth 30/60 FPS frames into player canvas
"""

import ctypes
from ctypes import wintypes
import os
import sys
import numpy as np
from PIL import Image

try:
    import win32gui
    import win32ui
    import win32con
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD)
    ]

from PyQt5.QtCore import QObject, QThread, pyqtSignal, QMutex, QWaitCondition

def ensure_interactive_station():
    """Ensure current thread is attached to interactive WinSta0\\Default"""
    try:
        user32 = ctypes.windll.user32
        hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x0000037F)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
        hdesk = user32.OpenDesktopW('Default', 0, False, 0x000001FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            return True
    except Exception:
        pass
    return False

def list_open_windows(filter_self_pid=None):
    """
    Returns list of dicts:
    [{'hwnd': hwnd, 'title': title, 'rect': (x, y, w, h), 'pid': pid}, ...]
    """
    ensure_interactive_station()
    results = []
    if not HAS_WIN32:
        return results

    user32 = ctypes.windll.user32
    cur_pid = filter_self_pid or os.getpid()

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd): # minimized
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return True

        # Ignore desktop / shell windows
        clsname = win32gui.GetClassName(hwnd)
        if clsname in ('Progman', 'WorkerW', 'Shell_TrayWnd', 'Windows.UI.Core.CoreWindow'):
            return True

        # Check PID
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == cur_pid:
                return True
        except Exception:
            pid = 0

        # Check client or window rect
        try:
            r = win32gui.GetClientRect(hwnd)
            w = r[2] - r[0]
            h = r[3] - r[1]
            if w < 50 or h < 50:
                return True
            wr = win32gui.GetWindowRect(hwnd)
            results.append({
                'hwnd': hwnd,
                'title': title,
                'width': w,
                'height': h,
                'rect': (wr[0], wr[1], wr[2] - wr[0], wr[3] - wr[1]),
                'pid': pid
            })
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass

    if not results:
        try:
            hdesk = user32.OpenDesktopW('Default', 0, False, 0x000001FF)
            if hdesk:
                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
        except Exception:
            pass

    return results

def list_monitors():
    """
    Returns list of dicts for each physical monitor and virtual desktop:
    [{'id': 'monitor_0', 'type': 'monitor', 'title': '🖥️ Monitor 1 (1920x1080) [Primary]', 'rect': (0,0,1920,1080), ...}, ...]
    """
    ensure_interactive_station()
    results = []
    if not HAS_WIN32:
        return results

    user32 = ctypes.windll.user32
    monitors_raw = []

    def _mon_cb(hmon, hdc, lprect, lparam):
        try:
            r = lprect.contents
            monitors_raw.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        except Exception:
            pass
        return True

    try:
        MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
        user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_mon_cb), 0)
    except Exception:
        pass

    for idx, (mx, my, mw, mh) in enumerate(monitors_raw):
        is_primary = (mx == 0 and my == 0)
        tag = " [Primary]" if is_primary else ""
        results.append({
            'id': f"monitor_{idx}",
            'type': 'monitor',
            'index': idx,
            'title': f"🖥️ Monitor {idx + 1} ({mw}x{mh}){tag}",
            'width': mw,
            'height': mh,
            'rect': (mx, my, mw, mh),
            'hwnd': -(idx + 1)
        })

    if len(monitors_raw) > 1:
        try:
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            results.append({
                'id': 'monitor_all',
                'type': 'monitor',
                'index': -1,
                'title': f"🖥️ All Monitors / Virtual Desktop ({vw}x{vh})",
                'width': vw,
                'height': vh,
                'rect': (vx, vy, vw, vh),
                'hwnd': -999
            })
        except Exception:
            pass

    return results

def capture_screen_frame(rect=None) -> np.ndarray:
    """
    Captures screen or monitor region using fast Win32 GDI BitBlt.
    Returns BGR numpy array or None on failure.
    """
    if not HAS_WIN32:
        return None
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        if rect is None:
            x = user32.GetSystemMetrics(76)
            y = user32.GetSystemMetrics(77)
            w = user32.GetSystemMetrics(78)
            h = user32.GetSystemMetrics(79)
        else:
            x, y, w, h = rect

        if w <= 0 or h <= 0:
            return None

        hdesktop = user32.GetDesktopWindow()
        hdc_src = user32.GetWindowDC(hdesktop)
        if not hdc_src:
            return None

        hdc_mem = gdi32.CreateCompatibleDC(hdc_src)
        hbitmap = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
        old_bmp = gdi32.SelectObject(hdc_mem, hbitmap)

        SRCCOPY = 0x00CC0020
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_src, x, y, SRCCOPY)

        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = np.empty((h, w, 4), dtype=np.uint8)
        gdi32.GetDIBits(hdc_mem, hbitmap, 0, h, buf.ctypes.data_as(ctypes.c_void_p), ctypes.byref(bmi), 0)

        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hdesktop, hdc_src)

        return buf[:, :, :3].copy()
    except Exception:
        return None

def capture_window_frame(hwnd) -> np.ndarray:
    """
    Captures window frame by HWND and returns BGR numpy array (suitable for OpenCV/QImage).
    Returns None if capture fails.
    """
    if not HAS_WIN32:
        return None

    if not win32gui.IsWindow(hwnd):
        ensure_interactive_station()
        if not win32gui.IsWindow(hwnd):
            return None

    try:
        r = win32gui.GetClientRect(hwnd)
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w <= 0 or h <= 0:
            r = win32gui.GetWindowRect(hwnd)
            w = r[2] - r[0]
            h = r[3] - r[1]
        if w <= 0 or h <= 0:
            return None

        # Align width to multiple of 4 for bitmap stride
        hwndDC = None
        mfcDC = None
        saveDC = None
        saveBitMap = None
        try:
            hwndDC = win32gui.GetWindowDC(hwnd)
            mfcDC = win32ui.CreateDCFromHandle(hwndDC)
            saveDC = mfcDC.CreateCompatibleDC()
            saveBitMap = win32ui.CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
            saveDC.SelectObject(saveBitMap)

            # PW_RENDERFULLCONTENT = 2 captures DirectX/hardware accelerated browsers (Chrome, Edge, Firefox, Twitch)
            PW_RENDERFULLCONTENT = 2
            res = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
            if not res:
                res = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 0)
            if not res:
                # Fallback to direct BitBlt
                saveDC.BitBlt((0, 0), (w, h), mfcDC, (0, 0), win32con.SRCCOPY)

            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)

            # Convert buffer to numpy array BGRA -> BGR
            raw_w, raw_h = bmpinfo['bmWidth'], bmpinfo['bmHeight']
            img_np = np.frombuffer(bmpstr, dtype=np.uint8).reshape((raw_h, raw_w, 4))
            bgr = img_np[:, :, :3].copy()
            return bgr
        finally:
            # Cleanup GDI handles reliably in finally block to prevent memory/handle leaks
            if saveBitMap is not None:
                try:
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                except Exception:
                    pass
            if saveDC is not None:
                try:
                    saveDC.DeleteDC()
                except Exception:
                    pass
            if mfcDC is not None:
                try:
                    mfcDC.DeleteDC()
                except Exception:
                    pass
            if hwndDC is not None:
                try:
                    win32gui.ReleaseDC(hwnd, hwndDC)
                except Exception:
                    pass

    except Exception:
        return None

class WindowCaptureWorker(QThread):
    frame_captured = pyqtSignal(np.ndarray, float) # frame_bgr, timestamp
    stopped = pyqtSignal()

    def __init__(self, hwnd=None, monitor_rect=None, target_fps=30.0, parent=None):
        super().__init__(parent)
        self.hwnd = hwnd
        self.monitor_rect = monitor_rect
        self.is_monitor = (monitor_rect is not None) or (isinstance(hwnd, int) and hwnd < 0)
        self.target_fps = max(1.0, float(target_fps))
        self.is_running = True
        self.mutex = QMutex()

    def stop(self):
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.wait(1500)

    def run(self):
        import time
        try:
            ensure_interactive_station()
            interval = 1.0 / max(1.0, self.target_fps)
            t_start = time.perf_counter()

            while True:
                self.mutex.lock()
                running = self.is_running
                self.mutex.unlock()
                if not running:
                    break

                t0 = time.perf_counter()
                try:
                    if self.is_monitor:
                        frame = capture_screen_frame(self.monitor_rect)
                    else:
                        frame = capture_window_frame(self.hwnd)
                except Exception:
                    frame = None
                cur_time = time.perf_counter() - t_start

                if frame is not None:
                    self.frame_captured.emit(frame, cur_time)

                elapsed = time.perf_counter() - t0
                sleep_time = interval - elapsed
                if sleep_time > 0.001:
                    time.sleep(sleep_time)
        except Exception:
            pass
        finally:
            self.stopped.emit()
