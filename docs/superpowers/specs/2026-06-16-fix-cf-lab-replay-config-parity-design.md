---
comet_change: fix-cf-lab-replay-config-parity
role: technical-design
canonical_spec: openspec
---

# Design Doc — fix-cf-lab-replay-config-parity

修复反事实实验室回放与 live 生产的 **config 不一致**致 baseline_fidelity 仅 0.34 的问题。方案 C(生产基线立即生效 + 磁带录 resolved config 防漂移)。上游事实源见 proposal.md。

## 1. 根因(已坐实)

| 事实 | 证据 |
|---|---|
| replay/CF-sim baseline 用空 config | `replay_decision(record, None)` / `build_delta_report(baseline_config={})` |
| `_install_config_flags` 把 phase2 四 flag 默认 False | `decision_replay.py:201-204` `g("phase2_*", False)` |
| live 生产四 flag 都 True | `config_loader.py:166-169` DEFAULTS |
| 不一致致 confidence 发散 | 走 `judge.py:1283 max(40,conf*0.7)=40→quality_gate`,而 live 走 `1281 max(60,...)`→rr_below_floor |
| 量级 | 全量 660 条 v2:config={}→fidelity **0.365**;config=DEFAULTS→**0.902** |

**已证伪**:非 CF state injection(纯 L2 录制快照 replay 同样 0.365)、非分桶 EV/archetype 主因(那是生产 config 下剩余 ~10% 残差)。

## 2. 方案 C

### per-record effective config(核心)
```
effective_cfg(record) = record["config_snapshot"]      # 新记录(Part 2 录的)
                        or production_base_config()       # 旧记录 fallback
baseline 臂  = effective_cfg(record)
perturbed 臂 = { **effective_cfg(record), **perturbation }   # 扰动只覆盖目标旋钮
```
两臂同基线起步 → delta 干净;扰动 dict 绝不重置其它旋钮。

### Part 1 — 生产基线(立即修好现有磁带 → 0.90)
- 新增 `production_base_config()`:返回 `_install_config_flags` 消费的 config key 白名单(~57 旋钮 + phase2 四 flag)的生产值,取自 `config_loader` 解析后的生产 config。
- `build_delta_report`/`run_arm` baseline 臂、`sweep_knob`、`cf_direction_recommendation` 改用 `effective_cfg(record)` 替代 `{}`。

### Part 2 — 磁带录 resolved config(防未来漂移)
- `decision_tape.build_bundle` 加 `config_snapshot` 字段 = 决策时 Judge 的 config 白名单子集(就是 `_install_config_flags` 读的那批 key);Judge 在两个录制 chokepoint 传入自己 resolved config。`SCHEMA_VERSION` v2→v3。
- replay/`effective_cfg` 优先用 `record["config_snapshot"]`,缺则 fallback `production_base_config()`。旧 v2 记录永久走 fallback(已坐实 fallback=0.90)。
- **动机**:实验室本职是推荐改 config;config 一旦变,只有 per-record 录 config 才能让"变更后磁带"忠实回放,否则静态 DEFAULTS 又会发散。

### observability-only 红线
- `config_snapshot` 是写入时 Judge 录自己的 config(与 `state_snapshot` 同性质,write-only)。CF-sim/replay 只**读** config_snapshot 与 `config_loader`,**绝不读** live 运行态。守卫 `tests/test_cf_red_line_guard.py` 维持。

## 3. 测试策略
- **生产基线坐实**:全量 v2 磁带 L2 fidelity 用 `production_base_config()` ≥0.85(实测 0.90;对照 config={} 0.365)。
- **config_snapshot round-trip**:新构造 record 带 config_snapshot → `effective_cfg` 取它 → replay 复现;旧 record 无字段 → fallback production base。
- **perturbation 叠加**:扰动 rr_floor_default 不改动 phase2 flag(fixture 断言 perturbed cfg 只差目标 key)。
- **schema v3**:build_bundle 产出含 config_snapshot,`SCHEMA_VERSION` 升级;旧 v2 仍可读(fallback)。
- 红线守卫维持;全量 pytest 基线 1247 不回退。

## 4. 非目标 / 坦白
- 不追 production-config 下剩余 ~10% 残差(`ev_gate→15m_blocked` 36 / `ev_gate→accept` 27,二级状态重建差异),留后续 change。
- Part 2 的 config_snapshot 只对**新磁带**生效;旧磁带靠 Part 1 fallback 已 0.90,不阻塞当前可信度。
- 不改 live Judge 决策逻辑、不改 live 生产 config、不改 choppy 地板 1.50、无需 event_backtest。
