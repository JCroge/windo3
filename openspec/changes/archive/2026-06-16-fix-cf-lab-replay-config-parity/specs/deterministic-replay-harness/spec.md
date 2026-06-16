## ADDED Requirements

### Requirement: 回放有效 config 与 live 生产一致
回放 harness 的有效决策 config SHALL 与录制该决策时的 live 生产 config 一致，不得用空 config 致 `_install_config_flags` 把 Phase-2 等 flag 默认到与生产相反的值，从而使 confidence/gate 路径系统性发散。

#### Scenario: 优先用录制 config_snapshot
- **WHEN** 回放一条带 `config_snapshot` 的记录
- **THEN** harness SHALL 用该 `config_snapshot` 作为 baseline 有效 config

#### Scenario: 旧记录用生产基线 fallback
- **WHEN** 回放一条无 `config_snapshot` 的记录
- **THEN** harness SHALL 用 `production_base_config()`（取自 `config_loader` 生产解析值，含 Phase-2 flag=True）作 baseline，SHALL NOT 用空 config 默认值

#### Scenario: 生产基线显著恢复保真
- **WHEN** 用生产基线 config 对全量真实磁带跑零扰动 baseline 回放
- **THEN** gate-level baseline_fidelity SHALL 显著高于空 config（实测 0.365 → ~0.90），跨过可信阈值
