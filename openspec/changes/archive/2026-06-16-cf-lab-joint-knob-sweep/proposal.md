## Why

反事实实验室 L4 单旋钮扫描已收官，且给出**首个可信结论**：放宽 `rr_floor_default`（1.50→1.20）与 `min_confidence`（60→40）**各自** PnL delta≈0 → 非高价值杠杆（reject 被多 gate 过度决定，放宽单门只把决策级联到其它 gate 而非盈利开仓）。

但单旋钮扫描有一个结构性盲区：**它看不见旋钮之间的交互效应**。两个门各自不是瓶颈，不代表「同时放宽两个门」不是——若 `rr_floor` 与 `min_confidence` 在 reject 链路上**串联**（信号先过 confidence 门再过 R:R 门），单独放宽任一个都会被另一个继续拦截，唯有联合放宽才可能解锁开仓。单旋钮 delta≈0 既可能是「真没用」，也可能是「被另一个门掩盖」——单旋钮扫描**无法区分这两者**。

多旋钮联合扫描是区分二者的唯一手段。其科学价值不在「扫一个更大的网格找最优点」（背景下大概率仍 `no_actionable_direction`），而在**量化旋钮间的交互项**：`interaction(a,b) = delta(a,b) − delta(a,base) − delta(base,b)`。这是 2-way 因子设计的交互项，能给出单旋钮答不了的新信息：协同（联合解锁）/ 可加（确认独立无用）/ 拮抗（互相抵消）。

## What Changes

- 新增**通用 N 旋钮笛卡尔积扫描引擎**：复用 L3b `build_delta_report`（真实 Judge 序列重演），对多个旋钮的取值网格做笛卡尔积，每个组合作为一个多 key `perturbed_config` 跑一个 perturbed 臂。
- **baseline 臂只跑一次复用**：baseline_fidelity 是 baseline 臂相对录制的属性，对所有组合相同，不应每组合重算（笛卡尔积下省 N 倍 baseline 重算）。
- **核心产出：交互效应报告**——网格里纳入各旋钮 base 值作边缘参照，`(base,base)` 的 delta≈0 当自检锚点；对每个联合点算 `interaction = delta(a,b) − delta(a,base) − delta(base,b)`，判定协同/可加/拮抗。
- **辅助：多维孤峰守卫的方向推荐**——把单旋钮的一维「前后邻居连贯」守卫推广到网格上的「轴邻居连贯」（曼哈顿距离=1 的相邻点同向才算趋势），多重比较门槛随网格点数（∏ 各轴取值）收紧。
- 首发 driver 聚焦 `rr_floor_default × min_confidence` 两轴，接续已有的两个单旋钮结论。

## Capabilities

### New Capabilities
- `joint-knob-sweep`: 多旋钮笛卡尔积联合扫描 + 交互效应（可加性偏离）量化 + 多维孤峰守卫的方向推荐，复用 L3b `build_delta_report`，observability-only。

### Modified Capabilities
<!-- 无修改；与单旋钮 knob-sweep-engine / direction-recommender 并列，不改其语义 -->

## Impact

- 代码：新增 `utils/joint_knob_sweep.py`（笛卡尔积扫描 + 交互项计算 + 多维推荐）+ 对应测试；driver `cf_direction_recommendation.py` 增一段两轴联合扫描示例（或新 driver）。复用 `utils/sequential_perturbation.py::build_delta_report` / `run_arm`，**不改其逻辑**。
- 行为：纯离线分析工具；**不改 live Judge 决策逻辑、不改生产 config、不改 choppy R:R 地板 1.50、无需 event_backtest**。
- 红线：observability-only write-only；严禁任何 gate/veto/halt/rank/daily-stop import；推荐绝不自动改线上 config（人审）。红线守卫 `tests/test_cf_red_line_guard.py` 扩展覆盖新模块。
- 非目标：不引入 LLM 旋钮；不做 >2 轴的实战推荐（引擎通用支持 N 轴，但首发只验证 2 轴方法论）；不优化 `build_delta_report` 内部 PnL 估算保真（继承 L3b 天花板）；不自动应用任何结论到线上。
