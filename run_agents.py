#!/usr/bin/env python3
"""多 Agent 交易系统启动入口（支持远程重启）"""

import os
import sys
import time
import asyncio
import logging
from agents.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('launcher')


def main():
    flag_file = 'data/.restart_flag'

    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            orchestrator = Orchestrator()
            orchestrator.start()
        except Exception as e:
            logger.error(f"Orchestrator异常退出: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

        if os.path.exists(flag_file):
            os.remove(flag_file)
            logger.info("检测到重启标记，3秒后重启...")
            time.sleep(3)
            continue
        else:
            logger.info("正常退出，无重启标记")
            break


if __name__ == '__main__':
    main()
