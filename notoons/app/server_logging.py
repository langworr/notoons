"""Configure file logging for application and ASGI server output."""

import logging
import os
import re
from datetime import date


class DailyFileHandler(logging.FileHandler):
    """Write each local calendar day's records to a separate file."""

    def __init__(self, filename: str, **kwargs) -> None:
        self.filename_template = os.path.abspath(filename)
        self.current_date = date.today()
        super().__init__(self._dated_filename(self.current_date), **kwargs)

    def _dated_filename(self, day: date) -> str:
        directory, filename = os.path.split(self.filename_template)
        stem, extension = os.path.splitext(filename)
        return os.path.join(directory, f"{stem}_{day.isoformat()}{extension}")

    def emit(self, record: logging.LogRecord) -> None:
        today = date.today()
        if today != self.current_date:
            if self.stream:
                self.flush()
                self.stream.close()
                self.stream = None
            self.current_date = today
            self.baseFilename = self._dated_filename(today)
            self.stream = self._open()
        super().emit(record)


def configure_server_logging(config: dict) -> None:
    """Write application and Uvicorn logs to date-stamped server log files."""
    level = logging.getLevelNamesMapping()[config["server_log_level"]]
    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]

    for logger in loggers:
        for handler in logger.handlers[:]:
            if getattr(handler, "_notoons_server_log", False):
                logger.removeHandler(handler)
                handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    for logger in loggers:
        logger.setLevel(level)
        if logger is not loggers[0] and logger.propagate:
            continue
        handler = DailyFileHandler(config["server_log_file"], encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler._notoons_server_log = True
        logger.addHandler(handler)


def clear_server_logs(config: dict) -> int:
    """Clear dated server logs while keeping active handlers ready to write."""
    filename_template = os.path.abspath(config["server_log_file"])
    directory, filename = os.path.split(filename_template)
    stem, extension = os.path.splitext(filename)
    filename_pattern = re.compile(
        rf"^{re.escape(stem)}_\d{{4}}-\d{{2}}-\d{{2}}{re.escape(extension)}$"
    )
    handlers = [
        handler
        for logger in (
            logging.getLogger(),
            logging.getLogger("uvicorn.error"),
            logging.getLogger("uvicorn.access"),
        )
        for handler in logger.handlers
        if getattr(handler, "_notoons_server_log", False)
        and os.path.normcase(
            os.path.abspath(handler.filename_template)
        ) == os.path.normcase(filename_template)
    ]
    active_paths = {
        os.path.normcase(os.path.abspath(handler.baseFilename))
        for handler in handlers
    }

    cleared_paths = set()
    for handler in handlers:
        handler.acquire()
        try:
            path = os.path.normcase(os.path.abspath(handler.baseFilename))
            if handler.stream and path not in cleared_paths:
                handler.flush()
                handler.stream.seek(0)
                handler.stream.truncate()
                cleared_paths.add(path)
        finally:
            handler.release()

    with os.scandir(directory) as entries:
        for entry in entries:
            path = os.path.abspath(entry.path)
            if (
                filename_pattern.fullmatch(entry.name)
                and os.path.normcase(path) not in active_paths
                and entry.is_file(follow_symlinks=False)
            ):
                os.remove(path)
                cleared_paths.add(os.path.normcase(path))

    return len(cleared_paths)
