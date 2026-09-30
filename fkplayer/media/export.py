#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video export background worker.
Composites recorded actions, strokes, overlays, and video frames into final MP4/MKV.
Optimized with hardware-accelerated NVENC/MF encoding, NVDEC decoding, and pipelined producer-consumer queue.
"""

import os
import subprocess
import collections
import queue
import threading
import cv2
import numpy as np
import av

from PyQt5.QtCore import QThread, pyqtSignal, QRectF, QPointF, Qt
from PyQt5.QtGui import QImage, QPainter, QColor, QPainterPath, QPen, QBrush

try:
    from fkplayer.ui.canvas import OverlayObject
    from fkplayer.core.geometry import get_ffmpeg_path
    from fkplayer.media.reader import FastVideoReader
    from fkplayer.core.logger import get_logger
except (ImportError, ValueError):
    from ..ui.canvas import OverlayObject
    from ..core.geometry import get_ffmpeg_path
    from .reader import FastVideoReader
    from ..core.logger import get_logger

logger = get_logger("FKVideoPlayer.Export")


def get_available_hw_encoders() -> set:
    """Return set of available hardware encoder names in PyAV/FFmpeg."""
    try:
        hw_names = {'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'hevc_qsv', 'h264_amf', 'hevc_amf'}
        return {c for c in av.codec.codecs_available if c in hw_names}
    except Exception:
        return set()


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


class ExportVideoWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, video_path, events, total_duration, output_path, fps=30.0, out_size=None,
                 codec='libx264', bitrate='10M', rate_control='vbr', audio_path=None, hw_accel=True):
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
        self.hw_accel = bool(hw_accel)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self._do_run()
        except Exception as e:
            logger.error(f"ExportVideoWorker run error: {e}", exc_info=True)
            self.finished.emit(False, f"Export error: {e}")

    def _do_run(self):
        if not self.events or self.total_duration <= 0.05:
            self.finished.emit(False, "Recording is empty or duration is too short.")
            return

        reader = None
        cap = None
        src_w, src_h = 1280, 720

        if self.video_path and os.path.exists(self.video_path):
            try:
                reader = FastVideoReader(self.video_path)
                if reader.width > 0 and reader.height > 0:
                    src_w = reader.width
                    src_h = reader.height
                else:
                    reader.release()
                    reader = None
            except Exception as e:
                logger.warning(f"FastVideoReader failed to open {self.video_path} for export: {e}")
                reader = None

            if reader is None:
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

        codec_name = self.codec.lower()
        b_val = parse_bitrate_to_bps(self.bitrate)
        is_cbr = (self.rate_control == 'cbr')
        avail_hw = get_available_hw_encoders() if self.hw_accel else set()

        codec_candidates = []
        if codec_name in ('x264', 'libx264', 'h264'):
            if self.hw_accel:
                if 'h264_nvenc' in avail_hw:
                    codec_candidates.append(('h264_nvenc', 'nvenc'))
                if 'h264_qsv' in avail_hw:
                    codec_candidates.append(('h264_qsv', 'qsv'))
                if 'h264_amf' in avail_hw:
                    codec_candidates.append(('h264_amf', 'amf'))
            codec_candidates.append(('libx264', 'software'))
            codec_candidates.append(('h264', 'software'))
        elif codec_name in ('hevc', 'x265', 'libx265', 'h265'):
            if self.hw_accel:
                if 'hevc_nvenc' in avail_hw:
                    codec_candidates.append(('hevc_nvenc', 'nvenc'))
                if 'hevc_qsv' in avail_hw:
                    codec_candidates.append(('hevc_qsv', 'qsv'))
                if 'hevc_amf' in avail_hw:
                    codec_candidates.append(('hevc_amf', 'amf'))
            codec_candidates.append(('libx265', 'software'))
            codec_candidates.append(('hevc', 'software'))
        elif codec_name in ('vp9', 'libvpx-vp9'):
            codec_candidates.append(('libvpx-vp9', 'software'))
            codec_candidates.append(('vp9', 'software'))
        elif codec_name in ('prores', 'prores_ks'):
            codec_candidates.append(('prores_ks', 'software'))
            codec_candidates.append(('prores', 'software'))
        elif codec_name in ('mpeg4',):
            codec_candidates.append(('mpeg4', 'software'))
        else:
            codec_candidates.append(('libx264', 'software'))
            codec_candidates.append(('h264', 'software'))

        selected_codec = None
        for c_candidate, c_type in codec_candidates:
            try:
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        pass
                    container = None
                container = av.open(self.output_path, mode='w')
                stream = container.add_stream(c_candidate, rate=int(round(self.fps)))
                stream.width = out_w
                stream.height = out_h
                stream.pix_fmt = 'yuv420p'
                stream.bit_rate = b_val

                if c_type == 'nvenc':
                    if is_cbr:
                        stream.options = {
                            'preset': 'p4',
                            'b': str(b_val),
                            'minrate': str(b_val),
                            'maxrate': str(b_val),
                            'bufsize': str(b_val * 2)
                        }
                    else:
                        stream.options = {
                            'preset': 'p4',
                            'cq': '22' if 'h264' in c_candidate else '24',
                            'b': str(b_val),
                            'maxrate': str(int(b_val * 1.5)),
                            'bufsize': str(b_val * 2)
                        }
                elif c_type == 'qsv':
                    stream.options = {'preset': 'veryfast', 'b': str(b_val)}
                elif c_candidate in ('h264', 'libx264'):
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
                elif c_candidate in ('hevc', 'libx265'):
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
                elif c_candidate in ('vp9', 'libvpx-vp9'):
                    if is_cbr:
                        stream.options = {'b': str(b_val), 'minrate': str(b_val), 'maxrate': str(b_val)}
                    else:
                        stream.options = {'crf': '28', 'b': str(b_val)}
                elif 'prores' in c_candidate:
                    stream.options = {'profile': '3'}
                    stream.pix_fmt = 'yuv422p10le'

                selected_codec = c_candidate
                use_pyav = True
                logger.info(f"ExportVideoWorker: Initialized PyAV encoder '{c_candidate}' ({c_type}, {out_w}x{out_h} @ {self.fps:.2f} FPS)")
                break
            except Exception as enc_err:
                logger.debug(f"ExportVideoWorker: Codec candidate '{c_candidate}' failed: {enc_err}")
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        pass
                    container = None
                stream = None

        if not use_pyav or container is None or stream is None:
            use_pyav = False
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            cv_writer = cv2.VideoWriter(self.output_path, fourcc, self.fps, (out_w, out_h))
            if not cv_writer.isOpened():
                if reader:
                    reader.release()
                if cap:
                    cap.release()
                self.finished.emit(False, "Failed to initialize video codec for export.")
                return

        # Setup Asynchronous Producer-Consumer Pipeline
        frame_queue = queue.Queue(maxsize=16)
        encoder_error = None

        def encoder_worker():
            nonlocal encoder_error
            while True:
                item = frame_queue.get()
                if item is None:
                    break
                fmt, frame_data = item
                try:
                    if use_pyav and container is not None and stream is not None:
                        if fmt == 'bgra':
                            av_frame = av.VideoFrame.from_ndarray(frame_data, format='bgra')
                        else:
                            av_frame = av.VideoFrame.from_ndarray(frame_data, format='bgr24')
                        for packet in stream.encode(av_frame):
                            container.mux(packet)
                    elif cv_writer is not None:
                        if fmt == 'bgra':
                            bgr = cv2.cvtColor(frame_data, cv2.COLOR_BGRA2BGR)
                            cv_writer.write(bgr)
                        else:
                            cv_writer.write(frame_data)
                except Exception as err:
                    if encoder_error is None:
                        encoder_error = err
                        logger.error(f"Encoder thread error: {err}")
                finally:
                    frame_queue.task_done()

        encoder_thread = threading.Thread(target=encoder_worker, daemon=True)
        encoder_thread.start()

        total_frames = max(1, int(round(self.total_duration * self.fps)))
        event_idx = 0
        total_events = len(self.events)

        active_strokes = collections.OrderedDict()
        active_overlays = collections.OrderedDict()
        current_frame_idx = 0
        cached_bgr_frame = None
        cached_frame_idx = -1
        last_cap_pos = -1

        # Double/Triple buffer pool to avoid memory churn and race conditions
        img_pool = [QImage(out_w, out_h, QImage.Format_RGB32) for _ in range(4)]
        pool_idx = 0

        for frame_num in range(total_frames):
            if self._is_cancelled or encoder_error is not None:
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

            if reader is not None:
                if current_frame_idx != cached_frame_idx:
                    if last_cap_pos != current_frame_idx:
                        reader.seek(current_frame_idx, exact=True)
                    ret, read_frame = reader.read()
                    if ret and read_frame is not None:
                        cached_bgr_frame = read_frame
                        cached_frame_idx = current_frame_idx
                        last_cap_pos = current_frame_idx + 1
            elif cap is not None:
                if current_frame_idx != cached_frame_idx:
                    if last_cap_pos != current_frame_idx:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame_idx)
                    ret, read_frame = cap.read()
                    if ret and read_frame is not None:
                        cached_bgr_frame = read_frame
                        cached_frame_idx = current_frame_idx
                        last_cap_pos = current_frame_idx + 1

            has_overlays = bool(active_overlays)
            has_strokes = bool(active_strokes)

            if not has_overlays and not has_strokes and cached_bgr_frame is not None:
                # FAST PATH: Zero-copy bypass when no drawings or overlays are present
                h_f, w_f = cached_bgr_frame.shape[:2]
                if w_f == out_w and h_f == out_h:
                    frame_queue.put(('bgr', cached_bgr_frame.copy()))
                else:
                    resized = cv2.resize(cached_bgr_frame, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
                    frame_queue.put(('bgr', resized))
            else:
                # COMPOSITING PATH: Render via persistent ring-buffer QImage
                render_img = img_pool[pool_idx]
                pool_idx = (pool_idx + 1) % len(img_pool)

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
                bgra = raw[:, :out_w * 4].reshape((out_h, out_w, 4)).copy()
                frame_queue.put(('bgra', bgra))

            if frame_num % 10 == 0 or frame_num == total_frames - 1:
                self.progress.emit(frame_num + 1, total_frames, f"Exporting: {frame_num + 1}/{total_frames} frames")

        # Shutdown Producer-Consumer Pipeline
        if self._is_cancelled:
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                    frame_queue.task_done()
                except Exception:
                    break

        frame_queue.put(None)
        encoder_thread.join(timeout=10.0)

        for ov in active_overlays.values():
            try:
                ov.close()
            except Exception:
                pass
        active_overlays.clear()

        if reader is not None:
            try:
                reader.release()
            except Exception:
                pass

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

        if use_pyav and container is not None and stream is not None:
            if not self._is_cancelled and encoder_error is None:
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

        if encoder_error is not None:
            raise encoder_error

        # Audio commentary muxing via ffmpeg
        if not self._is_cancelled and self.audio_path and os.path.exists(self.audio_path) and os.path.getsize(self.audio_path) > 100:
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
