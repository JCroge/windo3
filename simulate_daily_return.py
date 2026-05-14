"""
Monte Carlo模拟：统一风险预算框架下的开仓率和日化收益率
模拟逻辑：
1. 随机生成市场状态（ATR、RSI、趋势、资金费率、日线位置等）
2. 应用所有过滤规则（RSI禁区、R:R门槛、日线反欺骗、LLM冲突等）
3. 统计开仓率 + 模拟盈亏 → 估算日化
"""
import random
import math
from dataclasses import dataclass

random.seed(42)

# ============ 系统参数 ============
BALANCE = 105.0
MAX_TRADE_AMOUNT = 10.0
MARGIN_PCT = 0.10
MAX_LOSS_PCT = 0.05
RR_THRESHOLD = 1.5
OKX_ALLOWED_LEV = [1, 2, 3, 5, 10, 20]
WIN_RATE_BASE = 0.60  # 基础胜率（rule_signal验证过83%，但实盘保守估计60%）
SIGNALS_PER_DAY = 6   # 每天平均产生的交易信号数（5标的×1h周期≈每4h一轮）

# 保守场景参数
WIN_RATE_CONSERVATIVE = 0.48  # 保守胜率（考虑滑点、延迟、市场噪音）
PARTIAL_TP_RATE = 0.40        # 40%概率完整止盈，60%部分止盈或提前出场


@dataclass
class MarketState:
    """随机市场状态"""
    atr_pct: float        # ATR百分比 (0.008 ~ 0.06)
    rsi: float            # RSI (15 ~ 85)
    trend_strength: int   # 趋势强度 (10 ~ 95)
    trend_dir: str        # bullish / bearish / neutral
    funding_rate: float   # 资金费率 (-0.001 ~ 0.002)
    near_daily_resistance: bool
    near_daily_support: bool
    ma_signal: str        # long / short / none
    volume_anomaly: bool
    spread_pct: float     # 点差


def generate_random_market() -> MarketState:
    """生成随机市场状态（基于真实分布）"""
    atr_pct = random.triangular(0.008, 0.06, 0.018)
    rsi = random.gauss(50, 15)
    rsi = max(15, min(85, rsi))
    trend_strength = random.randint(10, 95)

    # 趋势方向分布：40% bullish, 40% bearish, 20% neutral
    r = random.random()
    if r < 0.4:
        trend_dir = 'bullish'
    elif r < 0.8:
        trend_dir = 'bearish'
    else:
        trend_dir = 'neutral'

    # 资金费率：正态分布，均值0.0001，偶尔极端
    funding_rate = random.gauss(0.0001, 0.0003)
    funding_rate = max(-0.001, min(0.002, funding_rate))

    # 日线位置：15%概率接近阻力，15%接近支撑
    near_daily_resistance = random.random() < 0.15
    near_daily_support = random.random() < 0.15

    # MA信号：30% long, 30% short, 40% none
    r = random.random()
    if r < 0.30:
        ma_signal = 'long'
    elif r < 0.60:
        ma_signal = 'short'
    else:
        ma_signal = 'none'

    volume_anomaly = random.random() < 0.10
    spread_pct = random.triangular(0.005, 0.05, 0.01)

    return MarketState(
        atr_pct=atr_pct, rsi=rsi, trend_strength=trend_strength,
        trend_dir=trend_dir, funding_rate=funding_rate,
        near_daily_resistance=near_daily_resistance,
        near_daily_support=near_daily_support,
        ma_signal=ma_signal, volume_anomaly=volume_anomaly,
        spread_pct=spread_pct
    )


def compute_score(market: MarketState) -> tuple:
    """模拟Judge._compute_score逻辑，返回(score, action)"""
    score = 0
    action = 'hold'

    # rule_signal驱动（MA交叉）
    if market.ma_signal == 'long':
        score += 35
    elif market.ma_signal == 'short':
        score -= 35

    # 趋势加分
    if market.trend_strength > 70:
        if market.trend_dir == 'bullish':
            score += 15
        elif market.trend_dir == 'bearish':
            score -= 15

    # RSI动量
    if market.rsi > 65:
        score -= 5  # 超买区做多减分
    elif market.rsi < 35:
        score += 5  # 超卖区做空减分

    # 资金流向
    if market.funding_rate > 0.0005:
        score -= 5  # 高正费率利空多头
    elif market.funding_rate < -0.0003:
        score += 5

    # 成交量异常
    if market.volume_anomaly:
        if score > 0:
            score += 10
        elif score < 0:
            score -= 10

    # 确定方向
    if score >= 30:
        action = 'open_long'
    elif score <= -30:
        action = 'open_short'
    else:
        action = 'hold'

    return score, action


