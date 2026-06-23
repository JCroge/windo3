---
comet_change: restore-llm-rsi-veto-power
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-23-restore-llm-rsi-veto-power
status: final
---

# Design Doc: restore-llm-rsi-veto-power（反转合流否决）

> 针对策略诊断病根3（规则不可否决/反转预警自我压制）。高层决策（双信号合流 veto → deferred_pullback；不动 scoring）由 OpenSpec proposal/design 定，本文档为技术实现设计。

## 1. 问题与目标

现行 judge.py（行号 2026-06-23 实测）：rule_signal 触发 ±35 锁方向（`_compute_score` judge.py:3316），LLM 看反只缩仓 60%、不能否决（judge.py:1296-1310，注释 1251-1252 明写），RSI 背离在 HTF 对齐时被压到 ≤15（judge.py:3381-3400）。净效果：追势开仓买在反转点时无独立信号能拦。

**目标**：当一笔开仓即将发出且**两个相互独立的反转信号共振**时，把它从立即开改路由到等回调（deferred_pullback），保留回调后机会。只在合流时触发（最保守，最小误杀）。

## 2. 核心组件：单点收口 helper

```python
def _reversal_confluence_veto(self, action: str, llm_action: str, tech: dict) -> str | None:
    """反转合流否决判定。纯函数、单一实现，所有开仓终点共用。
    返回 reason 字符串=触发，None=不触发。"""
    if not self._reversal_veto_enabled:
        return None
    dir_long = (action == 'open_long')
    dir_short = (action == 'open_short')
    if not (dir_long or dir_short):
        return None
    # (a) LLM 明确看反向
    llm_counter = (
        llm_action in ('open_long', 'open_short')
        and llm_action != action
    )
    # (b) RSI 背离与开仓方向相反（读原始布尔信号，不读被压制的分数）
    rsi_div = ((tech or {}).get('momentum', {}) or {}).get('rsi_divergence')
    rsi_against = (
        (dir_long and rsi_div == 'bearish_div')
        or (dir_short and rsi_div == 'bullish_div')
    )
    return 'reversal_confluence' if (llm_counter and rsi_against) else None
```

- 输入：候选 `action`、`llm_action`（来自 `llm_result`）、`tech` 快照。
- **不读** `_compute_score` 的背离分数（那是 scoring，归病根1）；只读 `tech.momentum.rsi_divergence` 原始布尔。
- 单一函数 = 单点收口，杜绝 P1-03 那种第二份内联实现。

可选 LLM 置信下限：若引入 `reversal_veto_min_llm_confidence`，在 helper 内对 `llm_counter` 追加 `llm_confidence >= 阈值` 条件（默认值见 §4，保守起步可设 0 即不启用该子门）。

## 3. 插入点与路由

**主路径**（judge.py 主决策）：挂在 LLM 强冲突分支之后（judge.py:1310 之后）、`_open_quality_rejection`（judge.py:1312）之前：

```
... LLM 冲突缩仓处理 (1296-1310) ...
veto = self._reversal_confluence_veto(final_action, llm_action, tech)
if veto:
    → 路由到 deferred_pullback（见下），写归因，return
... _open_quality_rejection (1312) ...
```

**defer 路由选型**：复用 `_check_entry_position_policy` 的 `deferred_pullback_overheat` 同条路径（带 `deferred_target_price` + shadow 记录 + 归因），与 regime-aware-long-entry-guard 一致的观测口径。理由：比裸 `pending_pullback`+hold（judge.py:1424/1446）信息更全，Reviewer/backtest 可一致切分。实现时通过一个轻量入口（如给 `_check_entry_position_policy` 增加一个 reason 来源，或抽出共享的 `_route_to_deferred_pullback(symbol, action, tech, reason)` 单点函数）触发，**避免新建第二份 defer 构造逻辑**。

## 4. deferred 路径覆盖（红线关键）—— 已核定：边界

