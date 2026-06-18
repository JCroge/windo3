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
