import logging
import os
from datetime import datetime

def setup_logger(name):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # 启动健壮性：logs/ 目录可能不存在（全新部署），先确保创建
    os.makedirs('logs', exist_ok=True)

    handler = logging.FileHandler(f'logs/{name}_{datetime.now().strftime("%Y%m%d")}.log')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logger.addHandler(handler)
    logger.addHandler(console)
    return logger
