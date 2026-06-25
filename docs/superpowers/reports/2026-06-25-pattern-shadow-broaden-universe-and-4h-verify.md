# Verify 报告: pattern-shadow-broaden-universe-and-4h

- **Change**: pattern-shadow-broaden-universe-and-4h
- **Design Doc**: docs/superpowers/specs/2026-06-25-pattern-shadow-broaden-universe-and-4h-design.md
- **类型**: observability-only（零 live 改动）
- **日期**: 2026-06-25
- **结局**: **re-validate gate 失败 → 干净证伪日线/4h 形态 edge；change 改为"记录证伪"，4h cron 不部署**

## 1. 头条结论（re-validate gate）

扩盘到 ~100 币冻结 universe 后重跑 `cf_pattern_edge_discovery` 回测，**日线与 4h 双双干净证伪**：

```
过三关(三段同号 + 诚实门非薄样本 + PnL_CI下界>0 + FDR): 0
→ 无形态过关 → 日线尺度形态无可信 edge(干净证伪)。   ← 1d 与 4h 都是这句
```

- **所有 pattern×context 的均 R 全负**（1d −0.30~−1.46；4h −0.42~−2.53），无一为正。
- **`Bearish Engulfing | 低位 | 跌势`（30 币时代 +0.326R 的"已确认"信号）在宽 universe 排名里根本不出现** → 原 30 币结果是**小样本/选择偏差**，不泛化。
- 多个 pattern 已是 `actionable`(n=100~511) 但均 R 仍负 → 不是样本不足，是 edge 真负。

**这正是 re-validate gate 的设计意图。** 与既有结论 `alpha-source-hunt-verdict`（赌动量但市场无动量、全 alpha 源证伪）一致——日线蜡烛形态在宽 universe + 诚实多重比较校正下加入被证伪清单。

## 2. 实现核对（delta spec 全实现，code review 通过）

| 项 | 状态 |
|---|---|
| Task1 冻结 ~100 binance 流动 universe（排稳定币/杠杆/非标准 base） | ✅ commit 9b61dbf |
| Task2 fetch 102 币 ×{1d,4h} 入 klines.db | ✅ |
| Task4 部署版 runner interval 参数化 + **settle-when-determinable** + dedup-by-bar-ts | ✅ 69e8b96，task review Spec✅/质量 Approved（2 Minor 可接受） |
| Task5 lab 版 runner 同步参数化 | ✅ 63cc622，红线+5 测试绿 |
| Task6 全量 pytest | ✅（见 §3） |
| settle-when-determinable：早退出立即结算/整窗满 expired/窗未满留未结算；净 R 值不变、无前视 | ✅ 单测锁三态 |
| dedup `(symbol,detect_bar_open_time,interval)` + 旧记录 fallback | ✅ |

delta spec 三 requirement（record 含 interval+bar-ts dedup / settle-when-determinable+窗口×bpd / 冻结 universe）全部实现。

## 3. 范围调整（用户决策）

re-validate 失败后用户裁定：**改为 observability 记录证伪，不部署 4h cron**。落实：
- **保留**：冻结 ~100 universe、runner interval 参数化、settle-when-determinable（都是工具/正确性改进，已提交+审查）。
- **部署**：更新后的 runner `cp` 到部署目录（消除 repo/部署漂移；现有日线 record(09:17)/settle(周一) cron 自动用新版=宽 universe + settle-when-determinable）。
- **不部署**：**4h record/settle launchd jobs 不创建**——不加速收集一个已证伪的非-edge。Task7 的 4h launchd 子任务作废。
- 全量 pytest：见运行结果（基线 1430 → 含新 `test_fwdshadow_runner.py` 7）。

## 4. 意义与后续

- 日线/4h 蜡烛形态 edge 在诚实宽 universe 下不成立 → **形态路线作为独立 alpha 来源基本走到头**（与 strategy-no-directional-edge / alpha-source-hunt 同向）。
- 既有 30 币日线前向影子的 5 条记录：原本指望确认 +0.326R，现知该数是 artifact；可继续让日线 null-monitor 前向确认证伪，或停掉（低价值）。
- 真正的 edge 仍需回到策略上游（信号/时机/体制），形态不是答案。
- **不上 live、不改 config**；本 change 纯 observability，结论是负结果但有价值（证伪一条假设、且 gate 机制证明有效）。
