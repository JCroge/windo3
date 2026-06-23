# 验证报告: pseudo-resonance-downweight（伪共振降权 · 病根1a）

- 日期：2026-06-23
- 结论：**实现正确，但 CF 回放证明 cap 在安全值下不咬 → 默认 OFF 潜伏护栏合并**

## 验证方法（红线适配）

`event_backtest.py` 走 RobustStrategy MA 信号、不调 `_compute_score`，触达不到本改动。改用真实磁带的 `tech_analysis` 喂**真实 `_compute_score`**（off vs on，cap=50/45），口径与 `utils/decision_replay.py` 同源。

## 关键发现

样本：190 笔真实 accept-open。

| 检查 | 结果 |
|---|---|
| baseline 复算保真（vs 录制 signal_score） | 73%（138/190） |
| cap=50/45 翻转 accept→hold | **0/190** |
| cap=45 降 confidence | 4/190（2%），中位降 1 分 |
| 失去 EV 强信号豁免（score 70→<70） | 0/190 |
| baseline \|score\|≥70 | 6/190 |

**MA-bloc cap 在安全值(45-50)下几乎完全不咬。** 根因：入场门对 rule_signal 只要 25 分、**对分数量级不敏感**——伪共振抬高的是量级，但 accept/reject 由低门控制；且 MA 块绝对值中位才 30，cap 45 极少触发。要起效得压到 ~25-30（激进，逼近门会翻转决策）。

**"要求独立佐证"替代思路 PnL 不支持**：纯伪共振单（独立信号≈0/反向）n=6 净 −6.58U/胜率17%；有佐证单 n=6 净 −8.79U/胜率33%——两边都亏、n=6 太薄无法区分。

## 决策（用户拍板）

**默认 OFF 潜伏护栏合并**：cap 机制实现正确、单测齐全（10 passed）、`pseudo_resonance_downweight_enabled` 默认 false 不改任何线上行为。启用前须把 cap 压到能咬的区间并做更强 CF+PnL 验证。

## Meta 结论（跨病根3 + 病根1a，重要）

连续两个 score/gate 层 change 在真实数据上都"咬不动"：
- 病根3（反转合流否决）：LLM 从不产反转判断 → 0% 触发。
- 病根1a（伪共振降权）：入场门量级不敏感（25分）→ cap 安全值 0 翻转。

指向真问题**更底层**：策略无稳健方向 edge（[[strategy-no-directional-edge-diagnosis]]）+ decay 期有无佐证都亏 + 样本太薄过不了诚实门 + 低入场门结构。**下一步高价值方向不在继续拧 scoring/gate 参数**，而应考虑：入场门结构本身（25 分对 rule_signal 太松）、数据累积后重验、或 paper 重验机械 edge。

## 测试
- `test_pseudo_resonance_downweight.py`：10 passed（config、封顶数学、开关回退、归因、banner）。
- 回归：judge 相关套件 87 passed，零回归（默认 off 透传=旧行为）。
