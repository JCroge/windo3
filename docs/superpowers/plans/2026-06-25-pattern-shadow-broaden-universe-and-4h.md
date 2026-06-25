---
change: pattern-shadow-broaden-universe-and-4h
design-doc: docs/superpowers/specs/2026-06-25-pattern-shadow-broaden-universe-and-4h-design.md
base-ref: 2723673ce99b6991747cbb5572d9899e57cf7036
---

# pattern-shadow-broaden-universe-and-4h Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展形态前向影子 universe 30→~100 冻结快照 + 加 4h 并行影子（settle-when-determinable 使 4h 真快），加速诚实门样本累积。

**Architecture:** 复用 06-23 已建的 interval 感知（fetch `--intervals 1d,4h`、backtest `main(interval)`+`set_interval_windows`）；只需扩冻结 universe + 把日线硬编码的前向 runner 参数化（interval/窗口×bpd/settle-when-determinable/dedup-by-bar-ts/jsonl 分离）+ 加 4h launchd。observability-only、不接 live、不改 config。

**Tech Stack:** Python 3.9, ccxt(binance), sqlite3, pytest。

## Global Constraints

- **observability-only write-only**：runner 及产物严禁被 judge/executor/portfolio_risk_guard/reviewer/position_analyst import；不下单、不改 config.yaml、不接 live 决策。
- **冻结逻辑**：检测（Bearish Engulfing + context low|down）、ATR 退出（SL=1.5×/TP=3.0×）、flat 成本 0.2%、Wilson + `cf_honesty_gate` n<30 拒答——全部不动；唯一改的是 interval 参数化 + settle 时机（settle-when-determinable，净 R 值不变）。
- **窗口 ×bpd（interval 感知）**：`BARS_PER_DAY={"1d":1,"4h":6}`；4h 的 ATR_N=14×6=84 / RANGE_N=20×6=120 / MA_N=50×6=300 / 窗口=10×6=60 bar（与日线 10 日同时间口径）。
- **universe 冻结**：固化成代码常量，绝不每次动态 re-query。
- **部署副本**：repo 源 `scripts/fwdshadow_runner.py` 改完须 `cp` 到 `~/Library/Application Support/cryptoarb-fwdshadow/`。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `scripts/derive_universe.py` | 一次性派生 binance top~100 流动 universe | Create（构建期工具） |
| `fetch_historical_klines.py` | 历史抓取；`DEFAULT_SYMBOLS` | Modify（universe→~100） |
| `scripts/fwdshadow_runner.py` | 部署版自包含 runner | Modify（interval 参数化 + settle-when-determinable + dedup） |
| `pattern_forward_shadow.py` | lab 版 runner | Modify（同步 interval 参数化） |
| `tests/test_fwdshadow_runner.py` | runner 单测 | Create |
| `~/Library/.../fwdshadow_runner.py` | 部署副本 | `cp`（Task 7） |
| `~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.{record4h,settle4h}.plist` | 4h 调度 | Create（Task 7） |
| `README.md` | 运维文档 | Modify（Task 8） |

---

## Task 1: 派生并冻结扩展 universe

**Files:**
- Create: `scripts/derive_universe.py`
- Modify: `fetch_historical_klines.py`（`DEFAULT_SYMBOLS`）

- [ ] **Step 1: 写一次性派生脚本**

```python
# scripts/derive_universe.py —— 一次性派生 binance USDT-spot 成交量 top~100 流动 universe(构建期跑一次,结果固化进代码)
import ccxt
STABLES = {"USDC","USDT","FDUSD","TUSD","DAI","USDP","PYUSD","BUSD","EUR","GBP","USTC"}
def is_leveraged(base): return any(base.endswith(s) for s in ("UP","DOWN","BULL","BEAR"))
def derive(top=100):
    ex = ccxt.binance()
    ts = ex.fetch_tickers()
    rows = []
    for sym, t in ts.items():
        if not sym.endswith("/USDT"): continue
        base = sym[:-5]
        if base in STABLES or is_leveraged(base): continue
        qv = t.get("quoteVolume") or 0
        rows.append((base, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [b for b, _ in rows[:top]]
if __name__ == "__main__":
    syms = derive()
    print(f"# {len(syms)} symbols")
    print(", ".join(f'"{s}"' for s in syms))
```

