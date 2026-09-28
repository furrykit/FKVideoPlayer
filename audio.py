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
