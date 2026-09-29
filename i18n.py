#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Internationalization (i18n) module for FKVideoPlayer.
Supports English ('en') and Russian ('ru') with dynamic runtime switching.
"""

import json
import os

DEFAULT_LANGUAGE = 'en'
CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.fk_videoplayer_config.json')

STRINGS = {
    'en': {
        # Menus
        'menu_file': 'File',
        'menu_settings': 'Settings',
        'menu_video_settings': 'Video Settings',
        'menu_export': 'Export',
        'menu_help': 'Help',
        'act_new_canvas': 'New Canvas...',
        'act_open_video': 'Open Video...',
        'act_capture_window': 'Capture Window / Stream...',
        'act_close_project': 'Close Project',
        'act_recent_projects': 'Recent Projects',
        'act_save_actions': 'Save Actions JSON...',
        'act_load_actions': 'Load Actions JSON...',
        'act_exit': 'Exit',
        'prompt_unsaved_title': 'Unsaved Changes',
        'prompt_unsaved_text': 'You have unsaved drawings, overlays, or action recordings. Do you want to export your video before closing?',
        'btn_export_save': 'Export Video',
        'btn_discard': "Don't Save",
        'dlg_overlay_video_title': 'Video Overlay Settings',
        'lbl_overlay_playback': 'Playback Control:',
        'lbl_overlay_loop': 'Loop Video',
        'lbl_overlay_sync': 'Sync with Main Timeline',
        'lbl_overlay_speed': 'Playback Speed:',
        'lbl_overlay_opacity': 'Opacity:',
        'lbl_overlay_offset': 'Time Offset (sec):',
        'act_preferences': 'Preferences (Hotkeys, Language, Mic)...',
        'act_video_props': 'Video and Canvas Properties...',
        'act_export_video': 'Export Video with Edits...',
        'act_export_presets': 'Export Presets Manager...',
        'act_about': 'About FKVideoPlayer',
        'act_updates': 'Check for Updates...',
        'act_open_log_file': 'Open Log File',
        'act_open_logs_folder': 'Open Logs Folder',
        'act_welcome': 'Welcome & Support...',
        'welcome_title': 'Welcome to FKVideoPlayer',
        'welcome_heading': 'Welcome to FKVideoPlayer!',
        'welcome_desc': 'FKVideoPlayer is 100% free and open-source software for video playback, screen annotation, live window capture, and actions recording.\n\nAll features are completely free with no limits, ads, or watermarks.\nIf FKVideoPlayer helps you in your creative work, streaming, or video editing, you can optionally support the author with a donation!',
        'welcome_support_title': 'Support Creator:',
        'welcome_dont_show': "Don't show this message on startup",
        'welcome_start_btn': '🚀 Get Started',

        # Toolbar & Controls
        'btn_select': 'Select / Move Objects (V)',
        'btn_brush': 'Brush (B)',
        'btn_eraser': 'Eraser (E)',
        'btn_add_text': 'Add Text (T)',
        'btn_add_overlay': 'Add Image / GIF / PIP Video',
        'btn_undo': 'Undo (Ctrl+Z)',
        'btn_clear': 'Clear All Drawings (Del)',
        'btn_record_start': 'Record Actions',
        'btn_record_pause': 'Pause Recording',
        'btn_record_stop': 'Stop & Export',
        'btn_mic_on': 'Microphone: ON',
        'btn_mic_off': 'Microphone: OFF',
        'btn_play': 'Play (Space)',
        'btn_pause': 'Pause (Space)',
        'btn_step_prev': 'Step -1 Frame',
        'btn_step_next': 'Step +1 Frame',
        'btn_skip_back_1s': '-1s',
        'btn_skip_back_5s': '-5s',
        'btn_skip_fwd_1s': '+1s',
        'btn_skip_fwd_5s': '+5s',
        'btn_mute': 'Mute Audio',
        'btn_unmute': 'Unmute Audio',
        'btn_fit': 'Fit Window',
        'btn_100': '1:1 Zoom',
        'btn_grid': 'Toggle Grid',

        # Dialogs & Prompts
        'dlg_new_canvas_title': 'New Canvas Project',
        'dlg_new_canvas_preset': 'Resolution Preset:',
        'dlg_new_canvas_width': 'Width:',
        'dlg_new_canvas_height': 'Height:',
        'dlg_new_canvas_bg': 'Background Color:',
        'dlg_new_canvas_fps': 'Framerate (FPS):',
        'dlg_create': 'Create',
        'dlg_cancel': 'Cancel',

        'dlg_capture_title': 'Select Window to Capture / Stream',
        'dlg_capture_refresh': 'Refresh Window List',
        'dlg_capture_btn': 'Start Capturing Window',
        'dlg_capture_note': 'Select any open application, browser, or Twitch/YouTube stream to edit and draw over.',

        'dlg_text_title': 'Add / Edit Text Overlay',
        'dlg_text_label': 'Text:',
        'dlg_text_font': 'Font:',
        'dlg_text_size': 'Size:',
        'dlg_text_color': 'Color:',
        'dlg_text_bg': 'Background Fill:',
        'dlg_text_bold': 'Bold',
        'dlg_text_italic': 'Italic',

        'dlg_export_title': 'Export Rendered Video',
        'dlg_export_preset': 'Export Preset:',
        'dlg_export_codec': 'Video Codec:',
        'dlg_export_format': 'Container Format:',
        'dlg_export_res': 'Resolution:',
        'dlg_export_bitrate': 'Bitrate:',
        'dlg_export_rate_control': 'Rate Control:',
        'rate_control_vbr': 'VBR (Variable Bitrate)',
        'rate_control_cbr': 'CBR (Constant Bitrate)',
        'dlg_export_custom_bitrate': 'Custom Bitrate (kbps):',
        'dlg_bitrate_custom': 'Custom...',
        'dlg_export_fps': 'FPS:',
        'dlg_export_audio': 'Include Audio Commentary (Mic)',
        'dlg_export_save_preset': 'Save Current as Preset...',
        'dlg_export_start': 'Start Rendering',
        'dlg_export_rendering': 'Rendering Video...',

        'dlg_prefs_title': 'Preferences',
        'tab_hotkeys': 'Hotkeys',
        'tab_language': 'Language',
        'tab_audio': 'Audio & Microphone',
        'lbl_select_lang': 'Interface Language:',
        'lbl_mic_device': 'Microphone Input Device:',
        'btn_test_mic': 'Test Mic',
        'btn_reset_defaults': 'Reset to Defaults',
        'btn_save': 'Save',

        'dlg_about_title': 'About FKVideoPlayer',
        'about_desc': 'High-performance next-gen video player, screen editor, live annotation tool and actions recorder.',
        'about_author': 'Created by furrykit',
        'about_version': 'Version 1.1.1',
        'btn_donate_link': 'Donate / Support Creator',

        'dlg_updates_title': 'Check for Updates',
        'updates_checking': 'Checking repository for latest releases...',
        'updates_up_to_date': 'You are running the latest version of FKVideoPlayer!',
        'updates_available': 'A new update is available!',
        'updates_repo_url': 'GitHub Repository URL:',
        'btn_check_now': 'Check Now',
        'btn_download_update': 'Download Update',

        # Status & Notifications
        'status_ready': 'Ready',
        'status_video_loaded': 'Loaded video: {name} ({w}x{h}, {fps:.2f} fps)',
        'status_canvas_created': 'Created blank canvas ({w}x{h})',
        'status_window_capturing': 'Capturing window: {title} ({w}x{h})',
        'status_recording_started': 'Actions recording started (Microphone: {mic})',
        'status_recording_paused': 'Recording paused',
        'status_recording_resumed': 'Recording resumed',
        'status_export_complete': 'Export successfully finished: {path}',
        'status_export_failed': 'Export failed: {err}',
    },
    'ru': {
        # Menus
        'menu_file': 'Файл',
        'menu_settings': 'Настройки',
        'menu_video_settings': 'Настройки видео',
        'menu_export': 'Экспорт',
        'menu_help': 'Справка',
        'act_new_canvas': 'Новый холст...',
        'act_open_video': 'Открыть видео...',
        'act_capture_window': 'Захват окна / стрима...',
        'act_close_project': 'Закрыть проект',
        'act_recent_projects': 'Недавние проекты',
        'act_save_actions': 'Сохранить действия в JSON...',
        'act_load_actions': 'Загрузить действия из JSON...',
        'act_exit': 'Выход',
        'prompt_unsaved_title': 'Несохраненные изменения',
        'prompt_unsaved_text': 'У вас есть несохраненные рисунки, оверлеи или запись действий. Хотите экспортировать видео перед закрытием?',
        'btn_export_save': 'Экспортировать',
        'btn_discard': 'Не сохранять',
        'dlg_overlay_video_title': 'Настройки видео-оверлея',
        'lbl_overlay_playback': 'Управление воспроизведением:',
        'lbl_overlay_loop': 'Зациклить видео',
        'lbl_overlay_sync': 'Синхронизировать с таймлайном',
        'lbl_overlay_speed': 'Скорость:',
        'lbl_overlay_opacity': 'Прозрачность:',
        'lbl_overlay_offset': 'Смещение времени (сек):',
        'act_preferences': 'Параметры (Хоткеи, Язык, Микрофон)...',
        'act_video_props': 'Свойства видео и холста...',
        'act_export_video': 'Экспортировать видео с эдитами...',
        'act_export_presets': 'Управление пресетами экспорта...',
        'act_about': 'О программе FKVideoPlayer',
        'act_updates': 'Проверить обновления...',
        'act_open_log_file': 'Открыть файл логов',
        'act_open_logs_folder': 'Открыть папку с логами',
        'act_welcome': 'Приветствие и поддержка...',
        'welcome_title': 'Добро пожаловать в FKVideoPlayer',
        'welcome_heading': 'Добро пожаловать в FKVideoPlayer!',
        'welcome_desc': 'FKVideoPlayer — это полностью бесплатный плеер с открытым исходным кодом для просмотра видео, живого рисования, захвата окон и записи действий.\n\nВсе функции программы абсолютно бесплатны, без ограничений, рекламы и водяных знаков.\nЕсли плеер помогает вам в работе, стриминге или творчестве, вы можете добровольно поддержать автора донатом!',
        'welcome_support_title': 'Поддержать автора:',
        'welcome_dont_show': 'Больше не показывать при запуске',
        'welcome_start_btn': '🚀 Начать работу',

        # Toolbar & Controls
        'btn_select': 'Выбор / Перемещение объектов (V)',
        'btn_brush': 'Кисть (B)',
        'btn_eraser': 'Ластик (E)',
        'btn_add_text': 'Добавить текст (T)',
        'btn_add_overlay': 'Добавить картинку / GIF / PIP видео',
        'btn_undo': 'Отменить (Ctrl+Z)',
        'btn_clear': 'Очистить всё рисование (Del)',
        'btn_record_start': 'Запись действий',
        'btn_record_pause': 'Пауза записи',
        'btn_record_stop': 'Стоп и экспорт',
        'btn_mic_on': 'Микрофон: ВКЛ',
        'btn_mic_off': 'Микрофон: ВЫКЛ',
        'btn_play': 'Воспроизведение (Пробел)',
        'btn_pause': 'Пауза (Пробел)',
        'btn_step_prev': 'Шаг назад (-1 кадр)',
        'btn_step_next': 'Шаг вперед (+1 кадр)',
        'btn_skip_back_1s': '-1с',
        'btn_skip_back_5s': '-5с',
        'btn_skip_fwd_1s': '+1с',
        'btn_skip_fwd_5s': '+5с',
        'btn_mute': 'Отключить звук',
        'btn_unmute': 'Включить звук',
        'btn_fit': 'По размеру окна',
        'btn_100': 'Масштаб 1:1',
        'btn_grid': 'Сетка',

        # Dialogs & Prompts
        'dlg_new_canvas_title': 'Создание нового холста',
        'dlg_new_canvas_preset': 'Пресет разрешения:',
        'dlg_new_canvas_width': 'Ширина:',
        'dlg_new_canvas_height': 'Высота:',
        'dlg_new_canvas_bg': 'Цвет фона:',
        'dlg_new_canvas_fps': 'Частота кадров (FPS):',
        'dlg_create': 'Создать',
        'dlg_cancel': 'Отмена',

        'dlg_capture_title': 'Выбор окна для захвата и стрима',
        'dlg_capture_refresh': 'Обновить список окон',
        'dlg_capture_btn': 'Начать захват окна',
        'dlg_capture_note': 'Выберите любое открытое окно, браузер со стримом Twitch/YouTube для живого рисования и монтажа поверх него.',

        'dlg_text_title': 'Добавление / Редактирование текста',
        'dlg_text_label': 'Текст:',
        'dlg_text_font': 'Шрифт:',
        'dlg_text_size': 'Размер:',
        'dlg_text_color': 'Цвет текста:',
        'dlg_text_bg': 'Заливка фона:',
        'dlg_text_bold': 'Жирный',
        'dlg_text_italic': 'Курсив',

        'dlg_export_title': 'Экспорт отредактированного видео',
        'dlg_export_preset': 'Пресет экспорта:',
        'dlg_export_codec': 'Видео кодек:',
        'dlg_export_format': 'Формат контейнера:',
        'dlg_export_res': 'Разрешение:',
        'dlg_export_bitrate': 'Битрейт:',
        'dlg_export_rate_control': 'Режим битрейта:',
        'rate_control_vbr': 'VBR (Переменный битрейт)',
        'rate_control_cbr': 'CBR (Постоянный битрейт)',
        'dlg_export_custom_bitrate': 'Свой битрейт (кбит/с):',
        'dlg_bitrate_custom': 'Пользовательский...',
        'dlg_export_fps': 'FPS:',
        'dlg_export_audio': 'Включить аудио комментарии с микрофона',
        'dlg_export_save_preset': 'Сохранить как свой пресет...',
        'dlg_export_start': 'Начать рендер',
        'dlg_export_rendering': 'Рендеринг видео...',

        'dlg_prefs_title': 'Параметры программы',
        'tab_hotkeys': 'Горячие клавиши',
        'tab_language': 'Язык интерфейса',
        'tab_audio': 'Аудио и микрофон',
        'lbl_select_lang': 'Язык:',
        'lbl_mic_device': 'Устройство ввода микрофона:',
        'btn_test_mic': 'Проверить микрофон',
        'btn_reset_defaults': 'Сбросить по умолчанию',
        'btn_save': 'Сохранить',

        'dlg_about_title': 'О программе FKVideoPlayer',
        'about_desc': 'Высокопроизводительный видеоплеер, экранный редактор, инструмент живых аннотаций и рекордер действий нового поколения.',
        'about_author': 'Создано furrykit',
        'about_version': 'Версия 1.1.1',
        'btn_donate_link': 'Поддержать автора / Донат',

        'dlg_updates_title': 'Проверка обновлений',
        'updates_checking': 'Проверка репозитория на наличие новых релизов...',
        'updates_up_to_date': 'У вас установлена последняя версия FKVideoPlayer!',
        'updates_available': 'Доступна новая версия программы!',
        'updates_repo_url': 'URL репозитория GitHub:',
        'btn_check_now': 'Проверить сейчас',
        'btn_download_update': 'Скачать обновление',

        # Status & Notifications
        'status_ready': 'Готово',
        'status_video_loaded': 'Загружено видео: {name} ({w}x{h}, {fps:.2f} fps)',
        'status_canvas_created': 'Создан чистый холст ({w}x{h})',
        'status_window_capturing': 'Захват окна: {title} ({w}x{h})',
        'status_recording_started': 'Запись действий начата (Микрофон: {mic})',
        'status_recording_paused': 'Запись на паузе',
        'status_recording_resumed': 'Запись возобновлена',
        'status_export_complete': 'Экспорт успешно завершен: {path}',
        'status_export_failed': 'Ошибка экспорта: {err}',
    }
}

class I18nManager:
    _instance = None

    def __init__(self):
        self.lang = DEFAULT_LANGUAGE
        self.listeners = []
        self._load_config()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_config(self):
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.lang = data.get('language', DEFAULT_LANGUAGE)
        except Exception:
            self.lang = DEFAULT_LANGUAGE

    def save_config(self):
        try:
            data = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            data['language'] = self.lang
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def set_language(self, lang_code: str):
        if lang_code in STRINGS and lang_code != self.lang:
            self.lang = lang_code
            self.save_config()
            for cb in self.listeners:
                try:
                    cb()
                except Exception:
                    pass

    def add_listener(self, cb):
        if cb not in self.listeners:
            self.listeners.append(cb)

    def remove_listener(self, cb):
        if cb in self.listeners:
            self.listeners.remove(cb)

    def get(self, key: str, default: str = None, **kwargs) -> str:
        lang_dict = STRINGS.get(self.lang, STRINGS['en'])
        text = lang_dict.get(key, STRINGS['en'].get(key, default or key))
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

def tr(key: str, default: str = None, **kwargs) -> str:
    return I18nManager.instance().get(key, default, **kwargs)