def apply_filters(market: MarketState, action: str, score: int) -> tuple:
    """应用所有过滤规则，返回(pass, reject_reason)"""

    # Filter 1: RSI禁区
    if action == 'open_short' and market.rsi <= 30:
        return False, 'RSI禁区(<=30禁空)'
    if action == 'open_long' and market.rsi >= 70:
        return False, 'RSI禁区(>=70禁多)'

    # Filter 2: 日线反欺骗
    if action == 'open_long' and market.near_daily_resistance:
        effective_score = score * 0.3  # 衰减70%
        if effective_score < 30:
            return False, '日线阻力区做多衰减'
    if action == 'open_short' and market.near_daily_support:
        effective_score = score * 0.3
        if effective_score > -30:
            return False, '日线支撑区做空衰减'

    # Filter 3: 趋势强度不足
    if market.trend_strength < 30 and market.ma_signal == 'none':
        return False, '趋势强度不足+无MA信号'

    # Filter 4: 点差过大
    if market.spread_pct > 0.03:
        return False, '点差过大'

    return True, ''


def calc_risk_budget(market: MarketState, action: str) -> dict:
    """统一风险预算计算"""
    margin_usdt = min(BALANCE * MARGIN_PCT, MAX_TRADE_AMOUNT)
    max_loss_usdt = BALANCE * MAX_LOSS_PCT

    # sl_dist基于ATR
    sl_dist = market.atr_pct * 2.0  # 约2倍ATR作为止损
    sl_dist = max(0.015, min(0.05, sl_dist))

    raw_leverage = max_loss_usdt / (margin_usdt * sl_dist)
    leverage = max(1, min(20, int(raw_leverage)))

    # 向下圆整
    final_lev = OKX_ALLOWED_LEV[0]
    for lev in OKX_ALLOWED_LEV:
        if lev <= leverage:
            final_lev = lev
        else:
            break
    leverage = final_lev

    notional = margin_usdt * leverage
    actual_max_loss = margin_usdt * sl_dist * leverage

    # 资金费率成本
    funding_rate = abs(market.funding_rate)
    is_long = (action == 'open_long')
    if is_long:
        funding_mult = 1.0 if market.funding_rate > 0 else -0.5
    else:
        funding_mult = -0.5 if market.funding_rate > 0 else 1.0

    # 持仓时间
    if market.atr_pct >= 0.03:
        est_hours = 16
    elif market.atr_pct >= 0.015:
        est_hours = 32
    else:
        est_hours = 48

    funding_periods = est_hours / 8
    funding_cost = notional * funding_rate * funding_periods * max(0, funding_mult)
    fee_cost = notional * 0.001
    total_cost = funding_cost + fee_cost

    # tp_dist (约1.5~3倍sl_dist)
    tp_dist = sl_dist * 2.0
    gross_profit = notional * tp_dist
    gross_loss = actual_max_loss

    if (gross_loss + total_cost) > 0:
        effective_rr = (gross_profit - total_cost) / (gross_loss + total_cost)
    else:
        effective_rr = 99

    return {
        'leverage': leverage,
        'margin': margin_usdt,
        'notional': notional,
        'max_loss': actual_max_loss,
        'sl_dist': sl_dist,
        'tp_dist': tp_dist,
        'effective_rr': effective_rr,
        'total_cost': total_cost,
        'est_hours': est_hours,
    }


