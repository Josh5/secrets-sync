import logging
import os


class bcolours:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[33m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    HIGHINTENSITYRED = "\033[1;91m"
    WHITE = "\033[0;37m"
    HIGHINTENSITYWHITE = "\033[97m"


class LevelColorFormatter(logging.Formatter):
    """Apply colours to log messages based on level."""

    COLOR_MAP = {
        logging.DEBUG: bcolours.OKBLUE,
        logging.INFO: bcolours.WHITE,
        logging.WARNING: bcolours.WARNING,
        logging.ERROR: bcolours.FAIL,
        logging.CRITICAL: bcolours.HIGHINTENSITYRED,
    }

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        color = self.COLOR_MAP.get(record.levelno, bcolours.HIGHINTENSITYWHITE)
        return f"{color}{base}{bcolours.ENDC}"


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(
        LevelColorFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        handlers=[handler],
    )
