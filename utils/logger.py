import logging
import logging.handlers
import os
from datetime import datetime

_MAX_BYTES = 50 * 1024 * 1024  # 50MB per log file
_BACKUP_COUNT = 5              # keep 5 rotated files


def setup_logger(name):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    os.makedirs('logs', exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        f'logs/{name}_{datetime.now().strftime("%Y%m%d")}.log',
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logger.addHandler(handler)
    logger.addHandler(console)
    return logger
