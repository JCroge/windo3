#!/usr/bin/env python3
"""多 Agent 交易系统启动入口（支持远程重启）"""

import os
import sys
import time
import logging
from agents.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('launcher')
RESTART_FLAG_FILE = 'data/.restart_flag'


def _restart_process(argv=None, delay_seconds=3):
    """通过 execv 置换当前解释器镜像，确保重新加载磁盘上的源码。"""
    argv = argv or sys.argv
    script_path = os.path.abspath(argv[0])
    exec_argv = [sys.executable, script_path, *argv[1:]]

    logger.info("检测到重启标记，3秒后通过execv重启解释器...")
    time.sleep(delay_seconds)
    logging.shutdown()
    os.execv(sys.executable, exec_argv)
    raise RuntimeError("os.execv returned unexpectedly")


def main(argv=None):
    flag_file = RESTART_FLAG_FILE

    while True:
        try:
            orchestrator = Orchestrator()
            orchestrator.start()
        except Exception as e:
            logger.exception(f"Orchestrator异常退出: {e}")

        if os.path.exists(flag_file):
            os.remove(flag_file)
            try:
                _restart_process(argv=argv)
                break
            except Exception as e:
                logger.exception(f"execv重启失败: {e}")
                break
        else:
            logger.info("正常退出，无重启标记")
            break


if __name__ == '__main__':
    main()
