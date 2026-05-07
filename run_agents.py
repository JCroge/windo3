#!/usr/bin/env python3
"""多 Agent 交易系统启动入口"""

from agents.orchestrator import Orchestrator


if __name__ == '__main__':
    orchestrator = Orchestrator()
    orchestrator.start()