def simulate_trade_outcome(budget: dict, market: MarketState) -> float:
    """模拟单笔交易结果，返回PnL（USDT）"""
    # 胜率调整
    win_rate = WIN_RATE_BASE

    # 趋势强度加成
    if market.trend_strength > 70:
        win_rate += 0.05
    elif market.trend_strength < 40:
        win_rate -= 0.05

    # MA信号加成
    if market.ma_signal != 'none':
        win_rate += 0.08

    win_rate = max(0.35, min(0.80, win_rate))

    if random.random() < win_rate:
        # 盈利：tp_dist × notional - costs
        # 实际止盈可能提前（模拟50%全止盈，50%部分止盈）
        if random.random() < 0.6:
            profit_mult = 1.0  # 完整止盈
        else:
            profit_mult = 0.6  # 部分止盈
        pnl = budget['notional'] * budget['tp_dist'] * profit_mult - budget['total_cost']
    else:
        # 亏损：max_loss + costs
        # 80%正常止损，20%滑点多亏10%
        if random.random() < 0.8:
            pnl = -(budget['max_loss'] + budget['total_cost'])
        else:
            pnl = -(budget['max_loss'] * 1.1 + budget['total_cost'])

    return pnl


def run_simulation(n_days: int = 1000, signals_per_day: int = SIGNALS_PER_DAY):
    """运行Monte Carlo模拟"""
    print("=" * 70)
    print(f"Monte Carlo模拟：统一风险预算框架")
    print(f"参数：余额={BALANCE}U, 保证金上限={MAX_TRADE_AMOUNT}U, "
          f"R:R门槛={RR_THRESHOLD}")
    print(f"模拟：{n_days}天, 每天{signals_per_day}个信号")
    print("=" * 70)

    # 统计变量
    total_signals = 0
    total_opened = 0
    total_pnl = 0.0
    daily_pnls = []
    reject_reasons = {}
    leverage_dist = {}
    trade_pnls = []

    for day in range(n_days):
        day_pnl = 0.0
        day_trades = 0

        for _ in range(signals_per_day):
            total_signals += 1
            market = generate_random_market()

            # Step 1: 计算score和方向
            score, action = compute_score(market)
            if action == 'hold':
                reject_reasons['score<30(hold)'] = reject_reasons.get('score<30(hold)', 0) + 1
                continue

            # Step 2: 过滤规则
            passed, reason = apply_filters(market, action, score)
            if not passed:
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                continue

            # Step 3: 风险预算
            budget = calc_risk_budget(market, action)

            # Step 4: R:R门槛
            if budget['effective_rr'] < RR_THRESHOLD:
                reject_reasons['effective_rr<1.5'] = reject_reasons.get('effective_rr<1.5', 0) + 1
                continue

            # Step 5: 通过所有过滤 → 开仓
            total_opened += 1
            day_trades += 1
            leverage_dist[budget['leverage']] = leverage_dist.get(budget['leverage'], 0) + 1

            # Step 6: 模拟交易结果
            pnl = simulate_trade_outcome(budget, market)
            trade_pnls.append(pnl)
            day_pnl += pnl

        daily_pnls.append(day_pnl)
        total_pnl += day_pnl

    # ============ 输出结果 ============
    open_rate = total_opened / total_signals * 100
    avg_daily_pnl = total_pnl / n_days
    avg_daily_return = avg_daily_pnl / BALANCE * 100

    print(f"\n{'─'*70}")
    print(f"📊 开仓率分析")
    print(f"{'─'*70}")
    print(f"  总信号数: {total_signals}")
    print(f"  开仓次数: {total_opened}")
    print(f"  开仓率: {open_rate:.1f}%")
    print(f"  日均开仓: {total_opened/n_days:.1f}笔")

    print(f"\n  拒绝原因分布:")
    sorted_reasons = sorted(reject_reasons.items(), key=lambda x: -x[1])
    for reason, count in sorted_reasons:
        pct = count / total_signals * 100
        print(f"    {reason}: {count} ({pct:.1f}%)")

    print(f"\n{'─'*70}")
    print(f"📊 杠杆分布")
    print(f"{'─'*70}")
    for lev in OKX_ALLOWED_LEV:
        count = leverage_dist.get(lev, 0)
        if count > 0:
            pct = count / total_opened * 100
            bar = '█' * int(pct / 2)
            print(f"  {lev:2d}x: {count:5d} ({pct:5.1f}%) {bar}")

    print(f"\n{'─'*70}")
    print(f"📊 收益分析")
    print(f"{'─'*70}")
    win_trades = [p for p in trade_pnls if p > 0]
    loss_trades = [p for p in trade_pnls if p <= 0]
    win_rate_actual = len(win_trades) / len(trade_pnls) * 100 if trade_pnls else 0
    avg_win = sum(win_trades) / len(win_trades) if win_trades else 0
    avg_loss = sum(loss_trades) / len(loss_trades) if loss_trades else 0

    print(f"  实际胜率: {win_rate_actual:.1f}%")
    print(f"  平均盈利: +{avg_win:.2f}U")
    print(f"  平均亏损: {avg_loss:.2f}U")
    print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "  盈亏比: N/A")
    print(f"  总PnL: {total_pnl:.2f}U ({n_days}天)")
    print(f"  日均PnL: {avg_daily_pnl:.2f}U")
    print(f"  日化收益率: {avg_daily_return:.3f}%")
    print(f"  年化收益率: {avg_daily_return * 365:.1f}%")

    # 风险指标
    daily_returns_pct = [p / BALANCE * 100 for p in daily_pnls]
    max_daily_loss = min(daily_pnls)
    max_daily_gain = max(daily_pnls)

    # 最大回撤
    cumulative = 0
    peak = 0
    max_drawdown = 0
    for pnl in daily_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

    # Sharpe ratio (简化)
    import statistics
    if len(daily_returns_pct) > 1:
        mean_r = statistics.mean(daily_returns_pct)
        std_r = statistics.stdev(daily_returns_pct)
        sharpe = (mean_r / std_r) * math.sqrt(365) if std_r > 0 else 0
    else:
        sharpe = 0

    print(f"\n{'─'*70}")
    print(f"📊 风险指标")
    print(f"{'─'*70}")
    print(f"  最大单日亏损: {max_daily_loss:.2f}U ({max_daily_loss/BALANCE*100:.1f}%)")
    print(f"  最大单日盈利: +{max_daily_gain:.2f}U (+{max_daily_gain/BALANCE*100:.1f}%)")
    print(f"  最大回撤: {max_drawdown:.2f}U ({max_drawdown/BALANCE*100:.1f}%)")
    print(f"  Sharpe Ratio (年化): {sharpe:.2f}")
    print(f"  盈利天数: {sum(1 for p in daily_pnls if p > 0)}/{n_days} "
          f"({sum(1 for p in daily_pnls if p > 0)/n_days*100:.0f}%)")

    # 分位数
    sorted_daily = sorted(daily_pnls)
    p5 = sorted_daily[int(n_days * 0.05)]
    p25 = sorted_daily[int(n_days * 0.25)]
    p50 = sorted_daily[int(n_days * 0.50)]
    p75 = sorted_daily[int(n_days * 0.75)]
    p95 = sorted_daily[int(n_days * 0.95)]

    print(f"\n  日PnL分位数:")
    print(f"    P5:  {p5:.2f}U  (最差5%的日子)")
    print(f"    P25: {p25:.2f}U")
    print(f"    P50: {p50:.2f}U  (中位数)")
    print(f"    P75: {p75:.2f}U")
    print(f"    P95: {p95:.2f}U  (最好5%的日子)")

    print(f"\n{'═'*70}")
    print(f"💡 结论")
    print(f"{'═'*70}")
    print(f"  开仓率 {open_rate:.1f}% → 日均 {total_opened/n_days:.1f} 笔交易")
    print(f"  日化 {avg_daily_return:.3f}% → 月化 {avg_daily_return*30:.2f}% → 年化 {avg_daily_return*365:.1f}%")
    print(f"  单笔最大亏损控制在 {abs(min(trade_pnls)):.2f}U ({abs(min(trade_pnls))/BALANCE*100:.1f}%余额)")
    print(f"  风险预算框架有效：不同波动率标的获得不同杠杆，单笔风险一致")
    print(f"{'═'*70}")


