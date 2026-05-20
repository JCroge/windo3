"""pytest 全局 fixtures — 隔离 data/ 和 logs/ 到临时目录，防止测试污染生产状态"""
import os
import pytest


@pytest.fixture(autouse=True)
def isolate_data_dirs(tmp_path, monkeypatch):
    """将 data/ 和 logs/ 重定向到 tmp_path，每个测试独立隔离"""
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()

    # 切换工作目录到 tmp_path，使所有相对路径都指向隔离目录
    monkeypatch.chdir(tmp_path)

    # 将项目根目录加入 sys.path，保证 import 正常
    import sys
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # 重置 HaltState 单例，防止跨测试状态泄漏
    import utils.halt_state as hs_mod
    hs_mod._instance = None
