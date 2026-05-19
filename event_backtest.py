#!/usr/bin/env python3
"""事件驱动回测引擎 (P1-I)

与简化版 backtest.py 共存，目标是和实盘流水线的关键机制对齐：
    - long + short 双向
    - SL / TP 触发（不只是信号反转出场）
    - 杠杆（统一风险预算：margin × leverage = notional）
    - 资金费率 (CostModel)
    - 滑点 (CostModel)
    - 分批止盈 + Break-Even + Trailing Stop (Phase 7)
    - StoplossGuard 递增冷却（Phase 6p）
    - 入场门槛 / RSI cap / HTF 一致性（与 Judge 对齐）

输入: enriched DataFrame, 必须列：
    open / high / low / close / volume / open_time
    entry_long / entry_short / exit_long / exit_short
    atr  (用于 trailing stop 距离 & SL 锚点)
    rsi  (用于 RSI cap & 极端值禁区)
可选列：
    htf_bias   ('bullish'|'bearish'|'neutral')
    funding_rate  (单期费率，每 8h)
    ma_aligned_long / ma_aligned_short

输出: dict 含 trades / metrics / equity_curve

与 backtest.py 关系：
    backtest.py 仍用于快速参数搜索（向量化、long-only）
    event_backtest.py 用于策略验证（更接近实盘）
"""

import math
from typing import Dict, List, Optional
import pandas as pd

try:
    from utils.cost_model import get_default_cost_model
    _COST_MODEL = get_default_cost_model()
except Exception:
    _COST_MODEL = None


