## ADDED Requirements

### Requirement: 隔离回放构造真实 Judge
系统 SHALL 用 `MultiJudge.__new__` 绕过 `__init__` 构造 Judge，从 record 的状态快照白名单还原 `self.*`，并复用真实 `_make_decision` 决策逻辑，不重写评分/gate。

#### Scenario: 状态还原
- **WHEN** 给定一条带状态快照的 record
- **THEN** harness SHALL 还原快照内全部白名单 `self.*` 字段（list 还原回 set 等），使 Judge 看到与历史一致的隐藏状态

#### Scenario: 复用真实决策代码
- **WHEN** harness 执行回放
- **THEN** 其 SHALL 调用真实 `MultiJudge._make_decision`，SHALL NOT 另写第二份评分/gate/RR-floor 实现

### Requirement: 回放确定性 mock
系统 SHALL mock 决策路径全部已知非确定性来源，使同一 record 回放结果确定。

#### Scenario: 时间确定
- **WHEN** 回放执行
- **THEN** `time.time()` SHALL 返回 record 的 timestamp，使 cooldown/TTL/deferred timeout 判定确定

#### Scenario: 不触交易所
- **WHEN** 回放需要余额
- **THEN** 系统 SHALL 用快照 `_available_balance` 恢复，余额刷新打桩为 no-op，SHALL NOT 调真实交易所

#### Scenario: LLM 复用内联
- **WHEN** 回放走 LLM 决策路径
- **THEN** 系统 SHALL 注入 record 的 `llm_output_inline`，SHALL NOT 重新调用 LLM

#### Scenario: publish 截获
- **WHEN** 回放中 Judge 调用 `publish`
- **THEN** harness SHALL override 为 capture，收集 payload 而非发真实总线消息

### Requirement: golden-master 决策比对
系统 SHALL 比对回放输出与 record 的 `trade_decision_output`：离散字段严格相等，plan 连续字段允许极小相对容差。

#### Scenario: 严格字节级字段（决定决策）
- **WHEN** 比对回放与历史决策
- **THEN** `action`/`confidence`/`dispatch_path`/`entry_type`/`slot_type`/`is_probe`/`is_low_rr`/`short_gate_decision`/`short_gate_reason`/`rr_policy`/`rr_floor_used`/`entry_position_status`/`entry_position_block_reason`/`blocked_by` SHALL 严格相等，任一不等即判 mismatch

#### Scenario: 连续字段容差
- **WHEN** 比对 plan 的 `size_usdt`/`entry_ref`/`stop_loss`/`take_profit`（逐元素）/`leverage`
- **THEN** 系统 SHALL 允许 <0.5% 相对误差，超出即判 mismatch

#### Scenario: 自由文本仅信息不判负
- **WHEN** 比对 `reasoning`/`key_factors`/`risk_warnings`（LLM 自由文本透传）
- **THEN** 系统 SHALL 记录 diff 但 SHALL NOT 因其不一致判 mismatch（golden-master 钉决策逻辑，不钉自由文本）

#### Scenario: 复现不重算 PnL
- **WHEN** 回放经过 EV gate
- **THEN** 系统 SHALL 用快照 `_recent_wins`/`_total_completed_trades` 还原值，SHALL NOT 重算 realized PnL

### Requirement: harness observability-only write-only
系统 SHALL 保证回放 harness 为离线工具，严禁被任何 gate/veto/halt/rank/daily-stop 读取或进入生产决策链路。

#### Scenario: harness 不进生产链路
- **WHEN** 任意交易/风控逻辑执行
- **THEN** 其 SHALL NOT import 或调用回放 harness

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
