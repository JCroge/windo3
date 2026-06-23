---
change: pattern-forward-shadow-recorder
design-doc: docs/superpowers/specs/2026-06-23-pattern-forward-shadow-recorder-design.md
base-ref: 198161b56382
archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

# Pattern Forward Shadow Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。Steps use `- [ ]`.

**Goal:** record-only 日线前向影子记录器,验证已确认信号 Bearish Engulfing|低位跌势 的真实前向 edge。

**Architecture:** 独立脚本 `pattern_forward_shadow.py`(--record / --settle),复用 cf_pattern_edge_discovery 的 context/atr/settle + resolve_counterfactual + cf_honesty_gate;observability-only,红线守卫扩展。

**Tech Stack:** Python3, sqlite3, json;复用既有研究模块,零新依赖。

archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

### Task 1: 记录器 + 结算器 `pattern_forward_shadow.py`

**Files:**
- Create: `pattern_forward_shadow.py`
- Reuse: `cf_pattern_edge_discovery`(context/atr/set_interval_windows)、`candlestick_patterns.detect_patterns`、`counterfactual_pnl.resolve_counterfactual`、`cf_honesty_gate.summarize_bucket`

- [ ] **Step 1: 实现**

```python
"""日线形态前向影子记录器(observability-only,严禁决策路径 import)。
--record: 每日在各 symbol 最新已闭合日线检测确认信号(Bearish Engulfing|low|down),write-only 追加。
--settle: 对 ≥10 日前未结算信号经 resolve_counterfactual 结算,报滚动前向净 R + 诚实门。"""
import sqlite3, json, os, argparse, datetime as dt
from cf_pattern_edge_discovery import context, atr, set_interval_windows, SL_ATR, TP_ATR, MAX_HOLD_DAYS, DB
from candlestick_patterns import detect_patterns
from counterfactual_pnl import resolve_counterfactual
from cf_honesty_gate import summarize_bucket

LOG = "data/pattern_forward_shadow.jsonl"
TARGET = ("Bearish Engulfing", -1)
TARGET_CTX = "low|down"

def _load_bars(sym):
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT open_time,open,high,low,close FROM klines WHERE symbol=? AND interval='1d' ORDER BY open_time",(sym,)).fetchall()
    conn.close()
    return [{"open_time":t,"open":o,"high":h,"low":l,"close":c} for t,o,h,l,c in rows]

def _symbols():
    conn=sqlite3.connect(DB); r=[x[0] for x in conn.execute("SELECT DISTINCT symbol FROM klines WHERE interval='1d'")]; conn.close(); return r

def _existing_keys():
    keys=set()
    if os.path.exists(LOG):
        for line in open(LOG):
            try: d=json.loads(line); keys.add((d["symbol"],d["detect_date_utc"]))
            except: pass
    return keys

def record():
    set_interval_windows("1d")
    keys=_existing_keys(); added=0
    for sym in _symbols():
        bars=_load_bars(sym)
        if len(bars) < 60: continue
        i=len(bars)-1  # 最新已闭合 bar(klines.db 只存已收盘)
        try:
            if context(bars,i)!=TARGET_CTX: continue
            if TARGET not in detect_patterns(bars[max(0,i-4):i+1]): continue
            a=atr(bars,i)
            if not a: continue
        except Exception: continue
        ddate=dt.datetime.utcfromtimestamp(bars[i]["open_time"]/1000).strftime("%Y-%m-%d")
        if (sym,ddate) in keys: continue
        entry=bars[i]["close"]
        rec={"detect_date_utc":ddate,"symbol":sym,"pattern":TARGET[0],"direction":-1,"context":TARGET_CTX,
             "entry":entry,"atr":a,"stop_loss":entry+SL_ATR*a,"take_profit":entry-TP_ATR*a,
             "max_hold_days":MAX_HOLD_DAYS,"settled":False}
        with open(LOG,"a") as f: f.write(json.dumps(rec)+"\n")
        keys.add((sym,ddate)); added+=1
    print(f"[record] 新增 {added} 条;日志累计 {len(keys)} 条。observability-only。")

def settle():
    if not os.path.exists(LOG): print("[settle] 无日志。"); return
    recs=[json.loads(l) for l in open(LOG) if l.strip()]
    now=dt.datetime.utcnow(); rs=[]; updated=0
    out=[]
    for d in recs:
        if d.get("settled"):
            out.append(d); 
            if d.get("net_r") is not None: rs.append(d["net_r"])
            continue
        ddt=dt.datetime.strptime(d["detect_date_utc"],"%Y-%m-%d")
        if (now-ddt).days < d["max_hold_days"]: out.append(d); continue  # 未成熟
        bars=_load_bars(d["symbol"]); start=int(ddt.timestamp()*1000)
        fut=[b for b in bars if b["open_time"]>start][:d["max_hold_days"]]
        if len(fut)<2: out.append(d); continue
        rec={"symbol":d["symbol"],"side":"short","entry_price":d["entry"],"stop_loss":d["stop_loss"],
             "take_profit":[d["take_profit"]],"leverage":1,"size_usdt":100.0,"funding_rate":0.0,"created_at":start/1000.0}
        r=resolve_counterfactual(rec,fut,max_hold_sec=d["max_hold_days"]*86400)
        if r.net_usdt is None: out.append(d); continue
        sl_dist=abs(d["entry"]-d["stop_loss"])/d["entry"]; net_r=r.net_usdt/(100.0*sl_dist) if sl_dist>0 else 0
        d.update(settled=True,net_r=net_r,outcome=r.outcome); rs.append(net_r); updated+=1; out.append(d)
    with open(LOG,"w") as f:
        for d in out: f.write(json.dumps(d)+"\n")
    print(f"[settle] 新结算 {updated};已结算样本 {len(rs)}")
    if rs:
        wins=sum(1 for x in rs if x>0)
        summ=summarize_bucket(wins=wins,losses=len(rs)-wins,net_usdt_samples=rs)
        print(f"  滚动前向: 胜率{wins/len(rs)*100:.1f}% 均净{sum(rs)/len(rs):+.3f}R 总{sum(rs):+.2f}R 诚实门={summ['verdict']}")
    print("observability-only —— 仅量化,不据此自动改 config/上 live。")

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--record",action="store_true"); ap.add_argument("--settle",action="store_true")
    a=ap.parse_args()
    if a.settle: settle()
    else: record()
```

