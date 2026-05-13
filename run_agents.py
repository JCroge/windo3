#!/usr/bin/env python3
"""多 Agent 交易系统启动入口（支持远程重启）"""

import os
import time
from agents.orchestrator import Orchestrator


def main():
    flag_file = 'data/.restart_flag'

    while True:
        orchestrator = Orchestrator()
        orchestrator.start()

        if os.path.exists(flag_file):
            os.remove(flag_file)
            print("[启动器] 检测到重启标记，3秒后重启...")
            time.sleep(3)
            continue
        else:
            break


if __name__ == '__main__':
    main()
