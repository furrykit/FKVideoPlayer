import sys
import os
import collections
import cv2
import numpy as np

from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtGui import QIcon, QKeySequence, QFont
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QMessageBox
from PyQt5.QtMultimedia import QMediaPlayer

# Optimization: limit OpenCV thread pool to prevent thread contention on high-core CPUs (e.g. Ryzen 5800X)
try:
    cv2.setNumThreads(4)
except Exception:
    pass

from fkplayer.core.geometry import (
    APP_VERSION, set_dark_titlebar, resource_path, get_ffmpeg_path,
    dist_to_segment_sq, simplify_points, erase_stroke_subsegments
)
from fkplayer.ui.timeline import ClickableSlider, OverlayTrackRow, OverlayTrackContainer
from fkplayer.ui.canvas import Stroke, OverlayObject, VideoCanvas
from fkplayer.media.recorder import ActionRecorder
from fkplayer.media.export import ExportVideoWorker
from fkplayer.core.session import ProjectSession
from fkplayer.core.i18n import tr, I18nManager
from fkplayer.core.projects import ProjectManager
from fkplayer.ui.dialogs import (
    PreferencesDialog, AboutDialog, UpdatesDialog,
    parse_version, safe_open_url, WelcomeDialog,
    ExportDialog, GitHubUpdateCheckerWorker, DONATEPAY_URL
)
from fkplayer.core.logger import init_logging, get_logger, open_log_file, open_logs_folder
from fkplayer.media.audio import MicrophoneRecorder, get_audio_input_devices, SystemAudioRecorder
from fkplayer.media.capture import list_open_windows, WindowCaptureWorker

from fkplayer.ui.builder import UIBuilderMixin
from fkplayer.media.playback import PlaybackMixin
from fkplayer.ui.overlay_actions import OverlayActionsMixin
from fkplayer.media.record_controller import RecordingMixin
from fkplayer.core.tabs import TabManagerMixin

logger = get_logger("Player")

