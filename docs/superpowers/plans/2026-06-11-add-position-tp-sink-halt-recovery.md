---
change: add-position-tp-sink-halt-recovery
design-doc: docs/superpowers/specs/2026-06-11-add-position-tp-sink-halt-recovery-design.md
base-ref: cf34aa61e6b886c0fbee055e89e239a9387de81e
---

# 加仓 TP 收口 + halt 恢复语义诚实 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除加仓后 TP 不变量误破导致的全系统熔断事故（P1-01），并让 `/resume_symbol` 在全局 halt 仍在时诚实回显（P2-02）。

**Architecture:** P1-01 改 `ContractExecutor.add_to_position` 一段：TP 重算改为按每个 level 距旧均价比例平移整个 `take_profit_levels`，经唯一收口 `_set_position_tp` 写入。P2-02 在 agent executor 的 `resume_symbol` handler 给 `symbol_halt_cleared` 事件附 `global_halt_active`（读 `self._halt_state`），TG 渲染时追加 `/resume` 指引。两处均 surgical，零安全姿态改动。

**Tech Stack:** Python 3.9, pytest, OKX ccxt（mock）。测试落 `test_partial_tp_lifecycle.py`、`test_tg_symbol_halt_control.py`。

---

## File Structure

- Modify: `executor.py` — `add_to_position` TP 重算段（约 3178-3183）
- Modify: `agents/trading/executor.py` — `resume_symbol` handler（约 119-128）给 `symbol_halt_cleared` 附 `global_halt_active`
- Modify: `agents/trading/telegram_notifier.py` — `_handle_risk_alert` 的 `symbol_halt_cleared` 分支（约 227-230）按 `global_halt_active` 追加提示
- Test: `test_partial_tp_lifecycle.py` — 加仓 TP 不变量 + tp_filled==1 + 多级比例
- Test: `test_tg_symbol_halt_control.py` — resume_symbol 全局 halt 回显

---

## Task 1: P1-01 加仓 TP 经 `_set_position_tp` 收口

**Files:**
- Modify: `executor.py:3178-3183`（`add_to_position` TP 重算段）
- Test: `test_partial_tp_lifecycle.py`（新增 `TestAddPositionTpInvariant`）

- [ ] **Step 1: 写失败测试 — 加仓后不变量保持 + 不 halt**

在 `test_partial_tp_lifecycle.py` 末尾新增（复用文件内 `_make_executor`）：

```python
class TestAddPositionTpInvariant:
    def _open_long(self, ex, entry=100.0):
        pos = {
            'symbol': 'X-USDT-SWAP', 'side': 'long', 'amount': 1.0,
            'amount_usdt': 30.0, 'entry_price': entry,
            'stop_loss': entry * 0.95, 'original_sl': entry * 0.95,
            'take_profit': entry * 1.10,
            'take_profit_levels': [entry * 1.10, entry * 1.20],
            'tp_filled': 0, 'protection_state': 'protected', 'atr_pct': 0.02,
            'leverage': 1,
        }
        ex.positions['X-USDT-SWAP'] = pos
        return pos

    def _wire_add(self, ex, fill_price):
        # verified against executor.py:3080-3188
        ex.can_open_new_okx = lambda: True
        ex.is_symbol_halted = lambda s: False
        ex.get_balance = MagicMock(return_value=1000.0)
        ex.risk_manager.max_trade_amount = 30.0
        ex.risk_manager.check_can_trade = MagicMock(return_value=(True, ''))
        ex.balance_adapter = None  # → 走 exchange.fetch_balance()['USDT']['free']
        ex.exchange.fetch_balance = MagicMock(return_value={'USDT': {'free': 100000.0}})
        ex.exchange.set_leverage = MagicMock()
        ex.caps = None
        ex._build_open_order_params = MagicMock(return_value={})
        ex._replace_protective_sl = MagicMock()
        ex._save_positions = MagicMock()
        ex.idempotency = None
        ex.exchange.create_order = MagicMock(return_value={'id': 'ord1'})
        # add_to_position 取价入口 = exchange.fetch_ticker(symbol)['last']（3112-3113）
        ex.exchange.fetch_ticker = MagicMock(return_value={'last': fill_price})

    def test_invariant_holds_after_add(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)
        self._wire_add(ex, fill_price=110.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        assert pos['take_profit'] == pos['take_profit_levels'][0]
        # 加仓后跑止损轮询不得触发 tp_invariant_breach
        ex._update_trailing('X-USDT-SWAP', pos, pos['entry_price'])
        for call in ex._halt_symbol.call_args_list:
            assert call.kwargs.get('reason') != 'tp_invariant_breach'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest test_partial_tp_lifecycle.py::TestAddPositionTpInvariant::test_invariant_holds_after_add -v`