class EventBacktest:
    """事件驱动回测引擎。

    Key abstractions:
        - position: dict with full plan (entry/sl/tp/leverage/original_sl/highest/lowest/tp_filled)
        - sl_history: list[ts] for StoplossGuard cooldown
        - equity: running balance
    """

    # OKX 允许的杠杆档位
    LEVERAGE_LADDER = [1, 2, 3, 5, 10, 20]

    # 递增冷却（秒），对应 SL 次数：1->300, 2->600, 3->1200, 4+->3600
    SL_COOLDOWN_TIERS = [300, 600, 1200, 3600]
    SL_GUARD_WINDOW_HOURS = 4

    def __init__(self,
                 initial_capital: float = 1000,
                 risk_per_trade_pct: float = 0.05,
                 margin_pct: float = 0.10,
                 max_margin: float = 10.0,
                 rr_floor: float = 1.5,
                 entry_threshold: int = 35,
                 entry_only_on_signal: bool = True,
                 post_close_cooldown_bars: int = 5,
                 enable_partial_tp: bool = True,
                 enable_trailing: bool = True,
                 enable_sl_guard: bool = True,
                 verbose: bool = False):
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct  # 单笔最大亏损占余额比
        self.margin_pct = margin_pct                  # 单笔保证金占余额比
        self.max_margin = max_margin
        self.rr_floor = rr_floor
        self.entry_threshold = entry_threshold
        # entry_only_on_signal=True 时，只在 entry_long/entry_short=1 的 K 线尝试入场（事件驱动）
        # False 时每根 K 线都重新评分（向量化模式，可能过度开仓）
        self.entry_only_on_signal = entry_only_on_signal
        # 平仓后冷却 N 根 K 线再尝试开仓（模拟实盘 Judge 的开仓冷却）
        self.post_close_cooldown_bars = post_close_cooldown_bars
        self.enable_partial_tp = enable_partial_tp
        self.enable_trailing = enable_trailing
        self.enable_sl_guard = enable_sl_guard
        self.verbose = verbose

        self.cm = _COST_MODEL

    # ────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────

    def run(self, df: pd.DataFrame, symbol: str = "TEST") -> Dict:
        """运行回测。"""
        df = df.copy().reset_index(drop=True)
        self._validate_input(df)

        equity = self.initial_capital
        position: Optional[Dict] = None
        trades: List[Dict] = []
        equity_curve: List[Dict] = []
        sl_history: List[float] = []  # ts 列表（小时为单位）
        last_close_idx: int = -10000  # 平仓后冷却用

        for i in range(len(df)):
            row = df.iloc[i]
            ts_h = i  # 用索引作为时间（每根 K 线 = 1h）

            # 1. 检查现有持仓的 SL/TP/Trailing
            if position is not None:
                exit_info = self._check_position_exits(position, row, i, df)
                if exit_info is not None:
                    pnl_info = self._close_position(position, exit_info, row)
                    equity += pnl_info['net_pnl']
                    trades.append({
                        **pnl_info,
                        'symbol': symbol,
                        'entry_idx': position['entry_idx'],
                        'exit_idx': i,
                        'exit_reason': exit_info['reason'],
                        'direction': position['direction'],
                        'leverage': position['leverage'],
                    })
                    if exit_info['reason'] == 'sl':
                        sl_history.append(ts_h)
                    position = None
                    last_close_idx = i

                # 部分止盈：仓位继续，但已调整
                elif self.enable_partial_tp:
                    position = self._maybe_partial_tp(position, row)
                    position = self._maybe_break_even(position, row)
                    if self.enable_trailing and position.get('tp_filled', 0) >= 1:
                        position = self._maybe_trail(position, row)

            # 2. 检查入场信号（无持仓时）
            if position is None:
                # 平仓后冷却
                if i - last_close_idx < self.post_close_cooldown_bars:
                    pass
                # StoplossGuard 冷却
                elif self.enable_sl_guard and self._in_sl_cooldown(sl_history, ts_h):
                    pass
                # entry_only_on_signal: 只在 rule_signal=1 的 K 线尝试
                elif self.entry_only_on_signal and not (
                        int(row.get('entry_long', 0)) or int(row.get('entry_short', 0))):
                    pass
                else:
                    direction = self._check_entry_signal(row)
                    if direction is not None and i + 1 < len(df):
                        plan = self._build_plan(row, direction, equity)
                        if plan is not None:
                            # 3. 记录权益曲线（在开仓前，当前 K 线不含下一根才入场的仓位）
                            equity_curve.append({
                                'idx': i,
                                'ts': row.get('open_time', i),
                                'close': row['close'],
                                'equity': equity + self._mark_to_market(position, row),
                                'has_position': position is not None,
                            })
                            # 下一根 K 线开盘价入场
                            next_row = df.iloc[i + 1]
                            entry_price = next_row['open']
                            position = self._open_position(plan, entry_price, i + 1, next_row)
                            continue

            # 3. 记录权益曲线
            equity_curve.append({
                'idx': i,
                'ts': row.get('open_time', i),
                'close': row['close'],
                'equity': equity + self._mark_to_market(position, row),
                'has_position': position is not None,
            })

        # 强制平掉最后未平仓位（用最后收盘价）
        if position is not None:
            last_row = df.iloc[-1]
            exit_info = {'price': last_row['close'], 'reason': 'end_of_data'}
            pnl_info = self._close_position(position, exit_info, last_row)
            equity += pnl_info['net_pnl']
            trades.append({
                **pnl_info,
                'symbol': symbol,
                'entry_idx': position['entry_idx'],
                'exit_idx': len(df) - 1,
                'exit_reason': 'end_of_data',
                'direction': position['direction'],
                'leverage': position['leverage'],
            })

        return {
            'symbol': symbol,
            'trades': trades,
            'equity_curve': equity_curve,
            'final_equity': equity,
            **self._calc_metrics(trades, equity_curve),
        }

    # ────────────────────────────────────────────
    # Entry decision
    # ────────────────────────────────────────────

    def _check_entry_signal(self, row) -> Optional[str]:
        """返回 'long'|'short'|None。"""
        score = self._compute_score(row)
        if score >= self.entry_threshold:
            return 'long'
        if score <= -self.entry_threshold:
            return 'short'
        return None

    def _compute_score(self, row) -> float:
        """简化版评分：rule_signal (±35) + ma_aligned (±20) + RSI cap + HTF resonance。

        与 Judge._compute_score 的核心逻辑对齐：
            - rule_signal 是主驱动
            - RSI 极端值禁区（>=70 禁多 / <=30 禁空）
            - HTF 一致时 RSI cap 放宽
        """
        score = 0.0
        rsi = float(row.get('rsi', 50))
        htf_bias = str(row.get('htf_bias', 'neutral')).lower()

        # rule_signal: ±35
        if int(row.get('entry_long', 0)) == 1:
            score += 35
        if int(row.get('entry_short', 0)) == 1:
            score -= 35

        # ma_aligned: ±20
        if int(row.get('ma_aligned_long', 0)) == 1:
            score += 20
        if int(row.get('ma_aligned_short', 0)) == 1:
            score -= 20

        # RSI 加减分（cap 受 HTF 影响）
        cap_long = 25 if htf_bias == 'bullish' else 15
        cap_short = -25 if htf_bias == 'bearish' else -15

        if rsi < 30:
            # 超卖偏多
            rsi_score = (30 - rsi) * 1.5
            score += min(rsi_score, cap_long)
        elif rsi > 70:
            # 超买偏空
            rsi_score = (rsi - 70) * 1.5
            score -= min(rsi_score, abs(cap_short))

        # 硬性禁区（与 Judge 一致：RSI>=70 禁多、<=30 禁空）
        if rsi >= 70 and score > 0:
            score = min(score, 15)  # 多头方向被压制
        if rsi <= 30 and score < 0:
            score = max(score, -15)  # 空头方向被压制

        # HTF 共振加分
        if htf_bias == 'bullish' and score > 0:
            score += 10
        elif htf_bias == 'bearish' and score < 0:
            score -= 10

        return score

    def _build_plan(self, row, direction: str, equity: float) -> Optional[Dict]:
        """生成入场计划：SL / TP / 杠杆 / 仓位 (统一风险预算)。"""
        price = float(row['close'])
        atr = float(row.get('atr', 0))
        if atr <= 0 or price <= 0:
            return None

        # 1. SL 距离：基于 ATR，最多 2.5×ATR 或 5%
        atr_pct = atr / price
        sl_dist_pct = min(2.5 * atr_pct, 0.05)
        sl_dist_pct = max(sl_dist_pct, 0.005)  # 至少 0.5%（防 SL=入场价）

        # 2. TP 距离：至少 1.5×SL
        tp_dist_pct = sl_dist_pct * self.rr_floor

        if direction == 'long':
            sl = price * (1 - sl_dist_pct)
            tp = price * (1 + tp_dist_pct)
        else:
            sl = price * (1 + sl_dist_pct)
            tp = price * (1 - tp_dist_pct)

        # 3. 仓位：margin = min(equity * 10%, max_margin)
        margin = min(equity * self.margin_pct, self.max_margin)
        if margin < 1.0:
            return None

        # 4. 杠杆 = max_loss / (margin × sl_dist) 圆整到 ladder
        max_loss = equity * self.risk_per_trade_pct
        raw_leverage = max_loss / (margin * sl_dist_pct)
        leverage = 1
        for lev in self.LEVERAGE_LADDER:
            if lev <= raw_leverage:
                leverage = lev
        leverage = min(leverage, 20)

        # 5. R:R 检查（含成本）
        notional = margin * leverage
        gross_profit = notional * tp_dist_pct
        gross_loss = notional * sl_dist_pct
        if self.cm:
            fees = self.cm.round_trip_fee(notional)
            funding_rate = float(row.get('funding_rate', 0))
            funding = self.cm.funding_cost(notional, funding_rate, 8, direction)
            net_profit = gross_profit - fees - funding
            net_loss = gross_loss + fees + funding
        else:
            net_profit = gross_profit * 0.998
            net_loss = gross_loss * 1.002

        rr = net_profit / net_loss if net_loss > 0 else 0
        if rr < 1.0:  # 极端情况完全无利润
            return None

        # tp_levels 用于分批止盈（与 Phase 7 一致）
        # tp1 = 入场 + 0.7×TP距离，tp2 = TP（满仓出）
        tp1_dist = tp_dist_pct * 0.7
        if direction == 'long':
            tp1 = price * (1 + tp1_dist)
            tp2 = tp
        else:
            tp1 = price * (1 - tp1_dist)
            tp2 = tp

        return {
            'direction': direction,
            'entry_price': price,  # 决策时价格，真实入场用 next open
            'stop_loss': sl,
            'take_profit': tp,
            'tp_levels': [tp1, tp2],
            'leverage': leverage,
            'margin': margin,
            'notional': notional,
            'atr_pct': atr_pct,
            'sl_dist_pct': sl_dist_pct,
            'rr': rr,
        }

    # ────────────────────────────────────────────
    # Position lifecycle
    # ────────────────────────────────────────────

    def _open_position(self, plan: Dict, entry_price: float, entry_idx: int, row) -> Dict:
        """开仓：用真实入场价（next open）重算 SL/TP 距离。"""
        direction = plan['direction']
        sl_dist_pct = plan['sl_dist_pct']
        tp_dist_pct = sl_dist_pct * self.rr_floor

        if direction == 'long':
            sl = entry_price * (1 - sl_dist_pct)
            tp = entry_price * (1 + tp_dist_pct)
            tp1 = entry_price * (1 + tp_dist_pct * 0.7)
        else:
            sl = entry_price * (1 + sl_dist_pct)
            tp = entry_price * (1 - tp_dist_pct)
            tp1 = entry_price * (1 - tp_dist_pct * 0.7)

        return {
            'direction': direction,
            'entry_price': entry_price,
            'entry_idx': entry_idx,
            'stop_loss': sl,
            'original_sl': sl,
            'take_profit': tp,
            'tp_levels': [tp1, tp],
            'leverage': plan['leverage'],
            'margin': plan['margin'],
            'notional': plan['margin'] * plan['leverage'],
            'original_amount': plan['margin'],
            'atr_pct': plan['atr_pct'],
            'sl_dist_pct': sl_dist_pct,
            'highest_price': entry_price,
            'lowest_price': entry_price,
            'tp_filled': 0,  # 0=未触发, 1=tp1 已平 50%, 2=tp2 已平 25%
            'be_moved': False,
            'partial_realized': 0.0,  # 累计已实现盈亏（来自 partial TP）
            'funding_rate': float(row.get('funding_rate', 0)),
        }

    def _check_position_exits(self, position: Dict, row, idx: int, df) -> Optional[Dict]:
        """检查 SL / TP 是否触发。返回 {price, reason} 或 None。"""
        direction = position['direction']
        sl = position['stop_loss']
        tp = position['take_profit']
        high = float(row['high'])
        low = float(row['low'])

        # 同根 K 线 SL 和 TP 都触发时，假设最不利情况（SL 先触发）
        if direction == 'long':
            if low <= sl:
                return {'price': sl, 'reason': 'sl'}
            if high >= tp:
                return {'price': tp, 'reason': 'tp'}
            # 反向信号出场
            if int(row.get('exit_long', 0)) == 1 and idx + 1 < len(df):
                return {'price': float(df.iloc[idx + 1]['open']), 'reason': 'signal'}
        else:
            if high >= sl:
                return {'price': sl, 'reason': 'sl'}
            if low <= tp:
                return {'price': tp, 'reason': 'tp'}
            if int(row.get('exit_short', 0)) == 1 and idx + 1 < len(df):
                return {'price': float(df.iloc[idx + 1]['open']), 'reason': 'signal'}

        return None

    def _maybe_partial_tp(self, position: Dict, row) -> Dict:
        """检查 tp1/tp2 触发并平 50% / 25%。"""
        if position['tp_filled'] >= 2:
            return position
        direction = position['direction']
        tp_levels = position['tp_levels']
        high = float(row['high'])
        low = float(row['low'])

        # tp1 (50%)
        if position['tp_filled'] == 0:
            tp1 = tp_levels[0]
            hit = (direction == 'long' and high >= tp1) or (direction == 'short' and low <= tp1)
            if hit:
                # 平 50% 仓位，记录已实现盈亏
                close_amount = position['notional'] * 0.5
                if direction == 'long':
                    pnl_gross = (tp1 - position['entry_price']) * (close_amount / position['entry_price'])
                else:
                    pnl_gross = (position['entry_price'] - tp1) * (close_amount / position['entry_price'])
                if self.cm:
                    fees = self.cm.entry_fee(close_amount) + self.cm.exit_fee(close_amount)
                else:
                    fees = close_amount * 0.002
                position['partial_realized'] += pnl_gross - fees
                position['notional'] *= 0.5
                position['margin'] *= 0.5
                position['tp_filled'] = 1
                # SL 移到 entry + 0.5R (long) / entry - 0.5R (short)
                sl_dist = position['sl_dist_pct'] * position['entry_price']
                if direction == 'long':
                    new_sl = position['entry_price'] + sl_dist * 0.5
                    position['stop_loss'] = max(position['stop_loss'], new_sl)
                else:
                    new_sl = position['entry_price'] - sl_dist * 0.5
                    position['stop_loss'] = min(position['stop_loss'], new_sl)

        return position

    def _maybe_break_even(self, position: Dict, row) -> Dict:
        """浮盈 ≥ 1R 时 SL 移到 entry + 手续费（消除亏损风险）。"""
        if position['be_moved']:
            return position
        entry = position['entry_price']
        sl_dist = position['sl_dist_pct'] * entry  # 1R 价差
        high = float(row['high'])
        low = float(row['low'])
        direction = position['direction']

        if direction == 'long':
            if high - entry >= sl_dist:
                # 移到 entry + 0.1% 手续费缓冲
                new_sl = entry * 1.001
                if new_sl > position['stop_loss']:
                    position['stop_loss'] = new_sl
                    position['be_moved'] = True
        else:
            if entry - low >= sl_dist:
                new_sl = entry * 0.999
                if new_sl < position['stop_loss']:
                    position['stop_loss'] = new_sl
                    position['be_moved'] = True

        return position

    def _maybe_trail(self, position: Dict, row) -> Dict:
        """Trailing stop: tp_filled>=1 后激活，距离 max(atr_pct, R*0.5)。"""
        direction = position['direction']
        high = float(row['high'])
        low = float(row['low'])
        atr_pct = position['atr_pct']
        trail_pct = max(atr_pct, position['sl_dist_pct'] * 0.5)

        if direction == 'long':
            if high > position['highest_price']:
                position['highest_price'] = high
            new_sl = position['highest_price'] * (1 - trail_pct)
            if new_sl > position['stop_loss']:
                position['stop_loss'] = new_sl
        else:
            if low < position['lowest_price'] or position['lowest_price'] == position['entry_price']:
                position['lowest_price'] = low
            new_sl = position['lowest_price'] * (1 + trail_pct)
            if new_sl < position['stop_loss']:
                position['stop_loss'] = new_sl

        return position

    def _close_position(self, position: Dict, exit_info: Dict, row) -> Dict:
        """平仓计算 PnL。"""
        exit_price = exit_info['price']
        direction = position['direction']
        margin = position['margin']
        leverage = position['leverage']
        entry = position['entry_price']

        # 假设平均持仓 8 小时（每根 K 线 1h，简化）
        hold_hours = 8
        funding_rate = position.get('funding_rate', 0)

        if self.cm:
            pnl = self.cm.realized_pnl(
                side=direction,
                entry_price=entry,
                exit_price=exit_price,
                amount_usdt=margin,
                leverage=leverage,
                funding_rate=funding_rate,
                hold_hours=hold_hours,
            )
            net_pnl = pnl['net_pnl'] + position.get('partial_realized', 0)
            gross_pnl = pnl['gross_pnl']
            fees = pnl['fees']
            funding = pnl['funding']
        else:
            notional = margin * leverage
            if direction == 'long':
                gross_pnl = (exit_price - entry) * (notional / entry)
            else:
                gross_pnl = (entry - exit_price) * (notional / entry)
            fees = notional * 0.002
            funding = 0
            net_pnl = gross_pnl - fees + position.get('partial_realized', 0)

        return {
            'entry_price': entry,
            'exit_price': exit_price,
            'gross_pnl': gross_pnl,
            'fees': fees,
            'funding': funding,
            'partial_realized': position.get('partial_realized', 0),
            'net_pnl': net_pnl,
            'pnl_pct_of_margin': net_pnl / position['original_amount'] if position['original_amount'] > 0 else 0,
        }

    def _mark_to_market(self, position: Optional[Dict], row) -> float:
        """计算持仓浮盈/浮亏（不含成本）。"""
        if position is None:
            return 0
        notional = position['notional']
        entry = position['entry_price']
        close = float(row['close'])
        if position['direction'] == 'long':
            return (close - entry) * (notional / entry) + position.get('partial_realized', 0)
        else:
            return (entry - close) * (notional / entry) + position.get('partial_realized', 0)

    # ────────────────────────────────────────────
    # StoplossGuard
    # ────────────────────────────────────────────

    def _in_sl_cooldown(self, sl_history: List[float], now_h: float) -> bool:
        """递增冷却：4h 窗口内连续 SL 次数决定冷却时长。"""
        if not sl_history:
            return False
        # 过滤窗口内的 SL
        recent = [t for t in sl_history if now_h - t < self.SL_GUARD_WINDOW_HOURS]
        if not recent:
            return False
        last_sl = recent[-1]
        count = len(recent)
        tier = min(count - 1, len(self.SL_COOLDOWN_TIERS) - 1)
        cooldown_h = self.SL_COOLDOWN_TIERS[tier] / 3600
        return now_h - last_sl < cooldown_h

    # ────────────────────────────────────────────
    # Metrics
    # ────────────────────────────────────────────

    def _calc_metrics(self, trades: List[Dict], equity_curve: List[Dict]) -> Dict:
        if not trades:
            return {
                'total_trades': 0, 'win_rate': 0, 'profit_factor': 0,
                'total_pnl': 0, 'max_drawdown_pct': 0, 'sharpe': 0,
                'avg_holding_bars': 0, 'sl_count': 0, 'tp_count': 0,
            }
        df = pd.DataFrame(trades)
        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] <= 0]
        total_win = wins['net_pnl'].sum() if len(wins) else 0
        total_loss = abs(losses['net_pnl'].sum()) if len(losses) else 0
        pf = total_win / total_loss if total_loss > 0 else float('inf')

        # Equity drawdown
        eq = pd.DataFrame(equity_curve)
        if len(eq):
            eq['running_max'] = eq['equity'].cummax()
            eq['drawdown'] = (eq['running_max'] - eq['equity']) / eq['running_max']
            max_dd = eq['drawdown'].max() * 100
        else:
            max_dd = 0

        # Sharpe (bar-level returns)
        if len(eq) > 1:
            returns = eq['equity'].pct_change().dropna()
            if returns.std() > 0:
                sharpe = (returns.mean() / returns.std()) * math.sqrt(24 * 365)  # 假设小时级
            else:
                sharpe = 0
        else:
            sharpe = 0

        return {
            'total_trades': len(df),
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'win_rate': len(wins) / len(df) * 100,
            'profit_factor': pf,
            'total_pnl': float(df['net_pnl'].sum()),
            'avg_pnl': float(df['net_pnl'].mean()),
            'max_drawdown_pct': float(max_dd),
            'sharpe': float(sharpe),
            'sl_count': int((df['exit_reason'] == 'sl').sum()),
            'tp_count': int((df['exit_reason'] == 'tp').sum()),
            'signal_exit_count': int((df['exit_reason'] == 'signal').sum()),
            'avg_holding_bars': float((df['exit_idx'] - df['entry_idx']).mean()),
        }

    # ────────────────────────────────────────────
    # Validation
    # ────────────────────────────────────────────

    def _validate_input(self, df: pd.DataFrame):
        required = ['open', 'high', 'low', 'close', 'volume',
                    'entry_long', 'entry_short', 'exit_long', 'exit_short',
                    'atr', 'rsi']
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"event_backtest 缺少必要列: {missing}")
        if len(df) < 2:
            raise ValueError("数据量太少（至少 2 根 K 线）")


