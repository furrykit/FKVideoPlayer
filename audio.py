#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Microphone audio commentary recording module for FKVideoPlayer:
- Lists all audio input devices on Windows via PyQt5.QtMultimedia
- Records pristine 44.1kHz 16-bit PCM audio alongside action recordings
- Packages recorded audio into standard WAV container ready for video muxing
- Provides live audio level metering for settings and testing
"""

import os
import struct
import tempfile
import threading
import wave
import numpy as np

from PyQt5.QtCore import QObject, QFile, QIODevice, pyqtSignal
from PyQt5.QtMultimedia import QAudioFormat, QAudioInput, QAudioDeviceInfo, QAudio

def get_audio_input_devices():
    """
    Returns list of dicts:
    [{'name': str, 'device_info': QAudioDeviceInfo, 'is_default': bool}, ...]
    """
    default_dev = QAudioDeviceInfo.defaultInputDevice()
    devices = []
    seen_names = set()

    all_devs = QAudioDeviceInfo.availableDevices(QAudio.AudioInput)
    for dev in all_devs:
        name = dev.deviceName().strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        devices.append({
            'name': name,
            'device_info': dev,
            'is_default': (name == default_dev.deviceName().strip())
        })

    if not devices and not default_dev.isNull():
        devices.append({
            'name': default_dev.deviceName(),
            'device_info': default_dev,
            'is_default': True
        })

    return devices

class MicrophoneRecorder(QObject):
    level_changed = pyqtSignal(float) # 0.0 to 1.0 audio level

    def __init__(self, sample_rate=44100, channels=2, parent=None):
        super().__init__(parent)
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_input = None
        self.temp_pcm_file = None
        self.temp_pcm_path = None
        self.final_wav_path = None
        self.is_recording = False
        self.device_name = None

    def start_recording(self, output_wav_path: str, device_name: str = None) -> bool:
        self.final_wav_path = output_wav_path
        self.device_name = device_name

        # Prepare audio format
        format = QAudioFormat()
        format.setSampleRate(self.sample_rate)
        format.setChannelCount(self.channels)
        format.setSampleSize(16)
        format.setCodec("audio/pcm")
        format.setByteOrder(QAudioFormat.LittleEndian)
        format.setSampleType(QAudioFormat.SignedInt)

        device_info = QAudioDeviceInfo.defaultInputDevice()
        if device_name:
            for d in QAudioDeviceInfo.availableDevices(QAudio.AudioInput):
                if d.deviceName().strip() == device_name.strip():
                    device_info = d
                    break

        if not device_info.isFormatSupported(format):
            format = device_info.nearestFormat(format)
            self.sample_rate = format.sampleRate()
            self.channels = format.channelCount()

        # Temporary raw PCM file
        fd, self.temp_pcm_path = tempfile.mkstemp(suffix='.pcm')
        os.close(fd)

        self.temp_pcm_file = QFile(self.temp_pcm_path)
        if not self.temp_pcm_file.open(QIODevice.WriteOnly | QIODevice.Truncate):
            return False

        self.audio_input = QAudioInput(device_info, format)
        self.audio_input.setVolume(1.0)
        self.audio_input.start(self.temp_pcm_file)
        self.is_recording = True
        return True

    def stop_recording(self) -> str:
        """Stops recording and returns path to final WAV file"""
        if not self.is_recording:
            return None

        self.is_recording = False
        if self.audio_input:
            self.audio_input.stop()
            self.audio_input = None

        if self.temp_pcm_file:
            self.temp_pcm_file.close()
            self.temp_pcm_file = None

        # Convert raw PCM to standard WAV
        if self.temp_pcm_path and os.path.exists(self.temp_pcm_path) and os.path.getsize(self.temp_pcm_path) > 0:
            try:
                with open(self.temp_pcm_path, 'rb') as pcm:
                    raw_data = pcm.read()

                with wave.open(self.final_wav_path, 'wb') as wav_file:
                    wav_file.setnchannels(self.channels)
                    wav_file.setsampwidth(2) # 16-bit = 2 bytes
                    wav_file.setframerate(self.sample_rate)
                    wav_file.writeframes(raw_data)
            except Exception:
                pass
            finally:
                try:
                    os.remove(self.temp_pcm_path)
                except Exception:
                    pass

        return self.final_wav_path if (self.final_wav_path and os.path.exists(self.final_wav_path)) else None


class MicLevelMonitor(QObject):
    level_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio_input = None
        self.io_device = None
        self.is_monitoring = False

    def start_monitoring(self, device_name: str = None) -> bool:
        if self.is_monitoring:
            self.stop_monitoring()

        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(1)
        fmt.setSampleSize(16)
        fmt.setCodec("audio/pcm")
        fmt.setByteOrder(QAudioFormat.LittleEndian)
        fmt.setSampleType(QAudioFormat.SignedInt)

        device_info = QAudioDeviceInfo.defaultInputDevice()
        if device_name:
            for d in QAudioDeviceInfo.availableDevices(QAudio.AudioInput):
                if d.deviceName().strip() == device_name.strip():
                    device_info = d
                    break

        if not device_info.isFormatSupported(fmt):
            fmt = device_info.nearestFormat(fmt)

        try:
            self.audio_input = QAudioInput(device_info, fmt, self)
            self.io_device = self.audio_input.start()
            if self.io_device:
                self.io_device.readyRead.connect(self._on_ready_read)
                self.is_monitoring = True
                return True
        except Exception:
            pass
        return False

    def _on_ready_read(self):
        if not self.io_device:
            return
        data = self.io_device.readAll()
        if not data or len(data) < 2:
            return
        try:
            samples = np.frombuffer(data, dtype=np.int16)
            if len(samples) > 0:
                peak = float(np.max(np.abs(samples))) / 32768.0
                level = min(100, int(peak * 180.0))
                self.level_changed.emit(level)
        except Exception:
            pass

    def stop_monitoring(self):
        self.is_monitoring = False
        if self.audio_input:
            try:
                self.audio_input.stop()
            except Exception:
                pass
            self.audio_input = None
        self.io_device = None
        self.level_changed.emit(0)


class SystemAudioRecorder(QObject):
    """
    Captures Windows system audio (WASAPI Loopback) during window/stream capture.
    Records clean 44.1kHz 16-bit PCM audio playing to the default speakers/headphones.
    """
    def __init__(self, sample_rate=44100, channels=2, parent=None):
        super().__init__(parent)
        self.sample_rate = sample_rate
        self.channels = channels
        self.output_wav_path = None
        self.is_recording = False
        self._thread = None
        self._stop_event = threading.Event()

    def start_recording(self, output_wav_path: str) -> bool:
        self.output_wav_path = output_wav_path
        self._stop_event.clear()
        self.is_recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        return True

    def _record_loop(self):
        try:
            import soundcard as sc
            import soundfile as sf
            spk = sc.default_speaker()
            loopback = sc.get_microphone(id=str(spk.id), include_loopback=True)
            with loopback.recorder(samplerate=self.sample_rate, channels=self.channels) as rec:
                with sf.SoundFile(self.output_wav_path, mode='w', samplerate=self.sample_rate,
                                  channels=self.channels, subtype='PCM_16') as sf_out:
                    block_frames = 2048
                    while not self._stop_event.is_set():
                        data = rec.record(numframes=block_frames)
                        sf_out.write(data)
        except Exception as e:
            print(f"[SystemAudioRecorder] Loopback error: {e}")

    def stop_recording(self) -> str:
        if not self.is_recording:
            return None
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.is_recording = False
        if self.output_wav_path and os.path.exists(self.output_wav_path) and os.path.getsize(self.output_wav_path) > 44:
            return self.output_wav_path
        return None