- [ ] **Step 2: 运行派生，得到冻结列表**

Run: `python3 scripts/derive_universe.py`
Expected: 打印 ~100 个 base symbol（含原 30 的多数 + 新增）。**复制这份输出作为冻结常量**用于 Step 3。若网络失败，重试或换网络后再跑（此步必须真出列表，不得用占位）。

- [ ] **Step 3: 固化进 `fetch_historical_klines.py`**

把 Step 2 的列表写入 `DEFAULT_SYMBOLS`（替换原 30 币列表），保留原 30 币（它们成交量高、自然在 top100）。保持原格式：
```python
DEFAULT_SYMBOLS = ["BTC","ETH","SOL", ...<Step2 派生的 ~100>... ]
```

- [ ] **Step 4: 自检常量**

Run: `python3 -c "from fetch_historical_klines import DEFAULT_SYMBOLS as S; assert len(S)>=90, len(S); assert 'BTC' in S and 'ETH' in S; assert not any(x in S for x in ['USDC','FDUSD','TUSD','DAI']); print(len(S),'ok')"`
Expected: 打印 `<~100> ok`

- [ ] **Step 5: 提交**

```bash
git add scripts/derive_universe.py fetch_historical_klines.py
git commit -m "feat(pattern-shadow-4h): 派生并冻结 binance top~100 流动 universe"
```

---

## Task 2: 重抓新增币历史（1d + 4h）

**Files:** 无代码改动（网络数据操作）

- [ ] **Step 1: 增量抓取**

Run: `python3 fetch_historical_klines.py --symbols "$(python3 -c 'from fetch_historical_klines import DEFAULT_SYMBOLS as S;print(",".join(S))')" --intervals 1d,4h`
Expected: 逐币打印抓取进度，末尾 `n_ok/总数`；现有 30 币幂等去重、只增新币。失败 symbol 跳过+计数（不中断）。

- [ ] **Step 2: 验证落库**

Run: `python3 -c "import sqlite3;c=sqlite3.connect('data/klines.db');print(c.execute('SELECT interval,COUNT(DISTINCT symbol) FROM klines GROUP BY interval').fetchall())"`
Expected: `1d` 与 `4h` 的 symbol 数都 ≥90（接近 ~100，少数新币抓取失败可接受）。

- [ ] **Step 3: 记录覆盖（无需提交，数据不入 git）**

把 n_ok/失败 symbol 记下，供 verify 报告引用。

---

## Task 3: 重跑回测确认宽 universe edge（re-validate gate）

**Files:** 无代码改动（运行既有 backtest）

- [ ] **Step 1: 跑 1d 回测**

Run: `python3 -c "import cf_pattern_edge_discovery as m; m.main('1d')" 2>&1 | tail -30`
Expected: 出 `Bearish Engulfing|低位跌势` 在宽 universe 的 OOS/FDR/诚实门结果。**记录是否仍过门 + 净 R**。

- [ ] **Step 2: 跑 4h 回测**

Run: `python3 -c "import cf_pattern_edge_discovery as m; m.main('4h')" 2>&1 | tail -30`
Expected: 4h 同上。**记录 4h edge 是否仍成立**。

- [ ] **Step 3: gate 判定（写入 verify 报告，不阻塞代码）**

- 若 1d edge 仍过门 → 扩盘成功，继续。
- 若 1d edge 在宽 universe **翻负/不过门** → 异常，暂停并向用户报告（universe 选择或数据问题）。
- 若 4h 不过门 → 4h 仅当探索（README/verify 标注、不下结论），**不阻塞日线**。
- 三种情形都把结论记入 verify 报告。