# ────────────────────────────────────────────
# 便捷函数：从优化好的策略 dataframe 跑一遍
# ────────────────────────────────────────────

def enrich_with_atr_htf(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """补齐 ATR / HTF bias 列（如果 strategy 没有的话）。"""
    df = df.copy()
    if 'atr' not in df.columns:
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(atr_period).mean().bfill().fillna(0)
    if 'htf_bias' not in df.columns:
        # 简化：用 ma_slow 斜率
        if 'ma_slow' in df.columns:
            df['ma_slow_slope'] = df['ma_slow'].diff(5)
            df['htf_bias'] = df['ma_slow_slope'].apply(
                lambda x: 'bullish' if x > 0 else ('bearish' if x < 0 else 'neutral')
            )
        else:
            df['htf_bias'] = 'neutral'
    if 'ma_aligned_long' not in df.columns and 'ma_fast' in df.columns and 'ma_slow' in df.columns:
        aligned = (df['ma_fast'] > df['ma_slow']).fillna(False).astype(bool)
        # 连续 3 根 K 线对齐
        df['ma_aligned_long'] = (aligned & aligned.shift(1, fill_value=False) & aligned.shift(2, fill_value=False)).astype(int)
        not_aligned = ~aligned
        df['ma_aligned_short'] = (not_aligned & not_aligned.shift(1, fill_value=False) & not_aligned.shift(2, fill_value=False)).astype(int)
    elif 'ma_aligned_long' not in df.columns:
        df['ma_aligned_long'] = 0
        df['ma_aligned_short'] = 0
    if 'funding_rate' not in df.columns:
        df['funding_rate'] = 0
    return df


if __name__ == '__main__':
    # 简单 smoke test
    import pandas as pd
    import numpy as np
    np.random.seed(42)
    n = 300
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open_time': pd.date_range('2026-01-01', periods=n, freq='1h'),
        'open': prices,
        'high': prices * 1.005,
        'low': prices * 0.995,
        'close': prices,
        'volume': 1000 + np.random.rand(n) * 500,
    })
    # 简单信号：MA 7/25 交叉
    df['ma_fast'] = df['close'].rolling(7).mean()
    df['ma_slow'] = df['close'].rolling(25).mean()
    df['rsi'] = 50  # 简化
    cross_up = (df['ma_fast'] > df['ma_slow']) & (df['ma_fast'].shift(1) <= df['ma_slow'].shift(1))
    cross_down = (df['ma_fast'] < df['ma_slow']) & (df['ma_fast'].shift(1) >= df['ma_slow'].shift(1))
    df['entry_long'] = cross_up.astype(int)
    df['entry_short'] = cross_down.astype(int)
    df['exit_long'] = cross_down.astype(int)
    df['exit_short'] = cross_up.astype(int)
    df = enrich_with_atr_htf(df).fillna(0)

    eb = EventBacktest(initial_capital=100, verbose=False)
    result = eb.run(df, symbol='SMOKE')
    print(f"Smoke test: {result['total_trades']} trades, "
          f"final_equity={result['final_equity']:.2f}, "
          f"win_rate={result['win_rate']:.1f}%, "
          f"pf={result['profit_factor']:.2f}, "
          f"max_dd={result['max_drawdown_pct']:.2f}%, "
          f"sharpe={result['sharpe']:.2f}")
    print(f"  SL: {result['sl_count']}, TP: {result['tp_count']}, signal: {result['signal_exit_count']}")
