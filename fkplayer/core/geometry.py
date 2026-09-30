#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Geometry, path calculations, system helpers, and ffmpeg path resolution.
"""

import os
import sys
import math
from PyQt5.QtCore import QPointF

APP_VERSION = "1.1.4"


def set_dark_titlebar(window):
    """Enable native dark titlebar and caption color on Windows 10/11"""
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


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller bundle."""
    base_path = getattr(sys, '_MEIPASS', None)
    if base_path:
        cand = os.path.join(base_path, relative_path)
        if os.path.exists(cand):
            return cand
        cand = os.path.join(base_path, "assets", "branding", os.path.basename(relative_path))
        if os.path.exists(cand):
            return cand
        return os.path.join(base_path, relative_path)

    # Dev mode: look in project root assets/branding first
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(os.path.dirname(cur_dir)) if os.path.basename(os.path.dirname(cur_dir)) == 'fkplayer' or os.path.basename(cur_dir) == 'core' else cur_dir
    branding_cand = os.path.join(root_dir, "assets", "branding", os.path.basename(relative_path))
    if os.path.exists(branding_cand):
        return branding_cand
    direct_cand = os.path.join(root_dir, relative_path)
    if os.path.exists(direct_cand):
        return direct_cand
    # Fallback checking parent
    parent_cand = os.path.join(os.path.dirname(cur_dir), relative_path)
    if os.path.exists(parent_cand):
        return parent_cand
    return os.path.join(cur_dir, relative_path)


def get_ffmpeg_path():
    """Finds ffmpeg executable: bundled in PyInstaller MEIPASS, in app directory, in PATH, or system fallback."""
    import shutil
    candidates = []
    # 1. Bundled in PyInstaller _MEIPASS
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, 'ffmpeg.exe'))
        candidates.append(os.path.join(sys._MEIPASS, 'ffmpeg'))
    # 2. Next to executable or current file
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(app_dir, 'ffmpeg.exe'))
        candidates.append(os.path.join(app_dir, 'ffmpeg'))
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(app_dir, 'ffmpeg.exe'))
        candidates.append(os.path.join(app_dir, 'ffmpeg'))
        root_dir = os.path.dirname(os.path.dirname(app_dir))
        candidates.append(os.path.join(root_dir, 'ffmpeg.exe'))
        candidates.append(os.path.join(root_dir, 'ffmpeg'))
    # 3. In PATH
    which_path = shutil.which("ffmpeg")
    if which_path:
        candidates.append(which_path)
    # 4. Standard Windows locations
    candidates.extend([
        r"C:\ffmpeg\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\ffmpeg.exe",
    ])
    for cand in candidates:
        if cand and os.path.exists(cand) and os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


def dist_to_segment_sq(p: QPointF, a: QPointF, b: QPointF) -> float:
    ab_x = b.x() - a.x()
    ab_y = b.y() - a.y()
    l2 = ab_x * ab_x + ab_y * ab_y
    if l2 < 1e-6:
        dx = p.x() - a.x()
        dy = p.y() - a.y()
        return dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p.x() - a.x()) * ab_x + (p.y() - a.y()) * ab_y) / l2))
    proj_x = a.x() + t * ab_x
    proj_y = a.y() + t * ab_y
    dx = p.x() - proj_x
    dy = p.y() - proj_y
    return dx * dx + dy * dy


def simplify_points(pts: list, tol: float = 0.8) -> list:
    """Ramer-Douglas-Peucker-like fast polyline simplifier to keep point counts minimal"""
    if len(pts) <= 2:
        return pts
    result = [pts[0]]
    tol2 = tol * tol
    for i in range(1, len(pts) - 1):
        prev = result[-1]
        cur = pts[i]
        nxt = pts[i + 1]
        dx = nxt.x() - prev.x()
        dy = nxt.y() - prev.y()
        l2 = dx * dx + dy * dy
        if l2 < 1e-6:
            continue
        t = max(0.0, min(1.0, ((cur.x() - prev.x()) * dx + (cur.y() - prev.y()) * dy) / l2))
        px = prev.x() + t * dx
        py = prev.y() + t * dy
        dist2 = (cur.x() - px) ** 2 + (cur.y() - py) ** 2
        if dist2 > tol2:
            result.append(cur)
    result.append(pts[-1])
    return result


def erase_stroke_subsegments(pts: list, center: QPointF, radius: float, stroke_width: float) -> list:
    """
    Sub-segment stroke carving: like Paint/Photoshop, cuts out and erases
    only the portion of the stroke under the circular eraser tip rather than
    deleting the whole stroke.
    """
    eff_r = radius + (stroke_width * 0.5)
    r2 = eff_r * eff_r
    if not pts:
        return []
    if len(pts) == 1:
        dx = pts[0].x() - center.x()
        dy = pts[0].y() - center.y()
        if (dx * dx + dy * dy) <= r2:
            return []
        return [pts]

    dense_pts = [pts[0]]
    for i in range(len(pts) - 1):
        p1 = pts[i]
        p2 = pts[i + 1]
        dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        step = max(2.0, min(5.0, stroke_width * 0.5))
        n_steps = max(1, int(dist / step))
        for s in range(1, n_steps + 1):
            t = s / float(n_steps)
            dense_pts.append(QPointF(p1.x() + t * (p2.x() - p1.x()), p1.y() + t * (p2.y() - p1.y())))

    new_segments = []
    current_run = []
    for pt in dense_pts:
        dx = pt.x() - center.x()
        dy = pt.y() - center.y()
        if (dx * dx + dy * dy) > r2:
            current_run.append(pt)
        else:
            if current_run:
                if len(current_run) >= 2 or len(pts) == 1:
                    new_segments.append(simplify_points(current_run))
                current_run = []
    if current_run:
        if len(current_run) >= 2 or len(pts) == 1:
            new_segments.append(simplify_points(current_run))

    return new_segments