实现阶段核定结论（judge.py deferred_pullback 再分发 ~L962-992）：deferred 再分发**仅在价格回调达标时触发**，这正是 veto 期望的"等回调"结果；且该处**无新鲜 LLM 读取**（不调 `_ask_llm`，仅有可能过时的 `_symbol_llm_cache`）。在此重复 veto 会自毁其目的（把已回调的入场又推迟一次）。

**决定**：veto 单点收口于**主路径即时开仓终点**（fresh LLM + RSI 齐备处），deferred 再分发路径**不重复挂** veto——这是语义正确的边界，非覆盖缺口。代码注释（deferred_pullback 再分发处）+ 本节 + delta spec 显式记录，**不写第二份判定实现**（守 P1-03 单点收口红线）。

## 5. 配置（config_loader 四段式）

| 键 | 默认 | 说明 |
|---|---|---|
| `llm_rsi_reversal_veto_enabled` | 见 §7 | 总开关，false 即回退旧行为（LLM/RSI 仅缩仓） |
| `reversal_veto_min_llm_confidence` | 0（不启用子门，保守起步） | LLM 反向需达此置信才计入合流 |

四段式：`utils/config_loader.py` DEFAULTS / HARD_LIMITS / env 覆盖 / yaml 覆盖；banner 显示开关状态。

## 6. 归因（observability，决策不变路径也写）

放行与 defer 双路径均写入 attribution：

| 字段 | 含义 |
|---|---|
| `reversal_veto_triggered` | bool |
| `reversal_veto_llm_action` | LLM 当时 action |
| `reversal_veto_rsi_div` | rsi_divergence 取值 |
| `reversal_veto_deferred_dir` | 被 defer 的方向（触发时） |

供 Reviewer 分桶 + backtest pre/post 分布对比。

## 7. 验证（CLAUDE.md 红线）——实况见 verify 报告

**event_backtest 不适用**：`event_backtest.py` 走 RobustStrategy MA 信号，不调 `MultiJudge._make_decision`/`_ask_llm`，触达不到 veto 路径。改用真实决策磁带（`decision_replay_tape.jsonl`，含真实 LLM+RSI）口径，与 `utils/decision_replay.py` 同源。

**真实磁带结论**（187 笔 accept-open）：`llm_relation` 只有 hold(155)/agree(32)、**从无 reverse** → veto（LLM 反向开仓 AND RSI 背离）**0/187 触发**。线上 LLM 从不"反向开仓"表达反对（只 hold）。

**决定（用户拍板）：默认 OFF 潜伏护栏合并**——`llm_rsi_reversal_veto_enabled` 默认 `false`，不改任何线上决策（红线"上 live"不触发）。机制正确就位，未来 LLM 行为若产出 reverse 判断置 true 即生效；**启用前须 CF 回放 pre/post PnL 验证**（净 PnL 不变差 + 触发率低区间）。详见 `docs/superpowers/reports/2026-06-23-restore-llm-rsi-veto-power-backtest.md`。

**单元测试**：
- 合流触发 → defer 路由 + 归因写入；
- 仅 LLM 反向（无 RSI 背离）→ 不触发；
- 仅 RSI 背离（LLM 不反向）→ 不触发；
- 开关 off → 完全回退（不触发，旧缩仓行为保留）；
- 主路径 ↔ deferred 路径 parity（同输入同判定）。

## 8. 边界与非目标

- 不动 scoring：±35 权重、RSI 背离 ≤15 分数压制 → 病根1 另起 change。
- 不碰 RSI≤30 空单硬门（judge.py:890/1015/1443）。
- 不碰出场、体制分类、槽位逻辑。
- 单点收口避免 P1-03 红线（第二份内联实现）。

## 9. 回退

`llm_rsi_reversal_veto_enabled=false` 即时回退。生效需重启 live 交易进程（Judge 实例化读配置）。
