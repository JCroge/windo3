"""pytest 全局 fixtures — 隔离 data/ 和 logs/ 到临时目录，防止测试污染生产状态"""
import os
import shutil
import pytest


# 项目根目录的真实 klines DB（network 测试可用），由 isolate_data_dirs 之前 capture
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_PROJECT_KLINES_DB = os.path.join(_PROJECT_ROOT, 'data', 'klines.db')


@pytest.fixture
def klines_db(tmp_path):
    """为 network 集成测试准备 klines DB；项目根没有真实 DB 则 skip。

    场景：`test_indicators.py` / `test_backtest.py` / `test_strategy.py` 均依赖
    `data/klines.db`。autouse 的 `isolate_data_dirs` 会 chdir 到 tmp_path，所以
    需要把项目根的 DB 拷贝过来；缺 DB 时跳过并给出准备说明，避免因临时 cwd 无
    DB 而 fail（AC-P1-006）。
    """
    if not os.path.exists(_PROJECT_KLINES_DB):
        pytest.skip(
            "缺少 data/klines.db；先运行 `python3 test_kline.py` 采集数据，"
            "或手动从生产环境同步该文件再跑 network 测试"
        )
    dst_dir = tmp_path / 'data'
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / 'klines.db'
    shutil.copy2(_PROJECT_KLINES_DB, dst)
    return str(dst)


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
