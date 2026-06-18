"""轮换尊重持仓研判（B-revised）单元测试

测试要点：
1. config 四段式接入 rotation_close_held_enabled（默认 False / env 覆盖 / banner 展示）
2. SymbolRouter B-revised 门控（持仓保留不平 / 无持仓仍平 / 开关回退 / fail-safe）
"""
import sys
import os
import json
import asyncio
import tempfile
sys.path.insert(0, '.')


# ───────────────── Config 四段式 ─────────────────

_BASE_YAML = "risk: {}\n"


def test_config_default_is_false():
    """默认 rotation_close_held_enabled=False（保护生效）"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write(_BASE_YAML)
        path = f.name
    try:
        cfg = load_config(yaml_path=path, strict_live_check=False)
        assert cfg.get('rotation_close_held_enabled') is False, \
            f"默认应为 False，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        os.unlink(path)
    print("  ✅ Case: config 默认 False")


def test_config_env_override_true():
    """env ROTATION_CLOSE_HELD_ENABLED=true 覆盖为 True"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write(_BASE_YAML)
        path = f.name
    os.environ['ROTATION_CLOSE_HELD_ENABLED'] = 'true'
    try:
        cfg = load_config(yaml_path=path, strict_live_check=False)
        assert cfg.get('rotation_close_held_enabled') is True, \
            f"env 覆盖应为 True，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        del os.environ['ROTATION_CLOSE_HELD_ENABLED']
        os.unlink(path)
    print("  ✅ Case: config env 覆盖 True")


def test_config_yaml_override_true():
    """config.yaml risk.rotation_close_held_enabled=true 生效"""
    from utils.config_loader import load_config
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write("risk:\n  rotation_close_held_enabled: true\n")
        path = f.name
    try:
        cfg = load_config(yaml_path=path, strict_live_check=False)
        assert cfg.get('rotation_close_held_enabled') is True, \
            f"yaml 覆盖应为 True，实际 {cfg.get('rotation_close_held_enabled')}"
    finally:
        os.unlink(path)
    print("  ✅ Case: config yaml 覆盖 True")


def test_banner_shows_rotation_flag():
    """启动 banner 含「轮换强平持仓」行"""
    from utils.config_loader import load_config, format_banner
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
        f.write(_BASE_YAML)
        path = f.name
    try:
        cfg = load_config(yaml_path=path, strict_live_check=False)
        banner = format_banner(cfg)
        assert '轮换强平持仓' in banner, "banner 应含「轮换强平持仓」行"
        rot_line = next((ln for ln in banner.splitlines() if '轮换强平持仓' in ln), '')
        assert rot_line, "banner 应含「轮换强平持仓」行"
        assert '关闭' in rot_line, f"默认应显示『关闭』，实际行: {rot_line}"
    finally:
        os.unlink(path)
    print("  ✅ Case: banner 展示开关状态")


# ───────────────── _get_position_symbols fail-safe ─────────────────

def _new_router(close_held=False):
    """裸构造 SymbolRouter（不需 exchange），绕过轮换冷却"""
    from agents.research.symbol_router import SymbolRouter
    r = SymbolRouter(config={'rotation_close_held_enabled': close_held})
    r._min_rotation_interval = 0          # 绕过 3600s 冷却
    r._active_symbols = ['XLM-USDT', 'SUI-USDT']
    return r


def test_get_position_symbols_missing_file(monkeypatch):
    """positions 文件不存在 → 返回 []"""
    import utils.state_paths as sp
    r = _new_router()
    missing = tempfile.mktemp(suffix='.json')   # 不创建
    monkeypatch.setattr(
        sp, 'get_state_paths',
        lambda: type('P', (), {'positions': missing})()
    )
    assert r._get_position_symbols() == [], "缺失文件应返回 []"
    print("  ✅ Case: positions 文件缺失 fail-safe []")


def test_get_position_symbols_corrupt_file(monkeypatch):
    """positions 文件损坏 → 返回 [] 不抛"""
    import utils.state_paths as sp
    r = _new_router()
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        f.write("{not valid json")
        path = f.name
    monkeypatch.setattr(
        sp, 'get_state_paths',
        lambda: type('P', (), {'positions': path})()
    )
    try:
        assert r._get_position_symbols() == [], "损坏文件应返回 []"
    finally:
        os.unlink(path)
    print("  ✅ Case: positions 文件损坏 fail-safe []")
