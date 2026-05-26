# R:R Floor Policy 修复 PRD

## 背景

2026-05-26 上午，INJ-USDT 多次出现 `effective_rr=1.45`、`score=45` 的多头机会，但被 `R:R=1.45<1.50，低于动态地板` 拦截。日志与事件流水显示这些信号的 `entry_regime/raw_regime` 为 `choppy`，因此当前代码按 `rr_floor_default=1.50` 执行，而没有走 `rr_floor_long_bullish=1.30` 的低 R:R 多头分支。

当前文档定义为：牛市多头 1.30、牛市空头 1.80、默认 1.50。直接将 `rr_floor_default` 从 1.50 改成 1.30 会把 mixed/choppy/bearish 下的默认多空门槛全部放宽，不符合现有风控意图。

## 问题定义

问题不是单纯的配置加载错误，而是策略语义不够精确：

- `utils/config_loader.py` 是运行态单一配置源，Orchestrator 会把完整 config 注入 Judge，Judge 内部 fallback 默认值不会在正常启动路径生效。
- 当前 `rr_floor_default=1.50` 与现有文档一致，但与“强势标的多头在 choppy/mixed 市况下允许低 R:R 缩仓试单”的新增预期不一致。
- 主开仓路径和 deferred 路径存在重复的 R:R floor 选择逻辑；deferred helper 已有 `plan.is_probe -> min_rr=1.30`，主路径没有同等分支，导致 probe_long 也可能被 1.50 拦截。
- 事件 attribution 只记录 `rr_policy`，没有记录实际使用的 `rr_floor_used` 和选择原因，事后排障成本高。

## 产品目标

在不全局降低赔率底线的前提下，允许符合条件的低 R:R 多头以缩仓和独立槽位方式进入，同时保留短线和震荡市风控。

目标行为：

- 默认 R:R floor 继续保持 1.50。
- 牛市多头 floor 保持 1.30，并进入 low_rr 缩仓策略。
- mixed/choppy 下，仅当多头信号与标的自身趋势强一致时，允许使用 1.30 floor。
- probe_long/probe_short 使用显式 probe floor 1.30，并保持 probe 仓位限制。
- 空头不因本修复被放宽：牛市空头仍为 1.80，其他 regime 默认仍为 1.50。

## 推荐方案

新增一个显式 long-only 策略，而不是改 `rr_floor_default`：

- 新配置：
  - `rr_floor_default: 1.50`
  - `rr_floor_long_bullish: 1.30`
  - `rr_floor_long_aligned_choppy: 1.30`
  - `low_rr_long_aligned_enabled: true`
  - `probe_rr_floor: 1.30`
- 新判定：
  - `action == open_long`
  - `low_rr_slot_enabled == true`
  - `effective_rr >= rr_floor_long_aligned_choppy`
  - `score >= min_deferred_signal_score`
  - `trend.direction == bullish`
  - `trend.higher_tf_bias == bullish` 或 `trend.daily_bias == bullish`
  - 15m 未明确 block long
- 通过后：
  - `plan.is_low_rr = true`
  - `plan.slot_type = low_rr_extra`
  - `plan.leverage <= low_rr_max_leverage`
  - `plan.size_usdt` 按既有 low R:R scaling 下调

## 实现要求

1. 抽出单一函数，例如 `_select_rr_floor(action, plan, tech, score)`，返回：
   - `min_rr`
   - `rr_policy`
   - `rr_floor_reason`
2. 主开仓路径和 `_apply_regime_policy()` 必须调用同一个 floor 选择函数。
3. `_rejection_attribution()` 和 open attribution 增加：
   - `rr_floor_used`
   - `rr_floor_reason`
   - `symbol_trend`
   - `symbol_higher_tf_bias`
4. 启动 banner 增加默认 floor、aligned choppy long floor、probe floor，避免只显示 low R:R floor。
5. 不修改 `rr_floor_default` 的含义，不把 mixed/choppy 下的 short 默认门槛降到 1.30。

## 非目标

- 不改变 `min_confidence`、EV gate、15m 入场确认、最大并发持仓、每日熔断。
- 不让所有 R:R 1.30-1.50 的信号自动入场。
- 不绕过 short-side guard。

## 运维要求

配置变更或代码变更后，必须通过 OS 层重启进程。当前系统在启动时把 config dict 注入各 Agent，同进程内不会自动刷新这些阈值。
