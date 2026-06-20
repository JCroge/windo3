---
change: fix-shadow-logger-replay-baseline-parity
design-doc: docs/superpowers/specs/2026-06-20-fix-shadow-logger-replay-baseline-parity-design.md
base-ref: 90bc8316bd64c1261605341efa2f3a5f6bb096bc
---

# 影子记录器：两臂同复盘 + baseline 自检闸 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把影子记录器的 lever1 增量口径从 `live(real) vs replay(both-levers)` 改为 `replay(lever2-only baseline) vs replay(both-levers shadow)`，并加 baseline 复现自检闸排除复盘失真记录。

**Architecture:** 全部改动集中在 `utils/shadow_decision_logger.py`（纯函数 + `log_shadow_decision` 跑两条复盘臂）；`cf_shadow_lever1_compare.py` 过滤 `baseline_mismatch`；`agents/trading/judge.py` 零改动（chokepoint 已传 `real_decision`）。observability-only write-only，fail-safe 绝不破 live。

**Tech Stack:** Python 3.9, asyncio, pytest；复用 `utils/decision_replay.py::replay_decision`。

---

## Task 1: `_is_accept` 与 `compute_baseline_mismatch` 纯函数

**Files:**
- Modify: `utils/shadow_decision_logger.py`
- Test: `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_shadow_decision_logger.py` 追加：

```python
def test_is_accept():
    from utils.shadow_decision_logger import _is_accept
    assert _is_accept("open_long") is True
    assert _is_accept("open_short") is True
    assert _is_accept("hold") is False
    assert _is_accept(None) is False
    assert _is_accept("close") is False


def test_compute_baseline_mismatch():
    from utils.shadow_decision_logger import compute_baseline_mismatch
    # baseline 复盘复现 live(都 accept) → 不 mismatch
    assert compute_baseline_mismatch("open_long", "open_long") is False
    # baseline 复盘复现 live(都 reject/hold) → 不 mismatch
    assert compute_baseline_mismatch("hold", "hold") is False
    # baseline 复盘背离 live(baseline hold, live accept) → mismatch
    assert compute_baseline_mismatch("hold", "open_long") is True
    # baseline 复盘背离 live(baseline accept, live hold) → mismatch
    assert compute_baseline_mismatch("open_short", "hold") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_is_accept tests/test_shadow_decision_logger.py::test_compute_baseline_mismatch -v`
Expected: FAIL with ImportError (`_is_accept` / `compute_baseline_mismatch` 不存在)

- [ ] **Step 3: Write minimal implementation**

在 `utils/shadow_decision_logger.py` 顶部（`compute_flip_kind` 附近）加：

```python
def _is_accept(action):
    return action in ("open_long", "open_short")


def compute_baseline_mismatch(baseline_action, real_action):
    """baseline 复现自检：replay(lever2-only) 的 accept/reject 必须复现 live record。

    不一致 → True（复盘失真，该条排除出 lever1 增量统计）。只比二元 accept/reject。
    """
    return _is_accept(baseline_action) != _is_accept(real_action)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_is_accept tests/test_shadow_decision_logger.py::test_compute_baseline_mismatch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/shadow_decision_logger.py tests/test_shadow_decision_logger.py
git commit -m "feat(shadow-parity): _is_accept + compute_baseline_mismatch 自检纯函数"
```

---

## Task 2: `compute_flip_kind` 复用 `_is_accept`（语义改为 baseline vs shadow）

**Files:**
- Modify: `utils/shadow_decision_logger.py`
- Test: `tests/test_shadow_decision_logger.py`

说明：函数签名 `(baseline_action, shadow_action)` 不变、对外行为不变（既有 `test_compute_flip_kind` 仍通过），仅内部复用 `_is_accept` 并更新 docstring 表达 baseline 语义。

- [ ] **Step 1: Update existing test docstring + add baseline-semantics case**

把 `tests/test_shadow_decision_logger.py` 的 `test_compute_flip_kind` 替换为：

