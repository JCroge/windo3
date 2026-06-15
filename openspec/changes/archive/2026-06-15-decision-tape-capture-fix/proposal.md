## Why

决策磁带的两个录制 chokepoint 把决定性输入**写死为空**，使全部 909 条已落盘记录 `tech_analysis=[]` / `llm_output_inline=null`——直接违反 `decision-replay-tape` 既有契约（spec 要求"tech_analysis 9 维全量快照"+"内联存储 parsed LLM 输出"）。后果：确定性回放 harness `replay_decision` 拿到空 tech + 空 LLM，`_make_decision` 在"无信号→hold"处短路，永远走不到任何 gate；导致 L2 终验 `baseline_fidelity=1.0` 虚高（仅 reject 大类碰巧匹配，未验证 gate 路径），L4 旋钮扫描（`rr_floor_default` 1.50→1.20、`min_confidence` 60→40）全程 `div=0/cf_open=0/delta=0` 空转。**这是反事实策略实验室 L2/L3/L4 无法产出任何方向推荐的根因。**

## What Changes

- **修复 reject 路径捕获**：`agents/trading/judge.py` 的 `_record_rejected_plan`→`build_bundle` 不再传 `tech_analysis={}` / `llm_output=None`，改为捕获真实决策输入。
- **修复 accept 路径捕获**：`_gate_and_publish_open` 处 `build_bundle` 不再传 `llm_output=None`，补齐 LLM 输出。
- **引入 `_symbol_llm_cache`**：镜像现有 `self._symbol_tech_cache` 模式，在 `_make_decision` 起点 reset、`_ask_llm` 之后写入、symbol 退出时 pop。两个 chokepoint 统一从 cache 按 symbol 取，**无需给 `_record_rejected_plan` / `_gate_and_publish_open` 加形参或穿透 10+ 调用点**。rule-only open 路径（LLM 之前）因 per-decision reset 取到 None，诚实反映"无 LLM 参与"。
- **`replayable` 真实性守卫**：`utils/decision_tape.py::build_bundle` 把 `replayable` 收紧为 `state_snapshot is not None AND bool(tech_analysis)`；旧 909 条空记录自然标 `replayable=false`，回放/报表自动跳过。`SCHEMA_VERSION` v1→v2 标记记录真正 self-contained。
- **旧数据不动**：保留 `data/decision_replay_tape.jsonl` 历史，不删不清。

非目标（Non-goals）：
- **绝不**改任何决策逻辑、gate 阈值、plan 计算、ranking——只改"录什么"，不改"怎么决策"。
- 不回填旧 909 条空记录（输入已不可追溯，永久不可回放）。
- 不改 `klines_1s.db` prune（独立的待办项，不在本 change 范围）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `decision-replay-tape`: 强化捕获契约——`tech_analysis` 与 `llm_output_inline` 必须反映真实决策输入（现有 accept/reject 落盘场景被空数据 vacuously 满足）；新增 `replayable` 标志真实性约束（仅当输入完整才标可回放）。

## Impact

- **代码**：`agents/trading/judge.py`（新增 `_symbol_llm_cache` + reset/set/pop + 两个 `build_bundle` 调用点改为 cache 取值）、`utils/decision_tape.py`（`replayable` 守卫 + schema bump）。
- **测试**：新增同构测试（构造带 tech+llm 的新 bundle，验证 `replay_decision` 能走到 gate：`rr_below_floor` 记录回放复现拒因，且 perturb `rr_floor_default` 后翻转 accept）；`tests/test_cf_red_line_guard.py` 不回归。
- **数据契约**：磁带新记录 schema v2（含真实 tech + llm）；旧 v1 空记录标 `replayable=false`。
- **运行影响**：observability-only write-only，**零决策路径变化**，不影响 live 交易。磁带每条约 +1~3KB（现 ~6.5KB/条、~6MB/天），90 天 prune 已存在。
- **时序**：修复只影响新磁带；需等新磁带累积（~900 条/天，1-2 天）才能重跑 L2 终验 + L4 方向推荐。