Expected: FAIL（当前 `take_profit != take_profit_levels[0]`，或 `_update_trailing` 调 `_halt_symbol(reason='tp_invariant_breach')`）

- [ ] **Step 3: 实现 — TP 按 level 平移经 sink**

`executor.py` 替换 3178-3183：

```python
            if old_tp and old_entry > 0:
                old_levels = position.get('take_profit_levels') or [old_tp]
                new_levels = []
                for lvl in old_levels:
                    dist = abs(lvl - old_entry) / old_entry
                    new_levels.append(new_entry * (1 + dist) if side == 'long'
                                      else new_entry * (1 - dist))
                self._set_position_tp(position, new_levels[0], new_levels)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest test_partial_tp_lifecycle.py::TestAddPositionTpInvariant::test_invariant_holds_after_add -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add executor.py test_partial_tp_lifecycle.py
git commit -m "fix(executor): route add_to_position TP through _set_position_tp (P1-01)"
```

---

## Task 2: P1-01 边界 — tp_filled>0 与多级比例

**Files:**
- Test: `test_partial_tp_lifecycle.py::TestAddPositionTpInvariant`

- [ ] **Step 1: 写测试 — tp_filled==1 加仓不破不变量、tp_filled 不变；多级比例保持**

```python
    def test_add_after_partial_tp_fill(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)
        pos['tp_filled'] = 1  # TP1 已部分成交
        self._wire_add(ex, fill_price=112.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        assert pos['tp_filled'] == 1
        assert pos['take_profit'] == pos['take_profit_levels'][0]
        ex._update_trailing('X-USDT-SWAP', pos, pos['entry_price'])
        for call in ex._halt_symbol.call_args_list:
            assert call.kwargs.get('reason') != 'tp_invariant_breach'

    def test_multi_level_ratios_preserved(self):
        ex = _make_executor()
        pos = self._open_long(ex, entry=100.0)  # levels=[110,120] → 距 10%/20%
        self._wire_add(ex, fill_price=120.0)
        ex._halt_symbol = MagicMock()
        ex.add_to_position('X-USDT-SWAP', 'long', size_pct=1.0)
        new_entry = pos['entry_price']
        levels = pos['take_profit_levels']
        assert abs((levels[0] - new_entry) / new_entry - 0.10) < 1e-9
        assert abs((levels[1] - new_entry) / new_entry - 0.20) < 1e-9
```

- [ ] **Step 2: 跑测试确认通过**

Run: `python3 -m pytest test_partial_tp_lifecycle.py::TestAddPositionTpInvariant -v`
Expected: 3 passed（含 Task 1）

- [ ] **Step 3: 提交**

```bash
git add test_partial_tp_lifecycle.py
git commit -m "test(executor): add tp_filled>0 and multi-level ratio cases for add TP shift (P1-01)"
```

---

## Task 3: P2-02 halt 恢复语义诚实

**Files:**
- Modify: `agents/trading/executor.py:119-128`（`resume_symbol` handler，`symbol_halt_cleared` payload）
- Modify: `agents/trading/telegram_notifier.py:227-230`（`symbol_halt_cleared` 渲染）
- Test: `test_tg_symbol_halt_control.py`（新增 `TestResumeSymbolGlobalHaltHint`）

- [ ] **Step 1: 写失败测试 — symbol_halt_cleared 携带 global_halt_active**

在 `test_tg_symbol_halt_control.py` 新增（参照文件内 agent-handler 测试风格，构造 resume_symbol system_command 并断言发布的 risk_alert payload）：

