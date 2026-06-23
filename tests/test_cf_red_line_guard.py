"""红线：任何交易决策/风控路径严禁读决策磁带/反事实 PnL/tick 产物。
（Judge 写 decision tape、collector 写 tick store 是允许的；禁止的是 *读* CF 产物做决策。）"""
import inspect


def _src(modpath):
    mod = __import__(modpath, fromlist=["x"])
    return inspect.getsource(mod)


def test_judge_does_not_read_cf_products():
    src = _src("agents.trading.judge")
    # Judge 可以写 decision tape（record_decision），但不得读反事实 PnL / honesty gate / tick
    assert "counterfactual_pnl" not in src
    assert "cf_honesty_gate" not in src
    assert "OneSecBarStore" not in src
    assert "klines_1s" not in src


def test_executor_does_not_read_cf_products():
    for mp in ["agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer"]:
        src = _src(mp)
        assert "counterfactual_pnl" not in src, mp
        assert "cf_honesty_gate" not in src, mp
        assert "decision_replay_tape" not in src, mp
        assert "klines_1s" not in src, mp


def test_halt_and_riskguard_do_not_read_tape():
    for mp in ["utils.halt_state", "agents.trading.portfolio_risk_guard"]:
        src = _src(mp)
        assert "decision_tape" not in src, mp
        assert "DecisionTape" not in src, mp


def test_decision_paths_do_not_read_replay_products():
    """L2：决策/风控路径严禁读回放 harness / driver / 状态快照产物。
    Judge 写 state_snapshot（经 _capture_state_snapshot，允许），但不得读回放产物做决策。"""
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer"]:
        src = _src(mp)
        # 精确匹配回放 harness 模块（注意 Judge 写 decision_replay_tape 是允许的写路径，不能误捕）
        assert "utils.decision_replay" not in src, mp
        assert "cf_replay_driver" not in src, mp
        assert "perturbation_replay" not in src, mp
        assert "cf_portfolio" not in src, mp
        assert "sequential_perturbation" not in src, mp
        assert "knob_sweep" not in src, mp
        assert "joint_knob_sweep" not in src, mp  # 多旋钮联合扫描 L4 扩展, observability-only
        # Judge 写快照用 _capture_state_snapshot；但任何路径都不得【读】回放磁带字段
        assert "state_snapshot_before_decision" not in src, mp


def test_decision_paths_do_not_read_shadow_products():
    """trend-entry-shadow-decision-logger：决策/风控路径严禁读影子产物。
    Judge 写影子日志（_schedule_shadow/_maybe_log_shadow）是允许的写路径，故不在禁列。"""
    for mp in ["agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst", "utils.halt_state"]:
        src = _src(mp)
        assert "shadow_decision_log" not in src, mp
        assert "shadow_decision_logger" not in src, mp


def test_decision_paths_do_not_read_ev_decouple_ab():
    """ev-decouple-forward-ab: 决策/风控路径严禁读解耦复核驱动产物（observability-only）。"""
    for mp in ["agents.trading.judge", "agents.trading.executor", "executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst"]:
        src = _src(mp)
        assert "cf_ev_decouple_ab" not in src, mp


def test_decision_paths_do_not_read_pattern_research():
    """daily-pattern-edge-lab: 决策/风控路径禁读形态研究模块/产物（observability-only）。"""
    forbidden = ("candlestick_patterns", "cf_pattern_edge_discovery", "pattern_forward_shadow")
    for mp in ["agents.trading.judge", "agents.trading.executor",
               "agents.trading.portfolio_risk_guard", "agents.trading.reviewer",
               "agents.trading.position_analyst"]:
        src = _src(mp)
        for f in forbidden:
            assert f not in src, f"{mp} 不得引用形态研究模块 {f}"
