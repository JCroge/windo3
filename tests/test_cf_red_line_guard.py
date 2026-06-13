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
