#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Action recording subsystem for FKVideoPlayer.
Captures user drawing strokes, zooming, panning, overlays, and timeline events.
"""

import json
import time

try:
    from fkplayer.ui.canvas import OverlayObject
except (ImportError, ValueError):
    from ..ui.canvas import OverlayObject


class ActionRecorder:
    STATE_IDLE = 0
    STATE_RECORDING = 1
    STATE_PAUSED = 2

    def __init__(self, player):
        self.player = player
        self.state = self.STATE_IDLE
        self.events = []
        self.elapsed_time = 0.0
        self.last_resume_time = 0.0
        self.last_recorded_frame = -1
        self.last_zoom = -1.0
        self.last_pan = (None, None)
        self._stroke_counter = 0

    def allocate_stroke_id(self) -> int:
        self._stroke_counter += 1
        return self._stroke_counter

    def is_recording(self) -> bool:
        return self.state == self.STATE_RECORDING

    def is_paused(self) -> bool:
        return self.state == self.STATE_PAUSED

    def is_active(self) -> bool:
        return self.state in (self.STATE_RECORDING, self.STATE_PAUSED)

    def current_time(self) -> float:
        if self.state == self.STATE_RECORDING:
            return round(self.elapsed_time + (time.perf_counter() - self.last_resume_time), 4)
        return round(self.elapsed_time, 4)

    def start(self):
        self.events.clear()
        self.elapsed_time = 0.0
        self.last_resume_time = time.perf_counter()
        self.state = self.STATE_RECORDING
        self._stroke_counter = 0
        self.last_recorded_frame = -1
        self.last_zoom = -1.0
        self.last_pan = (None, None)

        self.record_frame(force=True)

        if hasattr(self.player, 'canvas') and self.player.canvas.strokes:
            for s in self.player.canvas.strokes:
                sid = self.allocate_stroke_id()
                s.stroke_id = sid
                if s.points:
                    self.events.append({
                        'type': 'stroke_start',
                        'time': 0.0,
                        'stroke_id': sid,
                        'color': s.color.name(),
                        'width': s.width,
                        'pt': (round(s.points[0].x(), 2), round(s.points[0].y(), 2))
                    })
                    for pt in s.points[1:]:
                        self.events.append({
                            'type': 'stroke_point',
                            'time': 0.0,
                            'stroke_id': sid,
                            'pt': (round(pt.x(), 2), round(pt.y(), 2))
                        })
                    self.events.append({
                        'type': 'stroke_end',
                        'time': 0.0,
                        'stroke_id': sid
                    })

        if hasattr(self.player, 'canvas') and self.player.canvas.overlays:
            for ov in self.player.canvas.overlays:
                self.events.append({
                    'type': 'overlay_add',
                    'time': 0.0,
                    'obj_id': ov.obj_id,
                    'file_path': ov.file_path,
                    'rect': [round(ov.rect.x(), 2), round(ov.rect.y(), 2),
                             round(ov.rect.width(), 2), round(ov.rect.height(), 2)]
                })

    def pause(self):
        if self.state == self.STATE_RECORDING:
            self.elapsed_time += (time.perf_counter() - self.last_resume_time)
            self.state = self.STATE_PAUSED
        elif self.state == self.STATE_PAUSED:
            self.last_resume_time = time.perf_counter()
            self.state = self.STATE_RECORDING
            self.record_frame(force=True)

    def stop(self):
        if self.state == self.STATE_RECORDING:
            self.elapsed_time += (time.perf_counter() - self.last_resume_time)
        self.state = self.STATE_IDLE
        self.events.append({
            'type': 'stop',
            'time': round(self.elapsed_time, 4)
        })

    def record_frame(self, force=False):
        if self.state != self.STATE_RECORDING:
            return
        t = self.current_time()
        f_idx = self.player.current_frame_idx
        zoom = self.player.canvas.zoom_factor if hasattr(self.player, 'canvas') else 1.0
        pan = (
            self.player.canvas.pan_offset.x() if hasattr(self.player, 'canvas') else 0.0,
            self.player.canvas.pan_offset.y() if hasattr(self.player, 'canvas') else 0.0
        )
        if not force and f_idx == self.last_recorded_frame and abs(zoom - self.last_zoom) < 1e-3 and pan == self.last_pan:
            return

        self.last_recorded_frame = f_idx
        self.last_zoom = zoom
        self.last_pan = pan
        self.events.append({
            'type': 'frame',
            'time': t,
            'frame_idx': f_idx,
            'zoom': round(zoom, 4),
            'pan': (round(pan[0], 2), round(pan[1], 2))
        })

    def record_stroke_start(self, stroke_id: int, color_hex: str, width: float, pt: tuple):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_start',
            'time': self.current_time(),
            'stroke_id': stroke_id,
            'color': color_hex,
            'width': round(width, 2),
            'pt': (round(pt[0], 2), round(pt[1], 2))
        })

    def record_stroke_point(self, stroke_id: int, pt: tuple):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_point',
            'time': self.current_time(),
            'stroke_id': stroke_id,
            'pt': (round(pt[0], 2), round(pt[1], 2))
        })

    def record_stroke_end(self, stroke_id: int):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'stroke_end',
            'time': self.current_time(),
            'stroke_id': stroke_id
        })

    def record_undo(self):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'undo',
            'time': self.current_time()
        })

    def record_clear(self):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'clear',
            'time': self.current_time()
        })

    def record_erase(self, stroke_ids: list):
        if self.state != self.STATE_RECORDING or not stroke_ids:
            return
        self.events.append({
            'type': 'erase',
            'time': self.current_time(),
            'stroke_ids': list(stroke_ids)
        })

    def record_overlay_add(self, overlay):
        if self.state != self.STATE_RECORDING:
            return
        entry = {
            'type': 'overlay_add',
            'time': self.current_time(),
            'obj_id': overlay.obj_id,
            'obj_type': overlay.obj_type,
            'file_path': overlay.file_path,
            'rect': [round(overlay.rect.x(), 2), round(overlay.rect.y(), 2),
                     round(overlay.rect.width(), 2), round(overlay.rect.height(), 2)]
        }
        if overlay.obj_type == OverlayObject.TYPE_TEXT:
            entry['text_data'] = {
                'text': overlay.text,
                'font_family': overlay.font_family,
                'font_size': overlay.font_size,
                'bold': overlay.font_bold,
                'italic': overlay.font_italic,
                'color': overlay.text_color,
                'bg_color': overlay.bg_color
            }
        self.events.append(entry)

    def record_overlay_transform(self, overlay):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'overlay_transform',
            'time': self.current_time(),
            'obj_id': overlay.obj_id,
            'rect': [round(overlay.rect.x(), 2), round(overlay.rect.y(), 2),
                     round(overlay.rect.width(), 2), round(overlay.rect.height(), 2)]
        })

    def record_overlay_remove(self, obj_id: int):
        if self.state != self.STATE_RECORDING:
            return
        self.events.append({
            'type': 'overlay_remove',
            'time': self.current_time(),
            'obj_id': obj_id
        })

    def to_dict(self) -> dict:
        return {
            'video_path': getattr(self.player, 'video_path', ''),
            'video_fps': getattr(self.player, 'fps', 25.0),
            'total_frames': getattr(self.player, 'total_frames', 0),
            'total_duration': round(self.elapsed_time, 4),
            'event_count': len(self.events),
            'events': self.events
        }

    def save_json(self, path: str):
        data = self.to_dict()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_json(self, path: str):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.events = data.get('events', [])
        self.elapsed_time = float(data.get('total_duration', 0.0))
        self.state = self.STATE_IDLE
