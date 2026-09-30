#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video export background worker.
Composites recorded actions, strokes, overlays, and video frames into final MP4/MKV.
"""

import os
import subprocess
import collections
import cv2
import numpy as np
import av

from PyQt5.QtCore import QThread, pyqtSignal, QRectF, QPointF, Qt
from PyQt5.QtGui import QImage, QPainter, QColor, QPainterPath, QPen, QBrush

try:
    from fkplayer.ui.canvas import OverlayObject
    from fkplayer.core.geometry import get_ffmpeg_path
except (ImportError, ValueError):
    from ..ui.canvas import OverlayObject
    from ..core.geometry import get_ffmpeg_path


class ExportVideoWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, video_path, events, total_duration, output_path, fps=30.0, out_size=None,
                 codec='libx264', bitrate='10M', rate_control='vbr', audio_path=None):
        super().__init__()
        self.video_path = video_path
        self.events = events
        self.total_duration = total_duration
        self.output_path = output_path
        self.fps = max(10.0, min(120.0, fps))
        self.out_size = out_size
        self.codec = codec or 'libx264'
        self.bitrate = bitrate or '10M'
        self.rate_control = (rate_control or 'vbr').lower()
        self.audio_path = audio_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self._do_run()
        except Exception as e:
            self.finished.emit(False, f"Export error: {e}")

    def _do_run(self):
        if not self.events or self.total_duration <= 0.05:
            self.finished.emit(False, "Recording is empty or duration is too short.")
            return

        cap = None
        src_w, src_h = 1280, 720
        if self.video_path and os.path.exists(self.video_path):
            cap = cv2.VideoCapture(self.video_path)
            if cap.isOpened():
                src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            else:
                cap = None

        if src_w <= 0 or src_h <= 0:
            src_w, src_h = 1280, 720

        if self.out_size and len(self.out_size) == 2 and self.out_size[0] > 0 and self.out_size[1] > 0:
            out_w, out_h = self.out_size
        else:
            out_w, out_h = src_w, src_h

        out_w = (out_w // 2) * 2
        out_h = (out_h // 2) * 2

        use_pyav = True
        container = None
        stream = None
        cv_writer = None
        active_overlays = collections.OrderedDict()

        codec_name = self.codec.lower()
        if codec_name in ('x264', 'libx264', 'h264'):
            pyav_codec = 'h264'
        elif codec_name in ('hevc', 'x265', 'libx265', 'h265'):
            pyav_codec = 'hevc'
        elif codec_name in ('vp9', 'libvpx-vp9'):
            pyav_codec = 'vp9'
        elif codec_name in ('prores', 'prores_ks'):
            pyav_codec = 'prores'
        else:
            pyav_codec = 'h264'

        try:
            container = av.open(self.output_path, mode='w')
            stream = container.add_stream(pyav_codec, rate=int(round(self.fps)))
            stream.width = out_w
            stream.height = out_h
            stream.pix_fmt = 'yuv420p'
            def parse_bitrate_to_bps(b) -> int:
                s = str(b).strip().upper()
                if s.endswith('M'):
                    try:
                        return int(float(s[:-1]) * 1_000_000)
                    except Exception:
                        pass
                elif s.endswith('K'):
                    try:
                        return int(float(s[:-1]) * 1_000)
                    except Exception:
                        pass
                try:
                    return int(float(s))
                except Exception:
                    return 10_000_000

            b_val = parse_bitrate_to_bps(self.bitrate)
            is_cbr = (self.rate_control == 'cbr')

            if pyav_codec == 'h264':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'preset': 'veryfast',
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val),
                        'bufsize': str(b_val * 2)
                    }
                else:
                    stream.options = {
                        'preset': 'veryfast',
                        'crf': '20',
                        'maxrate': str(int(b_val * 1.5)),
                        'bufsize': str(b_val * 2)
                    }
            elif pyav_codec == 'hevc':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'preset': 'veryfast',
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val),
                        'bufsize': str(b_val * 2)
                    }
                else:
                    stream.options = {
                        'preset': 'veryfast',
                        'crf': '23',
                        'maxrate': str(int(b_val * 1.5)),
                        'bufsize': str(b_val * 2)
                    }
            elif pyav_codec == 'vp9':
                stream.bit_rate = b_val
                if is_cbr:
                    stream.options = {
                        'b': str(b_val),
                        'minrate': str(b_val),
                        'maxrate': str(b_val)
                    }
                else:
                    stream.options = {'crf': '28', 'b': str(b_val)}
            elif pyav_codec == 'prores':
                stream.options = {'profile': '3'}
                stream.pix_fmt = 'yuv422p10le'
            else:
                stream.bit_rate = b_val
        except Exception:
            use_pyav = False
            if container:
                try:
                    container.close()
                except Exception:
                    pass
                container = None
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            cv_writer = cv2.VideoWriter(self.output_path, fourcc, self.fps, (out_w, out_h))
            if not cv_writer.isOpened():
                if cap:
                    cap.release()
                self.finished.emit(False, "Failed to initialize video codec for export.")
                return

        total_frames = max(1, int(round(self.total_duration * self.fps)))
        event_idx = 0
        total_events = len(self.events)

        active_strokes = collections.OrderedDict()
        active_overlays = collections.OrderedDict()
        current_frame_idx = 0
        cached_bgr_frame = None
        cached_frame_idx = -1
        last_cap_pos = -1

        for frame_num in range(total_frames):
            if self._is_cancelled:
                break

            t = frame_num / float(self.fps)

            while event_idx < total_events and self.events[event_idx]['time'] <= t:
                ev = self.events[event_idx]
                etype = ev['type']
                if etype == 'frame':
                    current_frame_idx = ev['frame_idx']
                elif etype == 'stroke_start':
                    sid = ev['stroke_id']
                    col = QColor(ev['color'])
                    w = ev['width']
                    pt = QPointF(ev['pt'][0], ev['pt'][1])
                    p = QPainterPath()
                    p.moveTo(pt)
                    active_strokes[sid] = {
                        'color': col,
                        'width': w,
                        'points': [pt],
                        'path': p
                    }
                elif etype == 'stroke_point':
                    sid = ev['stroke_id']
                    if sid in active_strokes:
                        pt = QPointF(ev['pt'][0], ev['pt'][1])
                        active_strokes[sid]['points'].append(pt)
                        active_strokes[sid]['path'].lineTo(pt)
                elif etype == 'undo':
                    if active_strokes:
                        active_strokes.popitem(last=True)
                elif etype == 'clear':
                    active_strokes.clear()
                elif etype == 'erase':
                    for sid in ev.get('stroke_ids', []):
                        active_strokes.pop(sid, None)
                elif etype == 'overlay_add':
                    oid = ev['obj_id']
                    fpath = ev.get('file_path', '')
                    r = QRectF(*ev['rect'])
                    t_data = ev.get('text_data')
                    active_overlays[oid] = OverlayObject(oid, fpath, r, start_time=ev['time'], text_data=t_data)
                elif etype == 'overlay_transform':
                    oid = ev['obj_id']
                    if oid in active_overlays:
                        active_overlays[oid].rect = QRectF(*ev['rect'])
                        if 'rotation' in ev:
                            active_overlays[oid].rotation = float(ev['rotation'])
                elif etype == 'overlay_remove':
                    oid = ev['obj_id']
                    ov = active_overlays.pop(oid, None)
                    if ov:
                        ov.close()
                event_idx += 1

            if cap is not None:
                if current_frame_idx != cached_frame_idx:
                    if last_cap_pos != current_frame_idx:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame_idx)
                    ret, read_frame = cap.read()
                    if ret:
                        cached_bgr_frame = read_frame
                        cached_frame_idx = current_frame_idx
                        last_cap_pos = current_frame_idx + 1

            render_img = QImage(out_w, out_h, QImage.Format_RGB32)
            painter = QPainter(render_img)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            if cached_bgr_frame is not None:
                h_f, w_f, ch_f = cached_bgr_frame.shape
                q_video = QImage(cached_bgr_frame.data, w_f, h_f, ch_f * w_f, QImage.Format_BGR888)
                painter.drawImage(QRectF(0, 0, out_w, out_h), q_video)
            else:
                painter.fillRect(0, 0, out_w, out_h, QColor("#121216"))

            sx = out_w / float(src_w)
            sy = out_h / float(src_h)
            painter.save()
            painter.scale(sx, sy)

            for ov in active_overlays.values():
                ov_img = ov.get_frame_at_time(t)
                if ov_img and not ov_img.isNull():
                    painter.save()
                    rot = getattr(ov, 'rotation', 0.0)
                    if rot != 0.0:
                        center = ov.rect.center()
                        painter.translate(center)
                        painter.rotate(rot)
                        painter.translate(-center)
                    if hasattr(ov, 'opacity') and ov.opacity < 1.0:
                        painter.setOpacity(ov.opacity)
                    painter.drawImage(ov.rect, ov_img)
                    painter.restore()

            for s in active_strokes.values():
                pts = s['points']
                if not pts:
                    continue
                pen = QPen(s['color'], s['width'], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(pen)
                if len(pts) == 1:
                    painter.setBrush(QBrush(s['color']))
                    r = s['width'] / 2.0
                    painter.drawEllipse(pts[0], r, r)
                else:
                    painter.setBrush(Qt.NoBrush)
                    painter.drawPath(s['path'])

            painter.restore()
            painter.end()

            bpl = render_img.bytesPerLine()
            ptr = render_img.constBits()
            ptr.setsize(bpl * out_h)
            raw = np.frombuffer(ptr, np.uint8).reshape((out_h, bpl))
            rgba = raw[:, :out_w * 4].reshape((out_h, out_w, 4))
            bgr_out = cv2.cvtColor(rgba, cv2.COLOR_BGRA2BGR)

            if use_pyav and container is not None and stream is not None:
                try:
                    av_frame = av.VideoFrame.from_ndarray(bgr_out, format='bgr24')
                    for packet in stream.encode(av_frame):
                        container.mux(packet)
                except Exception:
                    use_pyav = False
                    if container:
                        try:
                            container.close()
                        except Exception:
                            pass
                        container = None
                    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                    cv_writer = cv2.VideoWriter(self.output_path, fourcc, self.fps, (out_w, out_h))
                    if cv_writer.isOpened():
                        cv_writer.write(bgr_out)
            elif cv_writer is not None:
                cv_writer.write(bgr_out)

            if frame_num % 10 == 0 or frame_num == total_frames - 1:
                self.progress.emit(frame_num + 1, total_frames, f"Exporting: {frame_num + 1}/{total_frames} frames")

        for ov in active_overlays.values():
            try:
                ov.close()
            except Exception:
                pass
        active_overlays.clear()

        if cap:
            try:
                cap.release()
            except Exception:
                pass

        if use_pyav and container is not None and stream is not None:
            if not self._is_cancelled:
                try:
                    for packet in stream.encode(None):
                        container.mux(packet)
                except Exception:
                    pass
            try:
                container.close()
            except Exception:
                pass
            container = None
        elif cv_writer is not None:
            try:
                cv_writer.release()
            except Exception:
                pass
            cv_writer = None

        # Audio commentary muxing via ffmpeg
        if not self._is_cancelled and self.audio_path and os.path.exists(self.audio_path) and os.path.getsize(self.audio_path) > 100:
            import subprocess
            ffmpeg_exe = get_ffmpeg_path()
            if ffmpeg_exe and os.path.exists(ffmpeg_exe):
                temp_mux = self.output_path + ".muxed" + os.path.splitext(self.output_path)[1]
                cmd = [
                    ffmpeg_exe, "-y",
                    "-i", self.output_path,
                    "-i", self.audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-shortest",
                    temp_mux
                ]
                flags = 0x08000000 if os.name == 'nt' else 0
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
                if res.returncode == 0 and os.path.exists(temp_mux) and os.path.getsize(temp_mux) > 1000:
                    try:
                        os.replace(temp_mux, self.output_path)
                    except Exception:
                        pass

        if self._is_cancelled:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass
            self.finished.emit(False, "Export cancelled.")
        else:
            self.finished.emit(True, self.output_path)
