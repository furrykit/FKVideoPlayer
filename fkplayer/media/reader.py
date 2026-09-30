#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
High-performance video reader for FKVideoPlayer.
Supports NVIDIA NVDEC (hevc_cuvid / h264_cuvid), Intel QSV,
and multi-threaded PyAV software decoding with automatic OpenCV fallback.
"""

import os
import av
import cv2
import numpy as np
from PyQt5.QtGui import QImage
from fkplayer.core.logger import get_logger

logger = get_logger("VideoReader")


class FastVideoReader:
    """Universal video stream decoder with GPU hardware acceleration (NVDEC/QSV) and zero-leak memory management."""

    def __init__(self, file_path):
        self.file_path = file_path
        self._is_hw = False
        self._is_pyav = False
        self.container = None
        self.stream = None
        self.hw_ctx = None
        self.cap = None
        self.width = 0
        self.height = 0
        self.fps = 25.0
        self.total_frames = 0
        self._cur_frame = 0
        self._demux_iter = None
        self._pending_frame = None
        self._preallocated_bgra = None

        try:
            self.container = av.open(file_path)
            if len(self.container.streams.video) > 0:
                self.stream = self.container.streams.video[0]
                self.fps = float(self.stream.average_rate or self.stream.guessed_rate or 25.0)
                if self.fps <= 1.0 or np.isnan(self.fps):
                    self.fps = 25.0

                self.total_frames = int(self.stream.frames or 0)
                if self.total_frames <= 0:
                    if self.stream.duration and self.stream.time_base:
                        dur = float(self.stream.duration * self.stream.time_base)
                        self.total_frames = max(1, int(dur * self.fps))
                    else:
                        self.total_frames = 1

                self.orig_width = int(self.stream.width)
                self.orig_height = int(self.stream.height)
                self.width = self.orig_width
                self.height = self.orig_height
                self._is_pyav = True

                # Determine if hardware downscaling is required for ultra-high resolution (> 4K)
                target_w = self.orig_width
                target_h = self.orig_height
                if self.orig_width > 3840 or self.orig_height > 2160:
                    scale = min(3840.0 / self.orig_width, 2160.0 / self.orig_height)
                    target_w = int(round(self.orig_width * scale)) & ~1
                    target_h = int(round(self.orig_height * scale)) & ~1

                # Attempt hardware acceleration for HEVC, H.264, VP9, and AV1
                codec_name = self.stream.codec_context.name
                hw_candidates = []
                if codec_name == 'hevc':
                    hw_candidates = ['hevc_cuvid', 'hevc_qsv']
                elif codec_name == 'h264':
                    hw_candidates = ['h264_cuvid', 'h264_qsv']
                elif codec_name == 'vp9':
                    hw_candidates = ['vp9_cuvid']
                elif codec_name == 'av1':
                    hw_candidates = ['av1_cuvid']

                for hw_name in hw_candidates:
                    try:
                        hw_codec = av.Codec(hw_name, 'r')
                        ctx = av.CodecContext.create(hw_codec)
                        if (target_w != self.orig_width or target_h != self.orig_height) and 'cuvid' in hw_name:
                            ctx.options = {'resize': f'{target_w}x{target_h}'}
                        if self.stream.codec_context.extradata:
                            ctx.extradata = self.stream.codec_context.extradata
                        ctx.open()
                        self.hw_ctx = ctx
                        self._is_hw = True
                        if target_w != self.orig_width or target_h != self.orig_height:
                            self.width = target_w
                            self.height = target_h
                            logger.info(f"FastVideoReader: Hardware acceleration + scaling enabled ({hw_name}) for {self.orig_width}x{self.orig_height} -> {self.width}x{self.height} @ {self.fps:.2f} FPS")
                        else:
                            logger.info(f"FastVideoReader: Hardware acceleration enabled ({hw_name}) for {self.width}x{self.height} @ {self.fps:.2f} FPS")
                        break
                    except Exception as hw_err:
                        logger.debug(f"Hardware codec {hw_name} not available: {hw_err}")

                if not self._is_hw:
                    # Software PyAV with thread clamping to avoid huge DPB overhead on high-core CPUs
                    self.stream.thread_type = 'AUTO'
                    max_threads = min(4, os.cpu_count() or 4)
                    self.stream.codec_context.thread_count = max_threads
                    logger.info(f"FastVideoReader: Using multi-threaded PyAV software engine ({max_threads} threads, {self.width}x{self.height} @ {self.fps:.2f} FPS)")

                if self.width > 0 and self.height > 0:
                    self._buffer_pool = [np.empty((self.height, self.width, 4), dtype=np.uint8) for _ in range(4)]
                    self._pool_idx = 0

                self._init_iter()
        except Exception as e:
            logger.warning(f"FastVideoReader: PyAV initialization failed for {file_path}, falling back to OpenCV: {e}")
            if self.container:
                try:
                    self.container.close()
                except Exception:
                    pass
                self.container = None
            self._is_pyav = False
            self._is_hw = False

        if not self._is_pyav:
            self.cap = cv2.VideoCapture(file_path)
            if self.cap.isOpened():
                self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.orig_width = self.width
                self.orig_height = self.height
                self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
                if self.fps <= 1.0 or np.isnan(self.fps):
                    self.fps = 25.0
                self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if self.width > 0 and self.height > 0:
                    self._buffer_pool = [np.empty((self.height, self.width, 4), dtype=np.uint8) for _ in range(4)]
                    self._pool_idx = 0
                logger.info(f"FastVideoReader: Opened with OpenCV backend ({self.width}x{self.height} @ {self.fps:.2f} FPS)")

    def _init_iter(self):
        if self._is_hw:
            self._demux_iter = self.container.demux(self.stream)
            self._pending_frame = None
        elif self._is_pyav and self.container and self.stream:
            self._demux_iter = self.container.decode(self.stream)
            self._pending_frame = None

    def isOpened(self) -> bool:
        if self._is_pyav:
            return self.container is not None
        return self.cap is not None and self.cap.isOpened()

    def _get_next_raw_frame(self):
        if self._pending_frame is not None:
            f = self._pending_frame
            self._pending_frame = None
            return f

        if self._is_hw:
            if self._demux_iter is None:
                return None
            try:
                for packet in self._demux_iter:
                    try:
                        frames = self.hw_ctx.decode(packet)
                    except (av.error.EOFError, av.FFmpegError):
                        return None
                    if frames:
                        return frames[0]
            except (av.error.EOFError, av.FFmpegError, StopIteration):
                return None
            return None
        elif self._is_pyav:
            if self._demux_iter is None:
                return None
            try:
                return next(self._demux_iter)
            except (av.error.EOFError, av.FFmpegError, StopIteration):
                return None
        return None

    def read(self):
        """Read frame as BGR numpy array (OpenCV standard)."""
        if self._is_pyav:
            try:
                frame = self._get_next_raw_frame()
                if frame is None:
                    return False, None
                self._cur_frame += 1
                if frame.format.name == 'nv12':
                    nv12 = frame.to_ndarray()
                    bgr = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
                    del nv12, frame
                    return True, bgr
                bgr = frame.to_ndarray(format='bgr24')
                del frame
                return True, bgr
            except (StopIteration, av.FFmpegError, Exception):
                return False, None
        else:
            if not self.cap or not self.cap.isOpened():
                return False, None
            return self.cap.read()

    def read_qimage(self):
        """High-performance frame decode returning zero-copy QImage directly from rotating ring buffer."""
        if self._is_pyav:
            try:
                frame = self._get_next_raw_frame()
                if frame is None:
                    return False, None
                self._cur_frame += 1

                if hasattr(self, '_buffer_pool') and self._buffer_pool:
                    buf = self._buffer_pool[self._pool_idx]
                    self._pool_idx = (self._pool_idx + 1) % len(self._buffer_pool)
                else:
                    buf = np.empty((self.height, self.width, 4), dtype=np.uint8)

                if frame.format.name == 'nv12':
                    nv12 = frame.to_ndarray()
                    cv2.cvtColor(nv12, cv2.COLOR_YUV2BGRA_NV12, dst=buf)
                    qimg = QImage(buf.data, self.width, self.height, self.width * 4, QImage.Format_RGB32)
                    qimg._buf_ref = buf
                    del nv12, frame
                    return True, qimg
                else:
                    bgra = frame.to_ndarray(format='bgra')
                    np.copyto(buf, bgra)
                    qimg = QImage(buf.data, self.width, self.height, self.width * 4, QImage.Format_RGB32)
                    qimg._buf_ref = buf
                    del bgra, frame
                    return True, qimg
            except (StopIteration, av.FFmpegError, Exception):
                return False, None
        else:
            if not self.cap or not self.cap.isOpened():
                return False, None
            ret, frame = self.cap.read()
            if not ret or frame is None:
                return False, None
            h, w = frame.shape[:2]
            if hasattr(self, '_buffer_pool') and self._buffer_pool:
                buf = self._buffer_pool[self._pool_idx]
                self._pool_idx = (self._pool_idx + 1) % len(self._buffer_pool)
                cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=buf)
                qimg = QImage(buf.data, w, h, w * 4, QImage.Format_RGB32)
                qimg._buf_ref = buf
            else:
                bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
                qimg = QImage(bgra.data, w, h, w * 4, QImage.Format_RGB32).copy()
                del bgra
            del frame
            return True, qimg

    def seek(self, target_f: int, exact: bool = True) -> bool:
        """Seek to frame. If exact=False, returns nearest keyframe in ~8ms for silky smooth scrubbing."""
        if self._is_pyav:
            target_f = max(0, min(self.total_frames - 1, int(target_f)))
            tb = self.stream.time_base
            target_pts = int((target_f / max(1.0, self.fps)) / tb)
            try:
                self.container.seek(target_pts, backward=True, stream=self.stream)
                if self._is_hw:
                    try:
                        self.hw_ctx.flush_buffers()
                    except Exception:
                        pass
                    self._demux_iter = self.container.demux(self.stream)
                    target_obj = None
                    try:
                        for packet in self._demux_iter:
                            try:
                                frames = self.hw_ctx.decode(packet)
                            except (av.error.EOFError, av.FFmpegError):
                                break
                            if frames:
                                if not exact:
                                    target_obj = frames[0]
                                    f_pts = frames[0].pts if frames[0].pts is not None else packet.pts
                                    cur_f = int(round(float(f_pts * tb) * self.fps)) if f_pts is not None else target_f
                                    self._cur_frame = cur_f
                                    break
                                for f in frames:
                                    cur_f = int(round(float(f.pts * tb) * self.fps)) if f.pts is not None else 0
                                    if cur_f >= target_f:
                                        target_obj = f
                                        break
                                if target_obj is not None:
                                    break
                    except (av.error.EOFError, av.FFmpegError, StopIteration):
                        pass

                    if target_obj is None:
                        try:
                            frames = self.hw_ctx.decode(None)
                            if frames:
                                target_obj = frames[-1]
                        except Exception:
                            pass

                    self._pending_frame = target_obj
                else:
                    try:
                        self.stream.codec_context.flush_buffers()
                    except Exception:
                        pass
                    self._demux_iter = self.container.decode(self.stream)
                    target_obj = None
                    try:
                        for f in self._demux_iter:
                            if not exact:
                                target_obj = f
                                f_pts = f.pts if f.pts is not None else target_pts
                                cur_f = int(round(float(f_pts * tb) * self.fps)) if f_pts is not None else target_f
                                self._cur_frame = cur_f
                                break
                            cur_f = int(round(float(f.pts * tb) * self.fps)) if f.pts is not None else 0
                            if cur_f >= target_f:
                                target_obj = f
                                break
                    except (av.error.EOFError, av.FFmpegError, StopIteration):
                        pass

                    self._pending_frame = target_obj

                if exact or target_obj is None:
                    self._cur_frame = target_f
                return True
            except av.error.EOFError:
                return True
            except Exception as e:
                logger.warning(f"FastVideoReader seek failed: {e}")
                return False
        else:
            if not self.cap or not self.cap.isOpened():
                return False
            return self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_f)

    def set(self, prop, val) -> bool:
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return self.seek(int(val), exact=True)
        if self._is_pyav:
            return False
        else:
            if not self.cap or not self.cap.isOpened():
                return False
            return self.cap.set(prop, val)

    def grab(self) -> bool:
        if self._is_pyav:
            try:
                if self._pending_frame is not None:
                    self._pending_frame = None
                elif self._is_hw:
                    if self._demux_iter is not None:
                        for packet in self._demux_iter:
                            try:
                                frames = self.hw_ctx.decode(packet)
                            except (av.error.EOFError, av.FFmpegError):
                                return False
                            if frames:
                                break
                else:
                    if self._demux_iter is not None:
                        next(self._demux_iter)
                self._cur_frame += 1
                return True
            except (av.error.EOFError, av.FFmpegError, StopIteration):
                return False
            except Exception:
                return False
        else:
            if not self.cap or not self.cap.isOpened():
                return False
            return self.cap.grab()

    def get(self, prop) -> float:
        if self._is_pyav:
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return float(self.total_frames)
            elif prop == cv2.CAP_PROP_FPS:
                return float(self.fps)
            elif prop == cv2.CAP_PROP_FRAME_WIDTH:
                return float(self.width)
            elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return float(self.height)
            elif prop == cv2.CAP_PROP_POS_FRAMES:
                return float(self._cur_frame)
            return 0.0
        else:
            if not self.cap or not self.cap.isOpened():
                return 0.0
            return self.cap.get(prop)

    def release(self):
        if self._is_pyav and self.container:
            try:
                self.container.close()
            except Exception:
                pass
            self.container = None
            self.stream = None
            self.hw_ctx = None
            self._demux_iter = None
            self._pending_frame = None
            self._buffer_pool = []
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self._buffer_pool = []
            self.cap = None
