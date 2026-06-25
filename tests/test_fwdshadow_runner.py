"""Task 4: fwdshadow_runner interval 参数化 + settle-when-determinable + dedup-by-bar-ts 测试。
TDD: 先写失败测试,实现后跑绿。"""
import importlib.util, os, sys
# 加载 repo 源 scripts/fwdshadow_runner.py 为模块
_spec = importlib.util.spec_from_file_location("fwdshadow_runner", os.path.join(os.path.dirname(__file__), "..", "scripts", "fwdshadow_runner.py"))
fr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fr)

def _bars(closes_hl):
    # closes_hl: list of (open_time_ms, high, low, close)
    return [{"open_time": t, "open": c, "high": h, "low": l, "close": c} for (t, h, l, c) in closes_hl]

def _rec(entry=100.0, sl=104.0, tp=88.0, max_hold_days=10, interval="1d"):
    # short: sl>entry>tp（与 record 一致）
    return {"detect_bar_open_time": 1000, "entry": entry, "stop_loss": sl, "take_profit": tp,
            "max_hold_days": max_hold_days, "interval": interval, "symbol": "X"}

def test_resolve_early_sl_settles_before_window_full():
    # 第2根触 SL(high>=104) → 立即结算 sl,不等满窗
    fut = _bars([(2000,101,99,100),(3000,105,100,104),(4000,103,99,100)])  # 仅3根,window=10
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "sl"

def test_resolve_early_tp_settles():
    fut = _bars([(2000,101,99,100),(3000,99,87,88)])  # 第2根触 TP(low<=88)
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "tp"

def test_resolve_no_exit_window_not_full_stays_unsettled():
    # 无退出 + bar 数 < window → None(不提前判 expired)
    fut = _bars([(2000,101,99,100),(3000,101,99,100),(4000,101,99,100)])
    assert fr.resolve_signal(_rec(), fut, window_bars=10) is None

def test_resolve_no_exit_window_full_expired():
    # 无退出 + 整窗满 → expired
    fut = _bars([(1000+i, 101, 99, 100) for i in range(10)])
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "expired"

def test_interval_windows_4h_scaled_by_bpd():
    atr_n, range_n, ma_n, window = fr._interval_windows("4h")
    assert (atr_n, range_n, ma_n, window) == (84, 120, 300, 60)
    assert fr._interval_windows("1d") == (14, 20, 50, 10)

def test_dedup_key_includes_bar_ts_and_interval():
    # 同 symbol 同 UTC 日不同 4h bar → 不同 key,不塌缩
    k1 = fr._dedup_key("X", 1000, "4h")
    k2 = fr._dedup_key("X", 1000 + 4*3600*1000, "4h")
    assert k1 != k2

def test_jsonl_path_per_interval():
    assert fr._log_path("1d").endswith("pattern_forward_shadow.jsonl")
    assert fr._log_path("4h").endswith("pattern_forward_shadow_4h.jsonl")
