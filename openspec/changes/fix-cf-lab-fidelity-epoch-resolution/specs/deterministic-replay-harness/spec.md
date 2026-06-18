## MODIFIED Requirements

### Requirement: 回放有效 config 与 live 生产一致
回放 harness 的有效决策 config SHALL 与**录制该决策时的纪元** live 生产 config 一致，不得用空 config 致 `_install_config_flags` 把 Phase-2 等 flag 默认到与生产相反的值。当某 config 键在录制之后才加入 DEFAULTS（默认值随之翻转），缺该键的旧记录回放 SHALL 用**录制纪元默认**而非当前 production 默认，避免默认漂移致系统性发散。有效 config 的合并优先级 SHALL 为：`production_base_config()` < 纪元兜底（缺键的旧纪元默认）< `record.config_snapshot`（录制实际值优先）< 扰动 override（CF 实验旋钮，最顶层）。

#### Scenario: 优先用录制 config_snapshot
- **WHEN** 回放一条带 `config_snapshot` 的记录
- **THEN** harness SHALL 用该 `config_snapshot` 的键值覆盖 production 基线与纪元兜底（录制实际值优先）

#### Scenario: 缺键用录制纪元默认 fallback
- **WHEN** 回放的记录其 `config_snapshot` 缺少某个当前 DEFAULTS 中存在的键（该键在录制后才加入）
- **THEN** harness SHALL 用该键的**录制纪元默认**（来自纪元兜底表，如 `ladder_rr_enabled`→False、`ev_winrate_gate_enabled`→True），SHALL NOT 用当前 production 默认（其默认可能已翻转）

#### Scenario: 扰动 override 不被纪元解析覆盖
- **WHEN** 回放传入扰动 override（CF 实验旋钮）
- **THEN** 该 override SHALL 在最顶层生效，覆盖纪元解析后的 baseline（保证 CF 扰动机制不被纪元修复破坏）

#### Scenario: 纪元解析恢复 baseline 保真
- **WHEN** 用纪元解析（逐记录按录制纪元）对全量真实磁带跑零扰动 baseline 回放
- **THEN** gate-level baseline_fidelity SHALL 跨过可信阈值（实测全局 pin 0.729 → 纪元解析 0.890）

### Requirement: accept/reject 二元保真为主可信度判据
CF lab 的主可信度判据 SHALL 为 accept/reject 二元保真（录制与回放在"开仓 vs 不开仓"上的一致率），而非 gate-level 严格保真（哪个门拦）。gate-level 严格保真对"同为 reject、仅门归因短路顺序不同"的情况过敏，低估真实可信度，SHALL 降为诊断性指标（记录/打印，不作硬可信门）。

#### Scenario: accept/reject 二元保真作硬门
- **WHEN** 对全量真实磁带跑零扰动 baseline 回放
- **THEN** accept/reject 二元保真 SHALL ≥0.95（实测 0.985），作为 lab 可信度硬断言

#### Scenario: gate 严格保真降为诊断
- **WHEN** baseline 回放计算 gate-level 严格保真
- **THEN** 其值 SHALL 被记录/打印供诊断，SHALL NOT 作为 lab 可信度的硬失败门

### Requirement: 纪元兜底表防静默漂移守卫
纪元兜底表（`_EPOCH_FALLBACK`）SHALL 覆盖所有"在 DEFAULTS 中存在、却缺于部分历史记录 `config_snapshot`、且影响 gate 决策"的键。系统 SHALL 提供守卫测试：任何此类缺键若未在 `_EPOCH_FALLBACK` 或显式 `_GATE_IRRELEVANT` allowlist 中分类，则测试失败，强制人工登记，防止未来默认翻转致保真度静默复发。

#### Scenario: 缺键必须被显式分类
- **WHEN** 扫描磁带发现某 DEFAULTS 键缺于部分记录的 `config_snapshot`
- **THEN** 守卫测试 SHALL 断言该键 ∈ `_EPOCH_FALLBACK` ∪ `_GATE_IRRELEVANT`，否则失败

#### Scenario: 纪元兜底键不悬空
- **WHEN** 校验 `_EPOCH_FALLBACK`
- **THEN** 其每个键 SHALL 存在于当前 `config_loader` DEFAULTS（无 stale/typo 条目）
