import sys
import os
import logging
from logging.handlers import RotatingFileHandler
import traceback
import threading
import platform

_LOG_FILE_PATH = None
_LOGGER = None


def get_default_log_dir() -> str:
    """Determine the optimal writable directory for log files."""
    # 1. Prefer executable / script directory if writable (ideal for portable mode)
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    try:
        test_file = os.path.join(base_dir, f".write_test_{os.getpid()}")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("ok")
        if os.path.exists(test_file):
            os.remove(test_file)
        return base_dir
    except Exception:
        pass

    # 2. Fallback to Windows APPDATA or user home
    appdata = os.environ.get('APPDATA')
    if appdata and os.path.isdir(appdata):
        target = os.path.join(appdata, "FKVideoPlayer", "logs")
        try:
            os.makedirs(target, exist_ok=True)
            return target
        except Exception:
            pass

    home = os.path.expanduser('~')
    target = os.path.join(home, ".fk_videoplayer", "logs")
    try:
        os.makedirs(target, exist_ok=True)
        return target
    except Exception:
        return tempfile.gettempdir()


def get_log_file_path() -> str:
    global _LOG_FILE_PATH
    if _LOG_FILE_PATH is None:
        log_dir = get_default_log_dir()
        _LOG_FILE_PATH = os.path.join(log_dir, "FKVideoPlayer.log")
    return _LOG_FILE_PATH


def init_logging(log_level=logging.INFO) -> logging.Logger:
    """Initialize rotating file and console logging."""
    global _LOGGER, _LOG_FILE_PATH
    if _LOGGER is not None:
        return _LOGGER

    log_path = get_log_file_path()
    logger = logging.getLogger("FKVideoPlayer")
    logger.setLevel(log_level)
    logger.propagate = False

    # Avoid duplicate handlers on re-init
    if not logger.handlers:
        fmt = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. File Handler (Rotating: 10 MB each, up to 5 backups)
        try:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding='utf-8',
                delay=False
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Failed to initialize log file at {log_path}: {e}\n")

        # 2. Console Handler
        try:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(log_level)
            console_handler.setFormatter(fmt)
            logger.addHandler(console_handler)
        except Exception:
            pass

    _LOGGER = logger

    # Log initial environment header
    logger.info("=" * 60)
    logger.info("FKVideoPlayer session started")
    logger.info(f"Log file: {log_path}")
    logger.info(f"Python: {platform.python_version()} ({platform.architecture()[0]})")
    logger.info(f"OS: {platform.system()} {platform.release()} ({platform.version()})")
    logger.info(f"PID: {os.getpid()} | CWD: {os.getcwd()}")
    logger.info("=" * 60)

    # Setup crash hook and threading hook
    setup_crash_handlers(logger)
    return logger


def get_logger(name: str = None) -> logging.Logger:
    """Retrieve logger instance with prefix."""
    if _LOGGER is None:
        init_logging()
    if name:
        return logging.getLogger(f"FKVideoPlayer.{name}")
    return _LOGGER


def setup_crash_handlers(logger: logging.Logger):
    """Installs hooks for uncaught exceptions across main and worker threads."""
    original_excepthook = sys.excepthook

    def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            original_excepthook(exc_type, exc_value, exc_traceback)
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        logger.critical(f"FATAL UNHANDLED EXCEPTION:\n{tb_text}")

        # Flush all handlers to disk immediately
        for handler in logger.handlers:
            try:
                handler.flush()
            except Exception:
                pass

        # If PyQt QApplication exists, alert the user with details
        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance()
            if app:
                msg = QMessageBox()
                msg.setIcon(QMessageBox.Critical)
                msg.setWindowTitle("FKVideoPlayer - Crash Report")
                msg.setText(f"An unexpected error occurred:\n<b>{exc_type.__name__}: {exc_value}</b>")
                msg.setInformativeText(f"A detailed error log has been saved to:\n{get_log_file_path()}")
                msg.setDetailedText(tb_text)
                msg.exec_()
        except Exception:
            pass

        original_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_unhandled_exception

    # Python 3.8+ threading.excepthook for background worker threads
    if hasattr(threading, 'excepthook'):
        def handle_thread_exception(args):
            if issubclass(args.exc_type, KeyboardInterrupt):
                return
            tb_text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            logger.error(f"UNHANDLED EXCEPTION IN THREAD '{args.thread.name}':\n{tb_text}")
            for handler in logger.handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

        threading.excepthook = handle_thread_exception

    # Qt message handler hook
    try:
        from PyQt5.QtCore import qInstallMessageHandler, QtMsgType

        def qt_message_handler(mode, context, message):
            qt_log = logging.getLogger("FKVideoPlayer.Qt")
            # Filter high-frequency harmless warnings
            if "QPainter::" in message or "QBackingStore::" in message:
                return
            if mode == QtMsgType.QtDebugMsg:
                qt_log.debug(message)
            elif mode == QtMsgType.QtInfoMsg:
                qt_log.info(message)
            elif mode == QtMsgType.QtWarningMsg:
                qt_log.warning(message)
            elif mode == QtMsgType.QtCriticalMsg:
                qt_log.error(message)
            elif mode == QtMsgType.QtFatalMsg:
                qt_log.critical(message)

        qInstallMessageHandler(qt_message_handler)
    except Exception:
        pass


def open_log_file():
    """Open the current log file in the operating system's default text editor."""
    path = get_log_file_path()
    if not os.path.exists(path):
        # Create empty log file if not existing yet
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write("")
        except Exception:
            pass

    try:
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            import subprocess
            subprocess.Popen(['open', path])
        else:
            import subprocess
            subprocess.Popen(['xdg-open', path])
    except Exception as e:
        get_logger().error(f"Failed to open log file {path}: {e}")


def open_logs_folder():
    """Open the folder containing log files in Explorer/Finder."""
    path = get_log_file_path()
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

    try:
        if sys.platform == 'win32':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            import subprocess
            subprocess.Popen(['open', folder])
        else:
            import subprocess
            subprocess.Popen(['xdg-open', folder])
    except Exception as e:
        get_logger().error(f"Failed to open logs folder {folder}: {e}")
