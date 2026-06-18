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


# ───────────────── B-revised 门控 ─────────────────

def _run_rotation(router, selected_symbols, held_symbols, monkeypatch):
    """驱动一次轮换，返回捕获的 publish 列表 [(msg_type, payload), ...]"""
    captured = []

    async def _fake_publish(msg_type, payload, to="broadcast", symbol=None):
        captured.append((msg_type, payload))

    monkeypatch.setattr(router, 'publish', _fake_publish)
    monkeypatch.setattr(router, '_get_position_symbols', lambda: list(held_symbols))
    payload = {'selected': [{'symbol': s} for s in selected_symbols]}
    asyncio.run(router._handle_research_result(payload))
    return captured


def test_held_symbol_retained_not_closed(monkeypatch):
    """持仓标的被轮出研判选集 → 保留在 active、不发 close"""
    r = _new_router(close_held=False)
    cap = _run_rotation(r, ['SUI-USDT', 'ADA-USDT'], ['XLM-USDT'], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' not in closes, f"持仓标的 XLM 不应被平，实际 closes={closes}"

    updates = [p for t, p in cap if t == 'symbol_update']
    assert updates, "应发 symbol_update"
    active = updates[-1]['active_symbols']
    assert 'XLM-USDT' in active, f"持仓标的 XLM 应保留在 active，实际 {active}"
    assert 'XLM-USDT' not in updates[-1].get('removed', []), "XLM 不应在 removed"
    print("  ✅ Case: 持仓标的保留不平")


def test_unheld_symbol_still_closed(monkeypatch):
    """无持仓标的被轮出 → 照发 close（原行为）"""
    r = _new_router(close_held=False)
    cap = _run_rotation(r, ['ADA-USDT'], [], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' in closes and 'SUI-USDT' in closes, \
        f"无持仓标的应被平，实际 closes={closes}"
    print("  ✅ Case: 无持仓标的仍平")


def test_close_held_true_reverts_old_behavior(monkeypatch):
    """开关 true → 持仓标的也被强平（回退旧行为）"""
    r = _new_router(close_held=True)
    cap = _run_rotation(r, ['SUI-USDT', 'ADA-USDT'], ['XLM-USDT'], monkeypatch)

    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' in closes, f"开关 true 时持仓标的应被平，实际 closes={closes}"
    updates = [p for t, p in cap if t == 'symbol_update']
    assert 'XLM-USDT' not in updates[-1]['active_symbols'], "开关 true 时 XLM 不应保留 active"
    print("  ✅ Case: 开关 true 回退旧强平")


def test_retained_merged_into_active(monkeypatch):
    """多个持仓标的均保留进 active（即便超出研判新选）"""
    r = _new_router(close_held=False)
    r._active_symbols = ['XLM-USDT', 'SUI-USDT', 'DOGE-USDT']
    cap = _run_rotation(r, ['ADA-USDT'], ['XLM-USDT', 'DOGE-USDT'], monkeypatch)

    active = [p for t, p in cap if t == 'symbol_update'][-1]['active_symbols']
    assert 'ADA-USDT' in active, "新选应在 active"
    assert 'XLM-USDT' in active and 'DOGE-USDT' in active, \
        f"两个持仓标的均应保留，实际 {active}"
    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'SUI-USDT' in closes, "无持仓的 SUI 应被平"
    assert 'XLM-USDT' not in closes and 'DOGE-USDT' not in closes, "持仓标的不应被平"
    print("  ✅ Case: 多持仓标的合并进 active")


def test_held_and_reselected_appears_once(monkeypatch):
    """标的既持仓又被研判重新选中 → active 中只出现一次（不重复）"""
    r = _new_router(close_held=False)
    # XLM 既在新选集、又仍持仓
    cap = _run_rotation(r, ['XLM-USDT', 'ADA-USDT'], ['XLM-USDT'], monkeypatch)

    active = [p for t, p in cap if t == 'symbol_update'][-1]['active_symbols']
    assert active.count('XLM-USDT') == 1, f"XLM 应只出现一次，实际 {active}"
    closes = [p['symbol'] for t, p in cap if t == 'trade_decision' and p.get('action') == 'close']
    assert 'XLM-USDT' not in closes, "重新选中且持仓的标的不应被平"
    print("  ✅ Case: 既持仓又重选 → active 不重复")


def main():
    import pytest
    raise SystemExit(pytest.main([__file__, '-q']))


if __name__ == '__main__':
    main()