```python
def test_compute_flip_kind():
    # 语义：baseline(lever2-only) vs shadow(both-levers)
    from utils.shadow_decision_logger import compute_flip_kind
    assert compute_flip_kind("hold", "open_long") == "shadow_opens"      # lever1 解锁新单
    assert compute_flip_kind("open_long", "open_long") == "same"
    assert compute_flip_kind("open_long", "hold") == "shadow_holds"
    assert compute_flip_kind("hold", "hold") == "same"
    assert compute_flip_kind("open_short", "open_long") == "same"        # 都 accept → same
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_compute_flip_kind -v`
Expected: 最后一个 assert（`open_short` vs `open_long` → same）可能 FAIL（现实现 `real_open == shadow_open` 已是布尔比较，应已 PASS——若现实现用相等动作判定则 FAIL）

- [ ] **Step 3: Refactor `compute_flip_kind` to reuse `_is_accept`**

把 `utils/shadow_decision_logger.py` 的 `compute_flip_kind` 改为：

```python
def compute_flip_kind(baseline_action, shadow_action):
    """baseline(lever2-only) vs shadow(both-levers) 的开仓翻转类别。"""
    baseline_open = _is_accept(baseline_action)
    shadow_open = _is_accept(shadow_action)
    if baseline_open == shadow_open:
        return "same"
    return "shadow_opens" if shadow_open else "shadow_holds"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_compute_flip_kind -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/shadow_decision_logger.py tests/test_shadow_decision_logger.py
git commit -m "refactor(shadow-parity): compute_flip_kind 复用 _is_accept, 语义=baseline vs shadow"
```

---

## Task 3: `build_shadow_record` 新增 baseline 字段

**Files:**
- Modify: `utils/shadow_decision_logger.py`
- Test: `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: Replace existing schema test**

把 `tests/test_shadow_decision_logger.py` 的 `test_build_shadow_record_schema` 替换为：

```python
def test_build_shadow_record_schema():
    from utils.shadow_decision_logger import build_shadow_record
    rec = build_shadow_record(
        ts=1.0, symbol="HYPE-USDT",
        real={"action": "open_long", "gate": "accept"},
        baseline={"action": "open_long", "gate": "accept"},
        shadow={"action": "open_long", "gate": "accept", "plan": {"x": 1}},
        tech_context={"trend": {"strength": 70}})
    assert rec["symbol"] == "HYPE-USDT"
    assert rec["real_action"] == "open_long" and rec["real_gate"] == "accept"
    assert rec["baseline_action"] == "open_long" and rec["baseline_gate"] == "accept"
    assert rec["shadow_action"] == "open_long" and rec["shadow_gate"] == "accept"
    assert rec["baseline_mismatch"] is False          # baseline 复现 live
    assert rec["flip_kind"] == "same"                 # baseline vs shadow 都 accept
    assert rec["shadow_plan"] == {"x": 1}
    assert rec["tech_context"] == {"trend": {"strength": 70}}