---

## Task 4: 部署版 runner interval 参数化 + settle-when-determinable

**Files:**
- Modify: `scripts/fwdshadow_runner.py`
- Test: `tests/test_fwdshadow_runner.py`

**Interfaces:**
- Produces: `resolve_signal(rec, fut_bars, window_bars)->(net_r:float, outcome:str)|None`（纯函数，settle 核心）；`record(interval)`；`settle(interval)`；`SYMBOLS`、`BARS_PER_DAY`、`_interval_windows(interval)->(atr_n,range_n,ma_n,window_bars)`。
- Consumes: Task 1 的冻结 `DEFAULT_SYMBOLS`（runner `SYMBOLS` 用同一份）。

- [ ] **Step 1: 写失败测试（settle-when-determinable + dedup + 窗口×bpd）**

```python
# tests/test_fwdshadow_runner.py
import importlib.util, os, sys
# 加载 repo 源 scripts/fwdshadow_runner.py 为模块
_spec = importlib.util.spec_from_file_location("fwdshadow_runner", os.path.join(os.path.dirname(__file__), "..", "scripts", "fwdshadow_runner.py"))
fr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fr)

def _bars(closes_hl):
    # closes_hl: list of (open_time_ms, high, low, close)
    return [{"open_time": t, "open": c, "high": h, "low": l, "close": c} for (t, h, l, c) in closes_hl]

def _rec(entry=100.0, sl=104.0, tp=88.0, max_hold_days=10, interval="1d"):
    # short: sl>entry>tp（与 record 一致）
    return {"detect_bar_open_time": 1000, "entry": entry, "stop_loss": sl, "take_profit": tp,
            "max_hold_days": max_hold_days, "interval": interval, "symbol": "X"}

def test_resolve_early_sl_settles_before_window_full():
    # 第2根触 SL(high>=104) → 立即结算 sl,不等满窗
    fut = _bars([(2000,101,99,100),(3000,105,100,104),(4000,103,99,100)])  # 仅3根,window=10
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "sl"

def test_resolve_early_tp_settles():
    fut = _bars([(2000,101,99,100),(3000,99,87,88)])  # 第2根触 TP(low<=88)
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "tp"

def test_resolve_no_exit_window_not_full_stays_unsettled():
    # 无退出 + bar 数 < window → None(不提前判 expired)
    fut = _bars([(2000,101,99,100),(3000,101,99,100),(4000,101,99,100)])
    assert fr.resolve_signal(_rec(), fut, window_bars=10) is None

def test_resolve_no_exit_window_full_expired():
    # 无退出 + 整窗满 → expired
    fut = _bars([(1000+i, 101, 99, 100) for i in range(10)])
    r = fr.resolve_signal(_rec(), fut, window_bars=10)
    assert r is not None and r[1] == "expired"

def test_interval_windows_4h_scaled_by_bpd():
    atr_n, range_n, ma_n, window = fr._interval_windows("4h")
    assert (atr_n, range_n, ma_n, window) == (84, 120, 300, 60)
    assert fr._interval_windows("1d") == (14, 20, 50, 10)

def test_dedup_key_includes_bar_ts_and_interval():
    # 同 symbol 同 UTC 日不同 4h bar → 不同 key,不塌缩
    k1 = fr._dedup_key("X", 1000, "4h")
    k2 = fr._dedup_key("X", 1000 + 4*3600*1000, "4h")
    assert k1 != k2

def test_jsonl_path_per_interval():
    assert fr._log_path("1d").endswith("pattern_forward_shadow.jsonl")
    assert fr._log_path("4h").endswith("pattern_forward_shadow_4h.jsonl")
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_fwdshadow_runner.py -q`
Expected: FAIL（`resolve_signal`/`_interval_windows`/`_dedup_key`/`_log_path` 未定义）