- [ ] **Step 2: smoke**

Run: `python3 pattern_forward_shadow.py --record` → 打印新增条数(对现有 klines.db 最新 bar)。再跑一次 → 新增 0(幂等)。`python3 pattern_forward_shadow.py --settle` → 无成熟项时优雅(检测日就是今天)。
Expected: 不崩;幂等。

- [ ] **Step 3: Commit**

```bash
git add pattern_forward_shadow.py
git commit -m "feat(forward-shadow): 日线形态前向影子记录器+结算器(observability-only)"
```

archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

### Task 2: 红线守卫扩展

**Files:** Modify `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: 扩展 forbidden 集**

在 `test_decision_paths_do_not_read_pattern_research` 的 `forbidden` 元组加 `"pattern_forward_shadow"`。

- [ ] **Step 2: 运行**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py -q` → PASS(决策路径未引用)。

- [ ] **Step 3: Commit** `git commit -m "test(forward-shadow): 红线守卫覆盖 pattern_forward_shadow"`

archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

### Task 3: 单测 `tests/test_pattern_forward_shadow.py`

**Files:** Create `tests/test_pattern_forward_shadow.py`

- [ ] **Step 1: 写测试**(合成日线,不依赖网络,用 tmp 日志路径 monkeypatch LOG)

覆盖:(a) 命中 Bearish Engulfing|low|down → 记录 1 条;(b) 非 low|down 上下文 → 不记;(c) 幂等(重复 record 不增);(d) settle 对成熟记录回写 net_r/settled;(e) 防前视(只用末尾已闭合 bar)。构造合成 OHLC 序列(跌势 + 低位 + 看跌吞没末根)。

- [ ] **Step 2: 运行** `python3 -m pytest tests/test_pattern_forward_shadow.py -v` → PASS

- [ ] **Step 3: Commit** `git commit -m "test(forward-shadow): 记录器单测(命中/上下文/幂等/结算/防前视)"`

archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

### Task 4: 调度文档 + 回归

- [ ] **Step 1** README/runbook 加每日 cron 注记:`python3 pattern_forward_shadow.py --record`(UTC 收盘后)+ 定期 `--settle`。
- [ ] **Step 2** 全量 `python3 -m pytest -q` 无新回归(1 预存正交 fail 已知)。
- [ ] **Step 3** record smoke + 勾选 tasks.md + 诚实汇报(前向样本须数周)。Commit。

archived-with: 2026-06-23-pattern-forward-shadow-recorder
---

## Self-Review
- Spec 覆盖:Task1(记录+结算 R1/R2)、Task2(红线 R3)、Task3(场景测试)、Task4(调度+回归)。delta spec 3 Requirement 全覆盖。
- 复用契约:`context/atr/set_interval_windows/SL_ATR/TP_ATR/MAX_HOLD_DAYS/DB` 来自 cf_pattern_edge_discovery;`resolve_counterfactual` record 字段契约一致;`summarize_bucket` 关键字参数一致。
- 防前视:只用 klines.db 已收盘 bar 的 `bars[-1]`。