def test_build_shadow_record_mismatch_flagged():
    from utils.shadow_decision_logger import build_shadow_record
    # live accept, 但 baseline 复盘 hold → baseline_mismatch=True
    rec = build_shadow_record(
        ts=2.0, symbol="XLM-USDT",
        real={"action": "open_long", "gate": "accept"},
        baseline={"action": "hold", "gate": "ev_gate"},
        shadow={"action": "hold", "gate": "ev_gate", "plan": None},
        tech_context={})
    assert rec["baseline_mismatch"] is True
    assert rec["flip_kind"] == "same"                 # baseline=hold, shadow=hold
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_build_shadow_record_schema tests/test_shadow_decision_logger.py::test_build_shadow_record_mismatch_flagged -v`
Expected: FAIL（`build_shadow_record` 现无 `baseline` 参数 → TypeError）

- [ ] **Step 3: Rewrite `build_shadow_record`**

把 `utils/shadow_decision_logger.py` 的 `build_shadow_record` 改为：

```python
def build_shadow_record(*, ts, symbol, real, baseline, shadow, tech_context):
    baseline_action = baseline.get("action")
    return {
        "timestamp": ts,
        "symbol": symbol,
        "real_action": real.get("action"),       # live 决策, 仅供自检追溯
        "real_gate": real.get("gate"),
        "baseline_action": baseline_action,       # replay(lever2-only)
        "baseline_gate": baseline.get("gate"),
        "shadow_action": shadow.get("action"),
        "shadow_gate": shadow.get("gate"),
        "shadow_plan": shadow.get("plan"),
        "baseline_mismatch": compute_baseline_mismatch(baseline_action, real.get("action")),
        "flip_kind": compute_flip_kind(baseline_action, shadow.get("action")),
        "tech_context": tech_context,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_build_shadow_record_schema tests/test_shadow_decision_logger.py::test_build_shadow_record_mismatch_flagged -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/shadow_decision_logger.py tests/test_shadow_decision_logger.py
git commit -m "feat(shadow-parity): build_shadow_record 加 baseline_action/gate/mismatch 字段"
```

---

## Task 4: `log_shadow_decision` 跑两条复盘臂

**Files:**
- Modify: `utils/shadow_decision_logger.py`
- Test: `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_shadow_decision_logger.py` 追加（用 monkeypatch 注入两臂复盘结果，避免依赖真实磁带）：

```python
def test_log_shadow_two_arms_and_mismatch(tmp_path, monkeypatch):
    import utils.shadow_decision_logger as sdl

    # 模拟两臂复盘：第一次调用(baseline)返回 hold, 第二次(shadow)返回 open_long
    calls = []
    async def fake_replay(bundle, config):
        calls.append(config)
        # baseline=lever2-only → hold; shadow=both → open_long
        if config.get("path_evidence_aligned_enabled") is False:
            return {"action": "hold", "attribution": {"blocked_by": "ev_gate"}}
        return {"action": "open_long", "plan": {"size": 1}}
    monkeypatch.setattr(sdl, "replay_decision", fake_replay)

    out = tmp_path / "s.jsonl"
    bundle = {"replayable": True, "symbol": "XLM-USDT", "timestamp": 9.0,
              "tech_analysis": {"t": 1}}
    r = asyncio.run(sdl.log_shadow_decision(bundle, {"action": "open_long"}, str(out)))
    assert r is not None
    # 跑了两臂, 顺序 baseline 先 shadow 后
    assert calls[0].get("path_evidence_aligned_enabled") is False
    assert calls[1].get("path_evidence_aligned_enabled") is True
    # baseline=hold 但 live=open_long → mismatch
    assert r["baseline_mismatch"] is True
    assert r["baseline_action"] == "hold"
    assert r["shadow_action"] == "open_long"
    assert r["flip_kind"] == "shadow_opens"     # baseline hold, shadow open
    line = json.loads([l for l in out.read_text().splitlines() if l.strip()][0])
    assert line["baseline_mismatch"] is True


def test_log_shadow_baseline_none_skips(tmp_path, monkeypatch):
    import utils.shadow_decision_logger as sdl
    async def fake_replay(bundle, config):
        # baseline 复盘返回 None(不可判定自检) → 整条跳过不写
        if config.get("path_evidence_aligned_enabled") is False:
            return None
        return {"action": "open_long"}
    monkeypatch.setattr(sdl, "replay_decision", fake_replay)
    out = tmp_path / "s.jsonl"
    bundle = {"replayable": True, "symbol": "X", "timestamp": 1.0, "tech_analysis": {}}
    r = asyncio.run(sdl.log_shadow_decision(bundle, {"action": "open_long"}, str(out)))
    assert r is None
    assert not out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_log_shadow_two_arms_and_mismatch tests/test_shadow_decision_logger.py::test_log_shadow_baseline_none_skips -v`
Expected: FAIL（现 `log_shadow_decision` 只跑一臂、`build_shadow_record` 调用缺 baseline → TypeError）

- [ ] **Step 3: Rewrite `log_shadow_decision` + add BASELINE_CONFIG**

在 `utils/shadow_decision_logger.py` 把 `SHADOW_CONFIG` 一行替换为两个 config，并重写 `log_shadow_decision`：

```python
# baseline = lever2-only（= live 现生效配置：l2 on / l1 off）
BASELINE_CONFIG = {"path_evidence_aligned_enabled": False, "ladder_rr_enabled": True}
# shadow = both levers on
SHADOW_CONFIG = {"path_evidence_aligned_enabled": True, "ladder_rr_enabled": True}


def _summ(decision):
    return {"action": (decision or {}).get("action", "hold"),
            "gate": _gate_of(decision), "plan": (decision or {}).get("plan")}


async def log_shadow_decision(bundle, real_decision, log_path, *, enabled=True, logger=None):
    """两臂复盘 + baseline 自检, write-only 追加 jsonl。fail-safe：异常绝不抛。

    baseline=replay(lever2-only), shadow=replay(both-levers)；lever1 增量=两臂之差。
    baseline 复盘背离 live record 的 accept/reject → baseline_mismatch=True（排除）。
    返回写入的 record(成功) 或 None(关闭/不可回放/baseline 不可判定/异常跳过)。
    """
    if not enabled:
        return None
    try:
        if not (bundle or {}).get("replayable"):
            return None
        baseline = await replay_decision(bundle, BASELINE_CONFIG)
        if baseline is None:          # baseline 无法复盘 → 自检不可判定 → 跳过不写
            return None
        shadow = await replay_decision(bundle, SHADOW_CONFIG)
        rec = build_shadow_record(
            ts=bundle.get("timestamp", 0), symbol=bundle.get("symbol"),
            real=_summ(real_decision), baseline=_summ(baseline), shadow=_summ(shadow),
            tech_context=bundle.get("tech_analysis"))
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
    except Exception as e:                # fail-safe：影子绝不破 live
        if logger:
            logger.warning(f"[shadow] log_shadow_decision skipped: {e}")
        return None
```

注：`_summ` 用 `_gate_of`，需确保 `_gate_of` 仍在文件中（原文件已有，不动）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py::test_log_shadow_two_arms_and_mismatch tests/test_shadow_decision_logger.py::test_log_shadow_baseline_none_skips -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add utils/shadow_decision_logger.py tests/test_shadow_decision_logger.py
git commit -m "feat(shadow-parity): log_shadow_decision 跑 baseline+shadow 两臂 + 自检 + None 短路"
```

---

## Task 5: fail-safe 回归 + 真实磁带冒烟

**Files:**
- Test: `tests/test_shadow_decision_logger.py`

- [ ] **Step 1: Update fail-safe + real-bundle tests for two-arm shape**

把 `tests/test_shadow_decision_logger.py` 的 `test_log_shadow_on_real_bundle` 末段 assert 扩展为校验新字段（其余 fail-safe 测试 `test_log_shadow_disabled_noop` / `test_log_shadow_fail_safe_never_raises` / `test_schedule_shadow_*` 不变，确认仍通过）：

```python
def test_log_shadow_on_real_bundle(tmp_path):
    rec = _load_one_replayable_record()
    if rec is None:
        import pytest
        pytest.skip("no replayable record in tape")
    from utils.shadow_decision_logger import log_shadow_decision
    out = tmp_path / "s.jsonl"
    r = asyncio.run(log_shadow_decision(rec, {"action": "hold"}, str(out)))
    if r is not None:
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["symbol"] == rec["symbol"]
        assert "baseline_action" in row and "baseline_mismatch" in row
        assert isinstance(row["baseline_mismatch"], bool)
        assert row["shadow_action"] in ("open_long", "open_short", "hold", "close", None)
```

- [ ] **Step 2: Run full shadow test module**

Run: `python3 -m pytest tests/test_shadow_decision_logger.py -v`
Expected: PASS（所有用例，含 fail-safe / schedule / disabled）

- [ ] **Step 3: Commit**

```bash
git add tests/test_shadow_decision_logger.py
git commit -m "test(shadow-parity): 真实磁带冒烟校验 baseline 字段 + fail-safe 回归"
```

---

## Task 6: 离线驱动过滤 `baseline_mismatch`

**Files:**
- Modify: `cf_shadow_lever1_compare.py:21-36`

- [ ] **Step 1: Update `load_shadow_opens` to exclude mismatches + count**

把 `cf_shadow_lever1_compare.py` 的 `load_shadow_opens` 改为（新增排除 `baseline_mismatch=True` 及缺该字段的旧记录，并返回被排除条数）：

```python
def load_shadow_opens():
    """读影子日志, 返回 flip_kind=shadow_opens 且 baseline 复现可信的记录。

    排除 baseline_mismatch=True（复盘失真）及缺 baseline_mismatch 字段的旧记录
    （fail-safe 当不可信），返回 (records, excluded_count)。
    """
    if not os.path.exists(SHADOW_LOG):
        return [], 0
    out = []
    excluded = 0
    for line in open(SHADOW_LOG):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("flip_kind") == "shadow_opens" and r.get("shadow_plan"):
            if r.get("baseline_mismatch") is False:
                out.append(r)
            else:                       # True 或缺字段(旧记录) → 不可信排除
                excluded += 1
    return out, excluded
```

- [ ] **Step 2: Update `main()` call site to unpack tuple + print excluded**

把 `cf_shadow_lever1_compare.py` 的 `main()` 中 `opens = load_shadow_opens()` 改为：

```python
    opens, excluded = load_shadow_opens()
    print(f"=== 影子对比：lever1 增量（影子 shadow_opens = lever1 解锁、实盘没开的单）===")
    print(f"shadow_opens 候选: {len(opens)}（已排除 baseline_mismatch/旧记录 {excluded} 条）")
```

（删除原先重复的 `print(f"shadow_opens 候选: ...")` 行，避免重复打印。）

- [ ] **Step 3: Smoke-run the driver against live log**

Run: `python3 cf_shadow_lever1_compare.py`
Expected: 正常运行，打印 "已排除 baseline_mismatch/旧记录 N 条"（当前日志全是旧记录无 baseline 字段 → 全部排除，shadow_opens 候选 0；不报错）

- [ ] **Step 4: Commit**

```bash
git add cf_shadow_lever1_compare.py
git commit -m "feat(shadow-parity): 离线驱动剔除 baseline_mismatch + 报排除条数"
```

---

## Task 7: 红线守卫 + 全量回归

**Files:**
- Verify only: `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: Run red-line guard (禁读影子产物不回归)**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -v`
Expected: PASS（决策/风控路径禁读影子产物断言不回归）

- [ ] **Step 2: Run full suite**

Run: `python3 -m pytest -q`
Expected: PASS 数 ≥ 1314 基线 + 新增用例；8 failed 仅 round2 asyncio 既有污染（`test_round2_probe_long_dispatcher` / `test_round2_request_id_position`），非本 change 引入；零新退化

- [ ] **Step 3: 登记 main() 用例（若该测试文件有 main 注册惯例）**

检查 `tests/test_shadow_decision_logger.py` 是否有 `if __name__` / main 注册块；若有，把新用例 `test_is_accept` / `test_compute_baseline_mismatch` / `test_build_shadow_record_mismatch_flagged` / `test_log_shadow_two_arms_and_mismatch` / `test_log_shadow_baseline_none_skips` 登记进去。无则跳过。

- [ ] **Step 4: Commit (if anything changed)**

```bash
git add -A
git commit -m "test(shadow-parity): 红线守卫不回归 + 全量回归零退化" || echo "nothing to commit"
```

---

## Task 8: 更新 CLAUDE.md 风控红线条目

**Files:**
- Modify: `CLAUDE.md`（风控红线里"前向影子决策记录器"条目）

- [ ] **Step 1: 在影子记录器红线条目追加口径修正说明**

在 `CLAUDE.md` 的"前向影子决策记录器"红线条目末尾追加一句（替换"影子 − 实盘 = lever1 纯增量"的旧表述）：

```
**（2026-06-20 `fix-shadow-logger-replay-baseline-parity`）lever1 增量口径修正**：原 `live(real) vs replay(both-levers)` 混入复盘保真偏差（实证 37 条 shadow_holds 全是复盘失真、lever1 真实 delta=0），改为 **`replay(lever2-only baseline) vs replay(both-levers shadow)` 两臂同复盘（偏差抵消）+ baseline 复现自检闸**（`replay(lever2-only)` 的 accept/reject 不复现 live record → 标 `baseline_mismatch=True` 排除）。新增 jsonl 字段 `baseline_action`/`baseline_gate`/`baseline_mismatch`，`flip_kind` 改基于 baseline vs shadow。不动 ev-gate config（config-parity 假设已证伪）。
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(shadow-parity): CLAUDE.md 影子记录器红线口径修正"
```

---

## Self-Review 结论

- **Spec coverage**：delta spec 三个 requirement 全覆盖——「前向影子决策记录」(Task 3/4 两臂+字段)、「对比隔离 lever1 增量」(Task 2/4 baseline vs shadow)、「baseline 复现自检闸」(Task 1/3/4/6)。
- **Placeholder scan**：无 TBD/TODO，每个代码 step 含完整代码。
- **Type consistency**：`_is_accept`/`compute_baseline_mismatch`/`compute_flip_kind(baseline_action, shadow_action)`/`build_shadow_record(*, ts, symbol, real, baseline, shadow, tech_context)`/`load_shadow_opens()->(list,int)` 跨任务签名一致。