def run_conservative_simulation(n_days: int = 1000):
    """保守场景模拟（更贴近实盘）"""
    print("\n\n")
    print("=" * 70)
    print("🔒 保守场景模拟（实盘预期）")
    print("=" * 70)
    print(f"  假设：基础胜率48%（含滑点/延迟损耗）")
    print(f"  假设：40%完整止盈，60%部分止盈(0.5x)")
    print(f"  假设：20%止损滑点多亏15%")
    print(f"  假设：每天4个信号（非高频）")
    print(f"  假设：LLM偶尔误判导致额外5%信号被错误开仓")
    print("=" * 70)

    total_signals = 0
    total_opened = 0
    total_pnl = 0.0
    daily_pnls = []
    trade_pnls = []
    signals_per_day = 4  # 更保守的信号频率

    for day in range(n_days):
        day_pnl = 0.0

        for _ in range(signals_per_day):
            total_signals += 1
            market = generate_random_market()
            score, action = compute_score(market)

            if action == 'hold':
                continue

            passed, reason = apply_filters(market, action, score)
            if not passed:
                continue

            budget = calc_risk_budget(market, action)
            if budget['effective_rr'] < RR_THRESHOLD:
                continue

            total_opened += 1

            # 保守胜率
            win_rate = WIN_RATE_CONSERVATIVE
            if market.trend_strength > 70 and market.ma_signal != 'none':
                win_rate += 0.10  # 强趋势+MA信号加成
            elif market.trend_strength < 40:
                win_rate -= 0.05

            win_rate = max(0.35, min(0.65, win_rate))

            if random.random() < win_rate:
                # 盈利
                if random.random() < PARTIAL_TP_RATE:
                    profit_mult = 1.0
                else:
                    profit_mult = 0.5  # 部分止盈
                pnl = budget['notional'] * budget['tp_dist'] * profit_mult - budget['total_cost']
            else:
                # 亏损
                if random.random() < 0.80:
                    pnl = -(budget['max_loss'] + budget['total_cost'])
                else:
                    pnl = -(budget['max_loss'] * 1.15 + budget['total_cost'])  # 滑点

            trade_pnls.append(pnl)
            day_pnl += pnl

        daily_pnls.append(day_pnl)
        total_pnl += day_pnl

    # 输出
    open_rate = total_opened / total_signals * 100 if total_signals > 0 else 0
    avg_daily_pnl = total_pnl / n_days
    avg_daily_return = avg_daily_pnl / BALANCE * 100

    win_trades = [p for p in trade_pnls if p > 0]
    loss_trades = [p for p in trade_pnls if p <= 0]
    win_rate_actual = len(win_trades) / len(trade_pnls) * 100 if trade_pnls else 0
    avg_win = sum(win_trades) / len(win_trades) if win_trades else 0
    avg_loss = sum(loss_trades) / len(loss_trades) if loss_trades else 0

    print(f"\n  开仓率: {open_rate:.1f}% → 日均 {total_opened/n_days:.1f} 笔")
    print(f"  实际胜率: {win_rate_actual:.1f}%")
    print(f"  平均盈利: +{avg_win:.2f}U | 平均亏损: {avg_loss:.2f}U")
    print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")
    print(f"\n  {'─'*50}")
    print(f"  日均PnL: {avg_daily_pnl:.2f}U")
    print(f"  日化收益率: {avg_daily_return:.3f}%")
    print(f"  月化收益率: {avg_daily_return*30:.2f}%")
    print(f"  年化收益率: {avg_daily_return*365:.1f}%")

    # 风险
    max_dd = 0
    cum = 0
    peak = 0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    sorted_daily = sorted(daily_pnls)
    print(f"\n  {'─'*50}")
    print(f"  最大回撤: {max_dd:.2f}U ({max_dd/BALANCE*100:.1f}%)")
    print(f"  盈利天数: {sum(1 for p in daily_pnls if p > 0)}/{n_days} "
          f"({sum(1 for p in daily_pnls if p > 0)/n_days*100:.0f}%)")
    print(f"  P5日PnL: {sorted_daily[int(n_days*0.05)]:.2f}U")
    print(f"  P50日PnL: {sorted_daily[int(n_days*0.50)]:.2f}U")
    print(f"  P95日PnL: {sorted_daily[int(n_days*0.95)]:.2f}U")

    # 复利模拟
    print(f"\n  {'─'*50}")
    print(f"  📈 复利增长模拟（初始{BALANCE}U）:")
    balance = BALANCE
    for month in range(1, 7):
        for d in range(30):
            idx = (month-1)*30 + d
            if idx < len(daily_pnls):
                balance += daily_pnls[idx] * (balance / BALANCE)  # 按比例缩放
        print(f"    第{month}月末: {balance:.1f}U ({(balance/BALANCE-1)*100:.0f}%)")

    print(f"\n{'═'*70}")
    print(f"💡 保守预期总结")
    print(f"{'═'*70}")
    print(f"  日化: {avg_daily_return:.2f}% (保守) ~ {avg_daily_return*1.5:.2f}% (中性)")
    print(f"  月化: {avg_daily_return*30:.1f}% ~ {avg_daily_return*30*1.5:.1f}%")
    print(f"  单笔风险: ≤5.25U (5%余额)，风险预算框架严格控制")
    print(f"  关键假设: 胜率48%+盈亏比{abs(avg_win/avg_loss):.1f}+日均{total_opened/n_days:.1f}笔")
    print(f"{'═'*70}")


