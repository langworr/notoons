"""Configure file logging for application and ASGI server output."""

import logging


def configure_server_logging(config: dict) -> None:
    """Write application and Uvicorn logs to the configured server log file."""
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
        handler = logging.FileHandler(config["server_log_file"], encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler._notoons_server_log = True
        logger.addHandler(handler)