- [ ] **Step 3: 改 `scripts/fwdshadow_runner.py`**

(a) 顶部加 `BARS_PER_DAY = {"1d": 1, "4h": 6}` 与基准天数常量 `ATR_DAYS, RANGE_DAYS, MA_DAYS, HOLD_DAYS = 14, 20, 50, 10`（替换原 `ATR_N, RANGE_N, MA_N = 14, 20, 50` 硬编码）。

```python
BARS_PER_DAY = {"1d": 1, "4h": 6}
ATR_DAYS, RANGE_DAYS, MA_DAYS, HOLD_DAYS = 14, 20, 50, 10

def _interval_windows(interval):
    bpd = BARS_PER_DAY.get(interval, 1)
    return ATR_DAYS * bpd, RANGE_DAYS * bpd, MA_DAYS * bpd, HOLD_DAYS * bpd

def _dedup_key(symbol, bar_open_time, interval):
    return (symbol, int(bar_open_time), interval)

DATA_DIR = os.environ.get("FWDSHADOW_DIR", os.path.dirname(os.path.abspath(__file__)))
def _log_path(interval):
    name = "pattern_forward_shadow.jsonl" if interval == "1d" else f"pattern_forward_shadow_{interval}.jsonl"
    return os.path.join(DATA_DIR, name)
```

(b) `load_bars(sym, interval)`：把 `WHERE interval='1d'` 改为 `WHERE interval=?` 参数化。

(c) `atr/context/is_bearish_engulfing` 等用到 `ATR_N/RANGE_N/MA_N` 的地方改为接收传入的窗口值（由 `record(interval)` 用 `_interval_windows(interval)` 算出后传入；不要再读全局常量）。

(d) 提取纯函数 `resolve_signal(rec, fut_bars, window_bars)`：
```python
def resolve_signal(rec, fut_bars, window_bars):
    fut = fut_bars[:window_bars]
    entry, sl, tp = rec["entry"], rec["stop_loss"], rec["take_profit"]  # short: sl>entry>tp
    for b in fut:
        hit_sl = b["high"] >= sl
        hit_tp = b["low"] <= tp
        if hit_sl:  # 同根 SL-first 保守(含 hit_sl and hit_tp)
            return _net_r(entry, sl, sl), "sl"
        if hit_tp:
            return _net_r(entry, tp, sl), "tp"
    if len(fut) >= window_bars:           # 整窗满且无退出 → expired
        return _net_r(entry, fut[-1]["close"], sl), "expired"
    return None                           # 窗口未满 → 留未结算

def _net_r(entry, exit_px, sl):
    gross_pct = (entry - exit_px) / entry            # 空单
    sl_dist_pct = abs(sl - entry) / entry
    net_usdt = SIZE * LEV * gross_pct - SIZE * LEV * COST_RT
    risk = SIZE * LEV * sl_dist_pct
    return (net_usdt / risk) if risk > 0 else 0.0
```

(e) `_settle_one(rec, interval)`：算 `_, _, _, window_bars = _interval_windows(interval)`，`bars = load_bars(rec["symbol"], interval)`，`start = rec["detect_bar_open_time"]`，`fut = [b for b in bars if b["open_time"] > start]`，`return resolve_signal(rec, fut, window_bars)`。

(f) `settle(interval)`：**删除** `(now - ddt).days < max_hold_days` 日历门；改为对每条未结算 rec 直接 `r = _settle_one(rec, interval)`，`r is None` 则保持未结算。读写 `_log_path(interval)`。诚实门/Wilson 报告不变。

(g) `record(interval)`：用 `_interval_windows(interval)` 取窗口；`load_bars(sym, interval)`；`i=len(bars)-1`（最新已闭合 `<interval>` bar）；dedup 用 `_dedup_key(sym, bars[i]["open_time"], interval)`；rec 增 `detect_bar_open_time=bars[i]["open_time"]` 与 `interval=interval`；写 `_log_path(interval)`。`SYMBOLS` 用 Task1 冻结列表（与 `fetch_historical_klines.DEFAULT_SYMBOLS` 同一份）。

