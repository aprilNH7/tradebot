import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

import colorlog


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Configure a coloured console logger and a daily rotating file handler.

    The logger writes colourised output to stdout and append-only records to
    logs/tradebot.log, rotating at midnight and keeping 7 days of backups.
    Calling the function twice for the same name returns the existing handler
    set instead of adding duplicates.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler with color
    console = colorlog.StreamHandler(sys.stdout)
    console.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    ))
    logger.addHandler(console)

    # Rotating file handler
    os.makedirs("logs", exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename="logs/tradebot.log",
        when="midnight",
        interval=1,
        backupCount=7,
        utc=True,
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)

    return logger
