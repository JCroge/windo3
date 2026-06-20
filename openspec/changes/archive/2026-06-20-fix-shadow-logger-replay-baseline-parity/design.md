## 高层架构决策（详细技术设计见 comet-design 阶段的 Superpowers Design Doc）

### 问题本质

影子记录器把 **live 决策（无复盘偏差）** 与 **replay(both-levers)（有复盘偏差）** 直接相减，差里混入复盘保真误差。实测 37 条 shadow_holds 全是复盘失真（lever1 两臂复盘 delta = 0；13/37 baseline 复盘复现不出 live accept）。

### 方案：两臂同复盘 + baseline 自检闸

```
现状(错):
  live(real, 无复盘偏差) ── 减 ──> replay(both, 有复盘偏差)
                                    差 = lever1 + 复盘偏差   ✗

改后(对):
  replay(lever2-only, baseline) ── 减 ──> replay(both, shadow)
                                          差 = lever1（复盘偏差两臂抵消） ✓
  + 自检: replay(lever2-only).accept/reject == live.accept/reject ?
            否 → baseline_mismatch=True → 排除出增量统计
```

### 关键决策

1. **两臂都走 `replay_decision`**：baseline 臂 `{path_evidence:False, ladder:True}`（= live 现配置）、shadow 臂 `{path_evidence:True, ladder:True}`。lever1 增量 = shadow − baseline，复盘机器的系统性偏差对两臂同向、在 delta 抵消。依据：`sequential_perturbation` 已确立"两臂同估算 → 偏差在 delta 抵消"原则。

2. **保留 live record 仅用于自检，不用于增量**：live 的 accept/reject 作为 baseline 复盘的"金标准"，baseline 复盘背离它即标 `baseline_mismatch`、排除。依据：`perturbation_replay` 的 baseline 复现自检闸。

3. **自检只比 accept/reject 二元类别**：不要求 plan 连续字段一致（复盘 plan 容差是另一层，参 golden-master <0.5%）。开仓 vs 非开仓的二元一致即认为 baseline 可信，符合 `fix-cf-lab-fidelity-epoch-resolution` 把可信度判据定为 accept/reject 二元保真 ≥0.95 的先例。

4. **多一次 replay 的成本**：每信号现在跑 2 次 `replay_decision`（baseline + shadow）而非 1 次。复盘是纯计算（mock 外部 await、缓存 llm、无网络），fire-and-forget 在 publish 后，对 live 零延迟；成本可接受。

### 红线（不变）

- observability-only write-only：两条复盘臂绝不 publish 真实 bus / 不下单 / 不 mutate live Judge·portfolio·cooldown·daily-stop。
- fail-safe：任一臂复盘异常 → 跳过本次影子记录、绝不破 live 决策。
- 不动 ev-gate config（config-parity 假设已证伪）。
- 红线守卫 `tests/test_cf_red_line_guard.py` 禁读影子产物不回归。

### 非目标

- 不深挖复盘失真的具体未还原状态根因（baseline 自检闸对失真源不可知地兜底，无需定位到字段）。
- 不改 lever1/lever2 策略本身、不改 ev-gate。
- 不补影子日志 retention（既有 follow-up，另议）。
