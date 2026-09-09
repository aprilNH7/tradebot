import logging
import sys
from datetime import datetime

import colorlog


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Configure a coloured console logger and a daily rotating file handler.

    The logger writes colourised output to stdout and append-only records to
    logs/tradebot_YYYYMMDD.log. Calling the function twice for the same name
    returns the existing handler set instead of adding duplicates.
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

    # File handler
    file_handler = logging.FileHandler(
        f"logs/tradebot_{datetime.now().strftime('%Y%m%d')}.log"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)

    return logger
