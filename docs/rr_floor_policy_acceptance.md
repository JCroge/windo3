# R:R Floor Policy 验收文档

## 验收范围

验证 R:R floor 修复后，低 R:R 多头机会可以在受限条件下放行，同时默认赔率门槛和空头风控不被意外放宽。

## 必须通过的验收项

### AC-RR-01 配置默认值

- `load_config(strict_live_check=False)` 返回：
  - `rr_floor_default == 1.50`
  - `rr_floor_long_bullish == 1.30`
  - `rr_floor_short_bullish == 1.80`
  - `probe_rr_floor == 1.30`
- 启动 banner 必须显示 default、long_bullish、long_aligned_choppy、probe 四类 floor。

### AC-RR-02 牛市多头低 R:R 放行

条件：

- `effective_regime=bullish`
- `action=open_long`
- `effective_rr=1.45`
- `score >= 45`
- `low_rr_slot_enabled=true`

期望：

- 不被 `rr_below_floor` 拦截。
- plan 标记 `is_low_rr=true`。
- `slot_type=low_rr_extra`。
- `rr_floor_used=1.30`。

### AC-RR-03 Choppy/Mixed 标的强一致多头放行

条件：

- `effective_regime=choppy` 或 `mixed`
- `action=open_long`
- `effective_rr=1.45`
- `score >= 45`
- `trend.direction=bullish`
- `trend.higher_tf_bias=bullish` 或 `trend.daily_bias=bullish`
- 15m 未明确 `block_long`

期望：

- 不被 `rr_below_floor:1.45<1.50` 拦截。
- plan 标记 `is_low_rr=true`。
- `slot_type=low_rr_extra`。
- attribution 中 `rr_policy=long_aligned_low_rr`，`rr_floor_used=1.30`。

### AC-RR-04 Choppy/Mixed 非强一致多头仍拦截

条件：

- `effective_regime=choppy` 或 `mixed`
- `action=open_long`
- `effective_rr=1.45`
- `trend.direction != bullish` 或 HTF/daily 均非 bullish

期望：

- 仍被 `rr_below_floor:1.45<1.50` 拦截。
- `slot_type` 不应被改成 `low_rr_extra`。

### AC-RR-05 空头默认门槛不被放宽

条件：

- `effective_regime=choppy` 或 `mixed`
- `action=open_short`
- `effective_rr=1.45`

期望：

- 仍被 `rr_below_floor:1.45<1.50` 拦截。
- 不允许因为 long-only 修复影响 short。

### AC-RR-06 牛市空头强保护不变

条件：

- `effective_regime=bullish`
- `action=open_short`
- `effective_rr=1.70`

期望：

- 仍按 `rr_floor_short_bullish=1.80` 或 short regime guard 拦截。

### AC-RR-07 Probe 路径一致

条件：

- `plan.is_probe=true`
- `effective_rr=1.35`
- 主开仓路径或 deferred 路径任一入口

期望：

- 使用 `probe_rr_floor=1.30`。
- 不被默认 1.50 floor 拦截。
- attribution 中记录 `rr_policy=probe`，`rr_floor_used=1.30`。

### AC-RR-08 主路径与 deferred 路径一致

同一组 action、plan、tech、score 输入，在主开仓路径和 `_apply_regime_policy()` 中必须得到相同的：

- `rr_floor_used`
- `rr_policy`
- 是否 low_rr scaling
- 是否 `rr_below_floor`

### AC-RR-09 线上日志可验证

修复后如再次出现 INJ-USDT 类似场景，日志或 journal payload 必须能看到：

- `entry_regime`
- `raw_regime`
- `rr_floor_used`
- `rr_floor_reason`
- `rr_policy`
- `is_low_rr`
- `slot_type`

## 回归命令

建议至少执行：

```bash
python3 -m pytest -q test_judge_deferred_regime_policy.py test_regime_hysteresis.py test_low_rr_slots.py test_ranking_slots.py
python3 -m pytest -q
```

## 部署验收

1. commit 并 push 代码和文档。
2. 对运行进程发送 `SIGTERM`，等待优雅退出。
3. 使用原 nohup 启动方式重启。
4. 查看 orchestrator/Judge 启动日志，确认新 floor 配置已打印。
5. 查看 `data/journal/events_YYYYMMDD.jsonl`，确认新 attribution 字段出现。