```python
class TestResumeSymbolGlobalHaltHint:
    def _make_agent_executor_with_halt(self, global_halted: bool):
        from agents.trading.executor import MultiExecutor
        ag = MultiExecutor.__new__(MultiExecutor)
        ag.logger = logging.getLogger('test_resume_hint')
        ag.executor = _make_executor_stub()
        ag.executor._halted_symbols = {"XLM-USDT-SWAP": {"reason": "sl_replace_failed"}}
        ag.executor._normalize_symbol = lambda s: "XLM-USDT-SWAP"
        ag._halt_state = MagicMock()
        ag._halt_state.can_open_new = (not global_halted)
        ag.publish = AsyncMock()
        return ag

    @pytest.mark.asyncio
    async def test_cleared_payload_flags_global_halt(self):
        ag = self._make_agent_executor_with_halt(global_halted=True)
        await ag._handle_system_command({
            'payload': {'command': 'resume_symbol', 'symbol': 'XLM', 'source': 'telegram'}
        })
        cleared_calls = [c for c in ag.publish.call_args_list
                         if c.args and c.args[0] == 'risk_alert'
                         and c.args[1].get('type') == 'symbol_halt_cleared']
        assert cleared_calls, "应发布 symbol_halt_cleared"
        assert cleared_calls[0].args[1].get('global_halt_active') is True
```

> 执行时核对 system_command 的实际入口方法名（grep `resume_symbol` 所在 handler，可能是 `_handle_system_command` 或 `on_message` 分发），按实调整调用方式，保持断言意图。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest test_tg_symbol_halt_control.py::TestResumeSymbolGlobalHaltHint -v`
Expected: FAIL（payload 无 `global_halt_active`）

- [ ] **Step 3: 实现 — handler 附 global_halt_active**

`agents/trading/executor.py` 的 `resume_symbol` 分支，`cleared > 0` 时的 `symbol_halt_cleared` publish 加字段：

```python
                if cleared > 0:
                    global_halt_active = not self._halt_state.can_open_new
                    await self.publish('risk_alert', {
                        'type': 'symbol_halt_cleared',
                        'symbol': normalized,
                        'source': source,
                        'global_halt_active': global_halt_active,
                    })
```

- [ ] **Step 4: 实现 — TG 渲染追加提示**

`agents/trading/telegram_notifier.py` 的 `symbol_halt_cleared` 分支：

```python
        if alert_type == 'symbol_halt_cleared':
            text = f"✅ {symbol} per-symbol halt 已解除 (来源: {payload.get('source', '?')})"
            if payload.get('global_halt_active'):
                text += "\n⚠️ 全局仍 halt，开新仓仍被阻断；请用 /resume（带对账）解除全局熔断"
            await self._send_message(text)
            return
```

- [ ] **Step 5: 跑测试确认通过 + 补全局未 halt 不提示用例**

补一条 `test_no_hint_when_global_clear`（`global_halted=False` → payload `global_halt_active is False`）。

Run: `python3 -m pytest test_tg_symbol_halt_control.py::TestResumeSymbolGlobalHaltHint -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add agents/trading/executor.py agents/trading/telegram_notifier.py test_tg_symbol_halt_control.py
git commit -m "fix(tg): honest global-halt hint on /resume_symbol when global halt persists (P2-02)"
```

---

## Task 4: 回归 + 同构记录 + 收尾

**Files:**
- Modify: `openspec/changes/add-position-tp-sink-halt-recovery/tasks.md`（勾选）

- [ ] **Step 1: 全量回归**

Run: `python3 -m pytest -q`
Expected: `1066+ passed`（新增用例后基线上调），`4 deselected, 1 warning`

- [ ] **Step 2: 编译检查**

Run: `env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q executor.py agents utils`
Expected: 无输出（通过）

- [ ] **Step 3: 记录 event_backtest 同构理由**

确认 `grep -niE "add_to_position|加仓" event_backtest.py` 为空 → 在 change 的 tasks.md 标注"加仓 TP 重算 live-only，event_backtest 无加仓决策路径，无同构对象需同步"。

- [ ] **Step 4: 勾选 change tasks.md 并提交**

```bash
git add openspec/changes/add-position-tp-sink-halt-recovery/tasks.md
git commit -m "docs(comet): mark tasks complete + event_backtest isomorphism note"
```

---

## Self-Review

- **Spec coverage**：delta `entry-drift-policy`（add 路径经 sink + tp_filled-safe + 多级比例）→ Task 1/2；delta `tg-symbol-halt-control`（全局 halt 仍在诚实回显 + clear_symbol_halt 返回类型不变）→ Task 3。全覆盖。
- **Placeholder scan**：实现代码完整；测试中两处「执行时核对入口名」是真实的实现期校验点（取价入口、system_command handler 名），非 placeholder——意图与断言已写定。
- **Type consistency**：`_set_position_tp(position, tp_first, tp_levels)` 签名一致；`global_halt_active` 字段在 handler 与 TG 渲染、测试三处一致；`clear_symbol_halt` 返回 int 不变。
