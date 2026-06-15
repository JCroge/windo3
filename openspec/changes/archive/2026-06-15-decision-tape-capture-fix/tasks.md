## 1. 测试先行（同构 + 红线）

- [x] 1.1 新增 `tests/test_decision_tape_capture.py`：构造一条带真实 `tech_analysis` + `llm_output_inline` + state_snapshot 的 bundle，断言 `replay_decision` 能走到 gate 并复现拒因（如 `rr_below_floor`），而非"无信号→hold"短路
- [x] 1.2 同测试加 perturb 用例：同一记录 perturb `rr_floor_default` 至低于其 R:R 后，`replay_decision` 翻转为 accept（验证捕获使旋钮可生效）
- [x] 1.3 加 `replayable` 真实性用例：tech 非空+有快照→replayable=true；tech 空 或 缺快照→replayable=false
- [x] 1.4 确认 `tests/test_cf_red_line_guard.py` 现有断言不回归（决策/风控路径仍不读 CF 产物）

## 2. decision_tape.py — replayable 守卫 + schema

- [x] 2.1 `build_bundle` 把 `replayable` 收紧为 `state_snapshot is not None and bool(tech_analysis)`
- [x] 2.2 `SCHEMA_VERSION` v1→v2，标记自包含记录

## 3. judge.py — LLM cache 捕获（核心，绝不动决策逻辑）

- [x] 3.1 `__init__` 新增 `self._symbol_llm_cache = {}`（紧邻 `self._symbol_tech_cache`）
- [x] 3.2 `_make_decision` 起点把该 symbol 的 `_symbol_llm_cache` 置 None（per-decision reset）
- [x] 3.3 `llm_result = await self._ask_llm(...)`（~1218）之后写入 `self._symbol_llm_cache[symbol] = llm_result`
- [x] 3.4 symbol 退出清理点（~378，tech cache pop 处）同步 `self._symbol_llm_cache.pop(s, None)`
- [x] 3.5 accept 录制点（~1979）：`llm_output=self._symbol_llm_cache.get(symbol)`（tech 维持 `_symbol_tech_cache.get(symbol) or {}`）
- [x] 3.6 reject 录制点（`_record_rejected_plan`，~3028）：`tech_analysis=self._symbol_tech_cache.get(symbol) or {}`、`llm_output=self._symbol_llm_cache.get(symbol)`

## 4. 验证与回归

- [x] 4.1 跑 `python3 -m pytest -q`，确认基线在 1223 之上 +新测试，无回归
- [x] 4.2 静态确认无决策路径行为变化：diff 仅触碰 cache 读写 + 两个 build_bundle 调用点 + decision_tape；gate/plan/ranking 逻辑零改动
- [x] 4.3 编译检查 `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q agents/trading/judge.py utils/decision_tape.py`

## 5. 收尾说明（非代码）

- [x] 5.1 在 verify 报告记录：修复只影响新磁带，旧 909 条永久 replayable=false；需等新磁带累积 1-2 天后重跑 `cf_direction_recommendation.py` 验证 L2 真实化 + L4 推荐