class FKVideoPlayer(
    QMainWindow,
    UIBuilderMixin,
    PlaybackMixin,
    OverlayActionsMixin,
    RecordingMixin,
    TabManagerMixin
):
    MAX_CACHE_MEMORY_BYTES = 120 * 1024 * 1024  # 120 MB maximum frame cache budget
    MAX_CACHE_FRAMES = 60

    def __init__(self, initial_video_path=None):
        super().__init__()
        self.setWindowTitle("FKVideoPlayer")
        self.resize(1180, 800)
        self.setMinimumSize(920, 540)
        self.setAcceptDrops(True)

        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.projects = []
        self._fallback_canvas = VideoCanvas(None)
        self._fallback_canvas.hide()
        self._fallback_recorder = ActionRecorder(self)
        self._fallback_cache = collections.OrderedDict()

        self.audio_player = QMediaPlayer(self, QMediaPlayer.LowLatency)
        self.audio_player.setNotifyInterval(20)
        self.current_volume = 80
        self.is_muted = False
        self.audio_player.setVolume(self.current_volume)
        self._play_start_time = 0.0
        self._play_start_frame = 0
        self._play_start_audio_pos = 0
        self._last_seen_audio_pos = -1
        self._last_audio_advance_time = 0.0

        self._pending_seek_frame = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._process_pending_seek)

        self._scrub_settle_timer = QTimer(self)
        self._scrub_settle_timer.setSingleShot(True)
        self._scrub_settle_timer.timeout.connect(self._on_scrub_settle)

        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._on_step_timer)

        self._audio_sync_timer = QTimer(self)
        self._audio_sync_timer.setSingleShot(True)
        self._audio_sync_timer.timeout.connect(self._sync_audio_position)

        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.PreciseTimer)
        self.play_timer.timeout.connect(self._on_play_tick)

        self.rec_update_timer = QTimer(self)
        self.rec_update_timer.timeout.connect(self._on_rec_update_timer)
        self.export_worker = None

        self.overlay_anim_timer = QTimer(self)
        self.overlay_anim_timer.timeout.connect(self._on_overlay_anim_tick)
        self.overlay_anim_timer.start(33)

        self.mic_recorder = MicrophoneRecorder(parent=self)
        self.is_mic_enabled = False
        self.last_mic_wav = None
        self.selected_mic_device = None

        self.sys_audio_recorder = SystemAudioRecorder(parent=self)
        self.last_sys_wav = None
        self.temp_sys_wav_path = None

        self.temp_capture_writer = None
        self.temp_capture_video_path = None

        self._init_ui()
        self._apply_dark_theme()
        self._setup_shortcuts()

        self.new_project_tab(name="Project 1", project_type="empty")

        if initial_video_path and os.path.exists(initial_video_path):
            self.load_video(initial_video_path)

        QTimer.singleShot(2500, self._check_updates_background)

    @property
    def active_project(self):
        if hasattr(self, 'projects') and self.projects:
            idx = self.project_tabs.currentIndex() if hasattr(self, 'project_tabs') else 0
            if 0 <= idx < len(self.projects):
                return self.projects[idx]
            if len(self.projects) > 0:
                return self.projects[0]
        return getattr(self, '_fallback_project', None)

    @property
    def canvas(self):
        proj = self.active_project
        return proj.canvas if proj else self._fallback_canvas

    @property
    def recorder(self):
        proj = self.active_project
        return proj.recorder if proj else self._fallback_recorder

    @recorder.setter
    def recorder(self, val):
        proj = self.active_project
        if proj:
            proj.recorder = val

    @property
    def cap(self):
        proj = self.active_project
        return proj.cap if proj else None

    @cap.setter
    def cap(self, val):
        proj = self.active_project
        if proj:
            proj.cap = val

    @property
    def video_path(self):
        proj = self.active_project
        return proj.video_path if proj else ""

    @video_path.setter
    def video_path(self, val):
        proj = self.active_project
        if proj:
            proj.video_path = val

    @property
    def fps(self):
        proj = self.active_project
        return proj.fps if proj else 24.0

    @fps.setter
    def fps(self, val):
        proj = self.active_project
        if proj:
            proj.fps = val

    @property
    def total_frames(self):
        proj = self.active_project
        return proj.total_frames if proj else 0

    @total_frames.setter
    def total_frames(self, val):
        proj = self.active_project
        if proj:
            proj.total_frames = val

    @property
    def current_frame_idx(self):
        proj = self.active_project
        return proj.current_frame_idx if proj else 0

    @current_frame_idx.setter
    def current_frame_idx(self, val):
        proj = self.active_project
        if proj:
            proj.current_frame_idx = val

    @property
    def is_playing(self):
        proj = self.active_project
        return proj.is_playing if proj else False

    @is_playing.setter
    def is_playing(self, val):
        proj = self.active_project
        if proj:
            proj.is_playing = val

    @property
    def is_looping(self):
        proj = self.active_project
        return proj.is_looping if proj else True

    @is_looping.setter
    def is_looping(self, val):
        proj = self.active_project
        if proj:
            proj.is_looping = val

    @property
    def playback_speed(self):
        proj = self.active_project
        return proj.playback_speed if proj else 1.0

    @playback_speed.setter
    def playback_speed(self, val):
        proj = self.active_project
        if proj:
            proj.playback_speed = val

    @property
    def frames_per_tick(self):
        proj = self.active_project
        return proj.frames_per_tick if proj else 1

    @frames_per_tick.setter
    def frames_per_tick(self, val):
        proj = self.active_project
        if proj:
            proj.frames_per_tick = val

    @property
    def has_audio(self):
        proj = self.active_project
        return proj.has_audio if proj else False

    @has_audio.setter
    def has_audio(self, val):
        proj = self.active_project
        if proj:
            proj.has_audio = val

    @property
    def temp_audio_path(self):
        proj = self.active_project
        return proj.temp_audio_path if proj else None

    @temp_audio_path.setter
    def temp_audio_path(self, val):
        proj = self.active_project
        if proj:
            proj.temp_audio_path = val

    @property
    def frame_cache(self):
        proj = self.active_project
        return proj.frame_cache if proj else self._fallback_cache

    @property
    def _cap_pos(self):
        proj = self.active_project
        return proj._cap_pos if proj else -1

    @_cap_pos.setter
    def _cap_pos(self, val):
        proj = self.active_project
        if proj:
            proj._cap_pos = val

    @property
    def window_capture_worker(self):
        proj = self.active_project
        return proj.window_capture_worker if proj else None

    @window_capture_worker.setter
    def window_capture_worker(self, val):
        proj = self.active_project
        if proj:
            proj.window_capture_worker = val

    @property
    def is_capturing_window(self):
        proj = self.active_project
        return proj.is_capturing_window if proj else False

    @is_capturing_window.setter
    def is_capturing_window(self, val):
        proj = self.active_project
        if proj:
            proj.is_capturing_window = val

    @property
    def captured_window_hwnd(self):
        proj = self.active_project
        return proj.captured_window_hwnd if proj else None

    @captured_window_hwnd.setter
    def captured_window_hwnd(self, val):
        proj = self.active_project
        if proj:
            proj.captured_window_hwnd = val

    @property
    def captured_window_title(self):
        proj = self.active_project
        return proj.captured_window_title if proj else ""

    @captured_window_title.setter
    def captured_window_title(self, val):
        proj = self.active_project
        if proj:
            proj.captured_window_title = val

    @property
    def temp_capture_writer(self):
        proj = self.active_project
        return proj.temp_capture_writer if proj else None

    @temp_capture_writer.setter
    def temp_capture_writer(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_writer = val

    @property
    def temp_capture_writer_size(self):
        proj = self.active_project
        return proj.temp_capture_writer_size if proj else None

    @temp_capture_writer_size.setter
    def temp_capture_writer_size(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_writer_size = val

    @property
    def temp_capture_video_path(self):
        proj = self.active_project
        return proj.temp_capture_video_path if proj else None

    @temp_capture_video_path.setter
    def temp_capture_video_path(self, val):
        proj = self.active_project
        if proj:
            proj.temp_capture_video_path = val

    @property
    def _capture_frame_count(self):
        proj = self.active_project
        return proj._capture_frame_count if proj else 0

    @_capture_frame_count.setter
    def _capture_frame_count(self, val):
        proj = self.active_project
        if proj:
            proj._capture_frame_count = val

    def showEvent(self, event):
        super().showEvent(event)
        set_dark_titlebar(self)
        if not getattr(self, '_welcome_checked', False):
            self._welcome_checked = True
            from PyQt5.QtCore import QSettings
            settings = QSettings("furrykit", "FKVideoPlayer")
            if settings.value("show_welcome", True, type=bool):
                QTimer.singleShot(250, self.open_welcome_dialog)

    def open_welcome_dialog(self, *args):
        dlg = WelcomeDialog(parent=self)
        dlg.exec_()

    def undo_last_action(self, *args):
        c = self.canvas
        if c:
            c.undo_last_action(*args)

    def clear_all_drawings(self, *args):
        c = self.canvas
        if c:
            c.clear_all_drawings(*args)

    def zoom_in(self, *args):
        c = self.canvas
        if c:
            c.zoom_in(*args)

    def zoom_out(self, *args):
        c = self.canvas
        if c:
            c.zoom_out(*args)

    def fit_to_view(self, *args):
        c = self.canvas
        if c:
            c.fit_to_view(*args)

    def open_preferences_dialog(self, *args):
        dlg = PreferencesDialog(parent=self)
        dlg.hotkeys_updated.connect(self._setup_shortcuts)
        dlg.language_changed.connect(lambda _: self.update_ui_texts())
        dlg.exec_()

    def open_video_properties_dialog(self, *args):
        QMessageBox.information(
            self,
            "Video & Canvas Properties",
            f"Source: {self.video_path or 'Blank Canvas / Stream'}\n"
            f"Resolution: {self.canvas.video_width}x{self.canvas.video_height}\n"
            f"FPS: {self.fps:.2f}\n"
            f"Total Frames: {self.total_frames}"
        )

    def open_export_presets_dialog(self, *args):
        dlg = ExportDialog(default_w=self.canvas.video_width, default_h=self.canvas.video_height, parent=self)
        dlg.exec_()

    def open_about_dialog(self, *args):
        dlg = AboutDialog(parent=self)
        dlg.exec_()

    def open_updates_dialog(self, *args):
        dlg = UpdatesDialog(parent=self)
        dlg.exec_()

    def _check_updates_background(self):
        try:
            from fkplayer.ui.dialogs import GitHubUpdateCheckerWorker
            self._update_worker = GitHubUpdateCheckerWorker(current_version=APP_VERSION, parent=self)
            self._update_worker.finished.connect(self._on_update_check_bg_finished)
            self._update_worker.start()
        except Exception:
            pass

    def _on_update_check_bg_finished(self, info: dict):
        if info.get("is_newer"):
            tag = info.get("tag_name", "")
            ans = QMessageBox.question(
                self,
                tr('updates_available'),
                f"{tr('updates_available')}\n\nVersion: {tag}\n\n{tr('btn_download_update')}?",
                QMessageBox.Yes | QMessageBox.No
            )
            if ans == QMessageBox.Yes:
                self.open_updates_dialog()

    def open_donate(self, *args):
        try:
            from fkplayer.ui.dialogs import DONATEPAY_URL, safe_open_url
            safe_open_url(DONATEPAY_URL)
        except Exception as e:
            logger.error(f"Failed to open donate URL: {e}")

    def closeEvent(self, event):
        for i, proj in enumerate(list(self.projects)):
            if proj.has_unsaved_changes():
                self.project_tabs.setCurrentIndex(i)
                if not self.prompt_unsaved_changes(project=proj):
                    event.ignore()
                    return

        # Stop timers & audio
        if hasattr(self, 'play_timer') and self.play_timer.isActive():
            self.play_timer.stop()
        if hasattr(self, '_audio_sync_timer') and self._audio_sync_timer.isActive():
            self._audio_sync_timer.stop()
        if hasattr(self, 'rec_update_timer') and self.rec_update_timer.isActive():
            self.rec_update_timer.stop()
        if hasattr(self, 'audio_player'):
            try:
                self.audio_player.stop()
            except Exception:
                pass
        self._cleanup_temp_audio()

        # Stop mic & system audio recording
        if hasattr(self, 'mic_recorder') and getattr(self.mic_recorder, 'is_recording', False):
            try:
                self.mic_recorder.stop_recording()
            except Exception:
                pass
        if hasattr(self, 'sys_audio_recorder') and getattr(self.sys_audio_recorder, 'is_recording', False):
            try:
                self.sys_audio_recorder.stop_recording()
            except Exception:
                pass

        # Stop active export worker if running
        if hasattr(self, 'export_worker') and self.export_worker is not None:
            try:
                self.export_worker.cancel()
                self.export_worker.wait(1000)
            except Exception:
                pass
            self.export_worker = None

        # Stop active window capture worker if running
        if hasattr(self, 'window_capture_worker') and self.window_capture_worker is not None:
            try:
                self.window_capture_worker.stop()
                self.window_capture_worker.wait(1000)
            except Exception:
                pass
            self.window_capture_worker = None

        # Stop update worker if running
        if hasattr(self, '_update_worker') and self._update_worker is not None:
            try:
                if self._update_worker.isRunning():
                    self._update_worker.quit()
                    if not self._update_worker.wait(300):
                        self._update_worker.terminate()
                        self._update_worker.wait(300)
            except Exception:
                pass
            self._update_worker = None

        if hasattr(self, 'cap') and self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass

        for proj in self.projects:
            try:
                proj.close()
            except Exception:
                pass

        # Clean up session temporary audio/video files
        for p in [getattr(self, 'temp_mic_wav_path', None),
                  getattr(self, 'temp_sys_wav_path', None),
                  getattr(self, 'temp_capture_video_path', None),
                  getattr(self, 'last_mic_wav', None),
                  getattr(self, 'last_sys_wav', None)]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        event.accept()


VideoPlayerWindow = FKVideoPlayer

def main():
    init_logging()
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, 'AA_DisableWindowContextHelpButton'):
        QApplication.setAttribute(Qt.AA_DisableWindowContextHelpButton, True)

    logger.info("Initializing QApplication")
    app = QApplication(sys.argv)

    # Set uniform scalable UI font so layout metrics match rendered text exactly
    app_font = app.font()
    app_font.setFamily("Segoe UI")
    app_font.setStyleHint(QFont.SansSerif)
    app_font.setPointSize(9)
    app.setFont(app_font)

    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    initial_file = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        initial_file = sys.argv[1]
        logger.info(f"Opening with initial file argument: {initial_file}")

    player = FKVideoPlayer(initial_file)
    player.show()
    logger.info("FKVideoPlayer main window displayed")
    ret = app.exec_()
    logger.info(f"FKVideoPlayer exiting with code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