(h) `_existing_keys(interval)`：读 `_log_path(interval)`，键用 `_dedup_key(d["symbol"], d["detect_bar_open_time"], d["interval"])`；对旧记录无 `detect_bar_open_time` 的 fallback 用 `detect_date_utc` 字符串保持兼容（不破已有 5 条日线记录）。

(i) `argparse` 加 `--interval`，`choices=["1d","4h"]`，`default="1d"`；`record`/`settle` 接收 `a.interval`。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_fwdshadow_runner.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add scripts/fwdshadow_runner.py tests/test_fwdshadow_runner.py
git commit -m "feat(pattern-shadow-4h): 部署版 runner interval 参数化 + settle-when-determinable + dedup-by-bar-ts"
```

---

## Task 5: lab 版 runner 同步参数化

**Files:**
- Modify: `pattern_forward_shadow.py`

**Interfaces:**
- Consumes: `cf_pattern_edge_discovery.set_interval_windows`（已 import）、`resolve_counterfactual`。

- [ ] **Step 1: 加 `--interval` + 同口径改动**

`pattern_forward_shadow.py`（lab 版，import 回测 helper）：
- `argparse` 加 `--interval choices=["1d","4h"] default="1d"`。
- record/settle 读 `interval` 参数；`set_interval_windows(interval)`（已 import，自动 ×bpd）。
- `load`：`WHERE interval=?` 参数化（现硬编码 `interval='1d'` 两处 line 20/31）。
- jsonl 路径按 interval 分离（1d→`pattern_forward_shadow.jsonl` / 4h→`pattern_forward_shadow_4h.jsonl`）。
- settle 改 settle-when-determinable（与 Task4 同语义：早退出立即结算/整窗满 expired/未满留 None），record 带 `detect_bar_open_time`、dedup 含 bar_ts+interval。

- [ ] **Step 2: 编译 + 红线守卫**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q && env PYTHONPYCACHEPREFIX=/private/tmp/crypto_audit_pycache python3 -m compileall -q pattern_forward_shadow.py scripts/fwdshadow_runner.py`
Expected: 红线守卫 PASS（`test_decision_paths_do_not_read_pattern_research` 含 `pattern_forward_shadow`）+ compile 无输出

- [ ] **Step 3: 提交**

```bash
git add pattern_forward_shadow.py
git commit -m "feat(pattern-shadow-4h): lab 版 runner 同步 interval 参数化 + settle-when-determinable"
```

---

## Task 6: 全量基线

**Files:** 无

- [ ] **Step 1: 全量 pytest**

Run: `python3 -m pytest -q`
Expected: PASS（基线 1430 + 新 `test_fwdshadow_runner.py` 7，无新增 fail）

- [ ] **Step 2: 提交（若 tasks.md 已勾选）**

```bash
git add openspec/changes/pattern-shadow-broaden-universe-and-4h/tasks.md
git commit -m "chore(pattern-shadow-4h): 全量基线绿 + tasks 勾选"
```

---

## Task 7: 部署 + 4h launchd jobs

**Files:**
- `cp` 到 `~/Library/Application Support/cryptoarb-fwdshadow/fwdshadow_runner.py`
- Create: `~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.record4h.plist`
- Create: `~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.settle4h.plist`

- [ ] **Step 1: 部署 runner 副本**

Run: `cp scripts/fwdshadow_runner.py "$HOME/Library/Application Support/cryptoarb-fwdshadow/fwdshadow_runner.py" && echo deployed`
Expected: `deployed`

- [ ] **Step 2: 写 record4h plist（每 4h，对齐 UTC 4h 收盘=本地 CST 08/12/16/20/00/04 +7min）**