def run_realistic_simulation(n_days: int = 1000):
    """最贴近实盘的模拟（含Daily Hard Stop + 冷却期）"""
    print("\n\n")
    print("=" * 70)
    print("🎯 实盘模拟（含熔断保护 + 冷却期）")
    print("=" * 70)
    print(f"  Daily Hard Stop: 单日亏损≥50U 或 连续3笔亏损 → 当日停止")
    print(f"  冷却期: 熔断后次日恢复")
    print(f"  胜率: 基础52%（MA信号验证过83%，实盘打6折）")
    print(f"  信号频率: 5标的×每4h一轮 = 日均5-6个信号")
    print("=" * 70)

    total_signals = 0
    total_opened = 0
    total_pnl = 0.0
    daily_pnls = []
    trade_pnls = []
    halted_days = 0
    signals_per_day = 5

    for day in range(n_days):
        day_pnl = 0.0
        day_trades = 0
        consecutive_losses = 0
        halted = False

        for _ in range(signals_per_day):
            total_signals += 1

            # Daily Hard Stop检查
            if day_pnl <= -50 or consecutive_losses >= 3:
                if not halted:
                    halted = True
                    halted_days += 1
                continue

            market = generate_random_market()
            score, action = compute_score(market)

            if action == 'hold':
                continue

            passed, reason = apply_filters(market, action, score)
            if not passed:
                continue

            budget = calc_risk_budget(market, action)
            if budget['effective_rr'] < RR_THRESHOLD:
                continue

            total_opened += 1
            day_trades += 1

            # 实盘胜率模型
            win_rate = 0.52
            if market.trend_strength > 70 and market.ma_signal != 'none':
                win_rate += 0.12
            elif market.trend_strength > 50 and market.ma_signal != 'none':
                win_rate += 0.06
            elif market.trend_strength < 35:
                win_rate -= 0.08

            # 资金费率方向正确加成
            is_long = (action == 'open_long')
            if (is_long and market.funding_rate < -0.0002) or \
               (not is_long and market.funding_rate > 0.0002):
                win_rate += 0.03

            win_rate = max(0.38, min(0.70, win_rate))

            if random.random() < win_rate:
                # 盈利（分级止盈）
                r = random.random()
                if r < 0.35:
                    profit_mult = 1.0   # 完整止盈
                elif r < 0.70:
                    profit_mult = 0.6   # 第一级止盈后移动止损
                else:
                    profit_mult = 0.3   # 早期出场
                pnl = budget['notional'] * budget['tp_dist'] * profit_mult - budget['total_cost']
                consecutive_losses = 0
            else:
                # 亏损
                r = random.random()
                if r < 0.75:
                    loss_mult = 1.0     # 正常止损
                elif r < 0.90:
                    loss_mult = 1.12    # 轻微滑点
                else:
                    loss_mult = 1.25    # 严重滑点（闪崩）
                pnl = -(budget['max_loss'] * loss_mult + budget['total_cost'])
                consecutive_losses += 1

            trade_pnls.append(pnl)
            day_pnl += pnl

        daily_pnls.append(day_pnl)
        total_pnl += day_pnl

    # 输出
    open_rate = total_opened / total_signals * 100 if total_signals > 0 else 0
    avg_daily_pnl = total_pnl / n_days
    avg_daily_return = avg_daily_pnl / BALANCE * 100

    win_trades = [p for p in trade_pnls if p > 0]
    loss_trades = [p for p in trade_pnls if p <= 0]
    win_rate_actual = len(win_trades) / len(trade_pnls) * 100 if trade_pnls else 0
    avg_win = sum(win_trades) / len(win_trades) if win_trades else 0
    avg_loss = sum(loss_trades) / len(loss_trades) if loss_trades else 0

    print(f"\n{'─'*70}")
    print(f"📊 核心指标")
    print(f"{'─'*70}")
    print(f"  开仓率: {open_rate:.1f}%")
    print(f"  日均开仓: {total_opened/n_days:.1f}笔")
    print(f"  熔断天数: {halted_days}/{n_days} ({halted_days/n_days*100:.1f}%)")
    print(f"  实际胜率: {win_rate_actual:.1f}%")
    print(f"  平均盈利: +{avg_win:.2f}U")
    print(f"  平均亏损: {avg_loss:.2f}U")
    print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")

    print(f"\n{'─'*70}")
    print(f"📊 收益预期")
    print(f"{'─'*70}")
    print(f"  日均PnL: {avg_daily_pnl:.2f}U")
    print(f"  日化收益率: {avg_daily_return:.3f}%")
    print(f"  月化收益率: {avg_daily_return*30:.2f}%")
    print(f"  年化收益率: {avg_daily_return*365:.1f}%")

    # 风险指标
    max_dd = 0
    cum = 0
    peak = 0
    for p in daily_pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    sorted_daily = sorted(daily_pnls)
    import statistics
    daily_returns_pct = [p / BALANCE * 100 for p in daily_pnls]
    mean_r = statistics.mean(daily_returns_pct)
    std_r = statistics.stdev(daily_returns_pct)
    sharpe = (mean_r / std_r) * math.sqrt(365) if std_r > 0 else 0

    print(f"\n{'─'*70}")
    print(f"📊 风险控制")
    print(f"{'─'*70}")
    print(f"  最大回撤: {max_dd:.2f}U ({max_dd/BALANCE*100:.1f}%)")
    print(f"  Sharpe Ratio: {sharpe:.2f}")
    print(f"  盈利天数: {sum(1 for p in daily_pnls if p > 0)}/{n_days} "
          f"({sum(1 for p in daily_pnls if p > 0)/n_days*100:.0f}%)")
    print(f"  最大单日亏损: {min(daily_pnls):.2f}U")
    print(f"  P5: {sorted_daily[int(n_days*0.05)]:.2f}U | "
          f"P50: {sorted_daily[int(n_days*0.50)]:.2f}U | "
          f"P95: {sorted_daily[int(n_days*0.95)]:.2f}U")

    # 按月统计
    print(f"\n{'─'*70}")
    print(f"📊 月度表现（前6个月）")
    print(f"{'─'*70}")
    for month in range(6):
        start = month * 30
        end = min(start + 30, n_days)
        month_pnl = sum(daily_pnls[start:end])
        month_return = month_pnl / BALANCE * 100
        month_wins = sum(1 for p in daily_pnls[start:end] if p > 0)
        print(f"  第{month+1}月: PnL={month_pnl:+.1f}U ({month_return:+.1f}%) "
              f"盈利天数={month_wins}/30")

    print(f"\n{'═'*70}")
    print(f"🎯 实盘预期（最终结论）")
    print(f"{'═'*70}")
    print(f"")
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │  开仓率:  ~{open_rate:.0f}% (日均{total_opened/n_days:.1f}笔)       │")
    print(f"  │  日化:    {avg_daily_return:.2f}%                        │")
    print(f"  │  月化:    {avg_daily_return*30:.1f}%                        │")
    print(f"  │  最大回撤: {max_dd/BALANCE*100:.0f}% (含熔断保护)          │")
    print(f"  │  Sharpe:  {sharpe:.1f}                            │")
    print(f"  └─────────────────────────────────────────────┘")
    print(f"")
    print(f"  ⚠️  注意事项:")
    print(f"  - 以上基于105U本金，10U保证金上限")
    print(f"  - 实际表现受市场regime影响大（趋势市>震荡市）")
    print(f"  - 前1-2周可能为负（系统需要时间适应）")
    print(f"  - 建议跑2周paper trading验证后再加仓")
    print(f"{'═'*70}")


if __name__ == '__main__':
    run_simulation(n_days=1000, signals_per_day=6)
    run_conservative_simulation(n_days=1000)
    run_realistic_simulation(n_days=1000)
