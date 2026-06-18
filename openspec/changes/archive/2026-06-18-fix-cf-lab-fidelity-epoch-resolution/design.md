# Design (高层)

> 深度技术设计见 comet-design 阶段产出的 Design Doc（`docs/superpowers/specs/`）。本文件只记高层架构决策。canonical spec = openspec。

## 架构决策

**纪元解析三层合并（采纳）。** `replay_decision` 的有效 config 改为：

```
production_base_config()        # 生产静态默认（会随默认翻转漂移）
  < 纪元兜底 epoch_defaults(rec) # 缺键按"录制纪元"补：ladder 缺→False、ev_winrate 缺→True
  < record.config_snapshot       # 录制时实际值（每条自描述，优先）
  < 真扰动 override (config)      # CF 实验扰动旋钮，仍在最顶层
```

否决：
- **现状（override 压 snapshot）**：测试把"纪元 pin"塞进"扰动 override"层，对新纪元记录系统性发散（0.729）。
- **全局 pin / 裸回放**：磁带横跨两纪元 + production 默认已翻转，单一纪元救不了任一侧（naked 0.525）。
- **只用 v3 完整 snapshot 记录**：v3-only 仍 0.797（残余非纪元问题），且丢弃数据。

## 可信度指标改判

gate 严格保真（哪个门拦）对"同 reject、门归因不同"过度敏感（range_position vs ev_gate 短路顺序），低估可信度。**新增 accept/reject 二元保真为主判据**（≥0.95，实测 v3 0.991 / full 0.985），gate 保真降为诊断次指标。

## 数据流

```
record ──┬─ config_snapshot (每条自描述纪元, 缺键则纪元兜底补)
         ├─ tech_analysis (完整, 含 entry_context.position_in_24h_range)
         └─ state_snapshot (_recent_win_rate 等)
              ↓ replay_decision 三层合并有效 config
         真实 Judge._make_decision → gate / accept-reject
              ↓ 对比
         _gate_of_recorded vs _gate_of_replayed  (诊断次指标)
         accept/reject(recorded) vs accept/reject(replayed)  (主可信度指标)
```

## 残余调查（range_position→ev_gate）

逐记录追 ev_gate EV 内部输入/输出，钉死 pass→fail 真因。已排除：capture 缺口（字段在）、ladder/ev_winrate 纪元。据结论决定本 change 修或 follow-up。

## 边界

- observability-only：全程离线 write-only，红线守卫禁生产链路 import，无 live 行为变更。
- 纪元兜底表是显式 map（键→录制纪元默认），新键加 DEFAULTS 时需登记其"加入前纪元默认"。

## 测试策略

- 纪元解析后 baseline gate 保真 ≥0.85（实测 0.890）。
- accept/reject 二元保真 ≥0.95（实测 0.985）。
- 扰动 override 仍能翻转目标旋钮（CF 实验机制不破，回归 perturbation 测试）。
- 纪元兜底对缺键记录补对值（单测 ladder 缺→False、ev_winrate 缺→True）。