`~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.record4h.plist`：ProgramArguments=`/usr/bin/python3 <runner> --record --interval 4h`，WorkingDirectory + `FWDSHADOW_DIR` env 同现有 record plist；`StartCalendarInterval` 为**数组** 6 条 `{Hour:0/4/8/12/16/20, Minute:7}`（注：本地时区，可直接用 0/4/8/12/16/20 本地时；时区偏移不影响"记已闭合 bar"正确性，只影响及时性）；StandardOut/ErrPath=`~/Library/Logs/pattern_forward_shadow.log`。

- [ ] **Step 3: 写 settle4h plist（每日 10:07）**

同上但 `--settle --interval 4h`，`StartCalendarInterval={Hour:10, Minute:7}`。

- [ ] **Step 4: 加载 + 即时验证**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.record4h.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cryptoarb.pattern-forward-shadow.settle4h.plist
launchctl kickstart -k gui/$(id -u)/com.cryptoarb.pattern-forward-shadow.record4h
sleep 30; tail -15 ~/Library/Logs/pattern_forward_shadow.log
```
Expected: 日志出 `[record]` 行（4h 新增 N 条或"无 4h 信号"），**无 `Operation not permitted`**（自包含 runner 不碰 Desktop，FDA 无关）。

---

## Task 8: README + 真跑收尾

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README §日线形态前向影子记录器**

改为「扩展 universe(~100 冻结快照) + 1d/4h 双轨」：加 4h launchd 命令、`--interval` 用法、冻结 universe + 前向=回测可比性说明、**settle-when-determinable**（4h 早退出快、净 R 同值）、4h margin 薄(+0.208R vs +0.326R)/低置信注脚、新 jsonl `pattern_forward_shadow_4h.jsonl`。

- [ ] **Step 2: 日线 record 真跑（确认扩 universe 生效）**

Run: `cd "$HOME/Library/Application Support/cryptoarb-fwdshadow" && python3 fwdshadow_runner.py --record --interval 1d 2>&1 | tail -3`
Expected: `[record]` 行，信号数应较旧 30 币时代多（扩 universe 生效）。

- [ ] **Step 3: 4h record 真跑**

Run: `cd "$HOME/Library/Application Support/cryptoarb-fwdshadow" && python3 fwdshadow_runner.py --record --interval 4h 2>&1 | tail -3`
Expected: 出首批 4h 信号（或诚实"无 4h 信号"），写 `pattern_forward_shadow_4h.jsonl`。

- [ ] **Step 4: 提交 + 结论入 verify**

```bash
git add README.md
git commit -m "docs(pattern-shadow-4h): README 扩 universe + 1d/4h 双轨 + settle-when-determinable"
```
verify 报告记：宽 universe 1d/4h 回测 edge 裁定（Task3）+ 前向首跑信号数（旧 30 vs 新~100）+ 预计 n≥30 提速。**不改 config、不上 live、诚实门未过前不下前向结论。**

---

## Self-Review

- **Spec coverage**：MODIFIED 记录(interval+dedup-by-bar-ts) → Task4g/4h、Task5；MODIFIED settle(settle-when-determinable+窗口×bpd) → Task4d/e/f、Task5；ADDED 冻结 universe → Task1；红线 → Task5 Step2。delta spec 全覆盖。
- **Placeholder scan**：无 TBD；TDD 任务(4)含完整测试+实现代码；运维任务(2/3/7/8)给精确命令+期望。
- **Type consistency**：`resolve_signal(rec,fut_bars,window_bars)->(net_r,outcome)|None`、`_interval_windows(interval)->(atr_n,range_n,ma_n,window_bars)`、`_dedup_key(symbol,bar_open_time,interval)`、`_log_path(interval)`——Task4 定义、Task4 测试/Task5 复用一致。
- **关键风险**：settle 改动须保证日线净 R 值不变（只时点提前）——Task4 测试锁早退出/未满不 expired/整窗 expired 三态。
