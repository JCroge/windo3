"""P1-J: 统一成本模型 CostModel

集中化所有交易成本计算（手续费、资金费率、滑点），避免散落在
judge.py / executor.py / risk_manager.py 中不一致的常数。

设计原则：
- 默认取 Taker 费率（worst case），让风险估算偏保守
- funding 方向性正确：正费率做多付费 / 做空收费
- 所有公式可单测、可被 EV 门复用
- 可通过 env 覆盖默认值（不同账户费率档位不同）

公式速查：
    entry_fee   = notional × taker_fee
    exit_fee    = notional × taker_fee
    funding     = notional × |funding_rate| × periods × direction_mult
                  direction_mult = +1 if (long 且正费率) 或 (short 且负费率) 否则 -1
                  注意：本模型只返回"成本"（>=0），收益部分不当作 cost 计
    slippage    = notional × spread_pct × 0.5 (单边按 spread/2)
    round_trip  = entry_fee + exit_fee + max(0, funding) + 2 × slippage

可调参数（env，新增时再加）：
    TAKER_FEE_RATE  (默认 0.001 = 0.1%)
    MAKER_FEE_RATE  (默认 0.0005 = 0.05%)
    SLIPPAGE_BUFFER (默认 0.0005 = 0.05% 单边)
"""
import os


class CostModel:
    """统一交易成本估算器。

    单例式使用——多模块共享同一实例可保证费率一致。
    """

    def __init__(self,
                 taker_fee: float = None,
                 maker_fee: float = None,
                 slippage_buffer: float = None):
        self.taker_fee = taker_fee if taker_fee is not None else float(
            os.getenv('TAKER_FEE_RATE', 0.001)
        )
        self.maker_fee = maker_fee if maker_fee is not None else float(
            os.getenv('MAKER_FEE_RATE', 0.0005)
        )
        self.slippage_buffer = slippage_buffer if slippage_buffer is not None else float(
            os.getenv('SLIPPAGE_BUFFER', 0.0005)
        )

    # ── 手续费 ──
    def entry_fee(self, notional: float, is_maker: bool = False) -> float:
        """开仓手续费。"""
        rate = self.maker_fee if is_maker else self.taker_fee
        return max(0.0, notional) * rate

    def exit_fee(self, notional: float, is_maker: bool = False) -> float:
        """平仓手续费（含止损/止盈触发后的市价单也算 taker）。"""
        rate = self.maker_fee if is_maker else self.taker_fee
        return max(0.0, notional) * rate

    def round_trip_fee(self, notional: float, entry_maker: bool = False, exit_maker: bool = False) -> float:
        return self.entry_fee(notional, entry_maker) + self.exit_fee(notional, exit_maker)

    # ── 资金费率（永续合约特有）──
    def funding_cost(self, notional: float, funding_rate: float,
                     hold_hours: float, side: str) -> float:
        """资金费率成本（USDT，正数表示成本，负数被裁剪为 0）。

        side ∈ {'long', 'short'}
        funding_rate 是单期费率（OKX 每 8h 结算）
        hold_hours 是预期持仓时长

        规则：
            正费率（多头付空头）：long 付费、short 收费
            负费率（空头付多头）：short 付费、long 收费

        本函数只返回成本（>=0），收益方向不计为 cost。
        """
        if notional <= 0 or funding_rate == 0 or hold_hours <= 0:
            return 0.0
        periods = hold_hours / 8.0  # 每 8h 一期
        # direction_mult: +1=付费, -1=收费
        if side == 'long':
            direction_mult = 1 if funding_rate > 0 else -1
        elif side == 'short':
            direction_mult = 1 if funding_rate < 0 else -1
        else:
            direction_mult = 0
        cost = notional * abs(funding_rate) * periods * direction_mult
        return max(0.0, cost)

    def funding_pnl_signed(self, notional: float, funding_rate: float,
                          hold_hours: float, side: str) -> float:
        """资金费率带符号 PnL（正=收益 / 负=成本）。EV 计算时净盈利/净亏损分别套用。"""
        if notional <= 0 or funding_rate == 0 or hold_hours <= 0:
            return 0.0
        periods = hold_hours / 8.0
        if side == 'long':
            direction_mult = -1 if funding_rate > 0 else 1
        elif side == 'short':
            direction_mult = -1 if funding_rate < 0 else 1
        else:
            direction_mult = 0
        return notional * abs(funding_rate) * periods * direction_mult

    # ── 滑点 ──
    def slippage_cost(self, notional: float, spread_pct: float = None) -> float:
        """滑点成本估算。

        无 spread_pct 时用 slippage_buffer 兜底。
        spread 单位是 pct（如 0.001 = 0.1%），单边按 spread/2 估算。
        """
        if notional <= 0:
            return 0.0
        if spread_pct is None or spread_pct <= 0:
            spread_pct = self.slippage_buffer * 2  # buffer 是单边，spread 是双边
        return notional * spread_pct * 0.5

    def round_trip_slippage(self, notional: float, spread_pct: float = None) -> float:
        """开仓 + 平仓双向滑点。"""
        return 2 * self.slippage_cost(notional, spread_pct)

    # ── 综合 round-trip 成本 ──
    def round_trip_cost(self, notional: float, funding_rate: float = 0,
                        hold_hours: float = 8, side: str = 'long',
                        spread_pct: float = None,
                        entry_maker: bool = False, exit_maker: bool = False) -> dict:
        """完整 round-trip 成本拆解。返回 dict 含各组成部分。

        关键：funding 用 funding_cost（>=0），EV 估算时不抵扣"被收费"的好处，
        让风险预算偏保守。如果调用方明确想看带符号 PnL，用 funding_pnl_signed。
        """
        ef = self.entry_fee(notional, entry_maker)
        xf = self.exit_fee(notional, exit_maker)
        fc = self.funding_cost(notional, funding_rate, hold_hours, side)
        sl = self.round_trip_slippage(notional, spread_pct)
        total = ef + xf + fc + sl
        return {
            'entry_fee': ef,
            'exit_fee': xf,
            'funding_cost': fc,
            'slippage_cost': sl,
            'total_cost': total,
            'notional': notional,
            'breakdown_pct': total / notional if notional > 0 else 0,
        }

    # ── PnL 计算辅助 ──
    def realized_pnl(self, side: str, entry_price: float, exit_price: float,
                     amount_usdt: float, leverage: int = 1,
                     funding_rate: float = 0, hold_hours: float = 0) -> dict:
        """计算实际平仓盈亏（扣除所有成本）。

        amount_usdt 是保证金（margin），notional = amount_usdt × leverage
        返回 dict 含 gross_pnl / fees / funding / net_pnl
        """
        if entry_price <= 0:
            return {'net_pnl': 0, 'gross_pnl': 0, 'fees': 0, 'funding': 0}
        notional = amount_usdt * leverage
        if side == 'long':
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
        gross_pnl = notional * pnl_pct
        fees = self.round_trip_fee(notional)
        funding = self.funding_cost(notional, funding_rate, hold_hours, side)
        net_pnl = gross_pnl - fees - funding
        return {
            'gross_pnl': gross_pnl,
            'fees': fees,
            'funding': funding,
            'net_pnl': net_pnl,
            'pnl_pct_of_margin': net_pnl / amount_usdt if amount_usdt > 0 else 0,
        }


# ═══ 默认共享实例 ═══
_default_model = None


def get_default_cost_model() -> CostModel:
    """获取进程级共享的 CostModel 实例（懒初始化）。"""
    global _default_model
    if _default_model is None:
        _default_model = CostModel()
    return _default_model
