---
change: daily-pattern-edge-lab
design-doc: docs/superpowers/specs/2026-06-23-daily-pattern-edge-lab-design.md
base-ref: dde4bd5622458ee9745304cea479ffcc16aebbf7
---

# Daily Pattern Edge Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个 observability-only 研究骨架,判定日线蜡烛形态在样本外有无可交易 edge。

**Architecture:** 历史 OHLC 抓取(ccxt→klines.db)→ 手写形态库(~28种,固定阈值)→ 反事实回测驱动(ATR退出+上下文6桶+OOS三分+FDR+诚实门+加权),镜像现有 cf_oi_divergence_ab.py。决策/风控路径禁读(红线守卫)。

**Tech Stack:** Python3, ccxt 4.5.52, pandas/numpy, sqlite3, pytest;复用 utils/counterfactual_pnl.py + utils/cf_honesty_gate.py。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `fetch_historical_klines.py` | 分页历史 OHLC 抓取器 → klines.db | 改造 |
| `utils/candlestick_patterns.py` | ~28 形态识别(固定阈值,返回 name+direction) | 新建 |
| `cf_pattern_edge_discovery.py` | 回测驱动:load→fire→settle→aggregate→gate→report | 新建 |
| `tests/test_candlestick_patterns.py` | 形态库 golden + near-miss 单测 | 新建 |
| `tests/test_cf_red_line_guard.py` | 加形态研究禁读守卫 | 修改 |

---

### Task 1: 历史抓取器改造(分页/多币/多周期)

**Files:**
- Modify: `fetch_historical_klines.py`(整体重写为可复用模块 + CLI)
- Test: 手动验证(网络依赖,不进 pytest)

- [ ] **Step 1: 重写 fetch_historical_klines.py**

```python
#!/usr/bin/env python3
"""分页历史 OHLC 抓取器 → data/klines.db。observability-only 研究数据。"""
import ccxt, sqlite3, time, argparse
from datetime import datetime, timezone

DB = "data/klines.db"
DEFAULT_SYMBOLS = ["BTC","ETH","SOL","XRP","DOGE","BCH","UNI","NEAR","XLM","SUI",
    "WLD","TRUMP","AVAX","LINK","LTC","ADA","TON","APT","ARB","FIL","PEPE","ONDO",
    "TAO","INJ","SEI","TIA","RUNE","AAVE","MKR","ENA"]

def _ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS klines(
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, interval TEXT,
        open_time INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL,
        close_time INTEGER, quote_volume REAL, trades INTEGER,
        UNIQUE(symbol, interval, open_time))''')

def fetch_symbol(ex, conn, symbol, interval, max_bars=4000):
    """分页拉取至 max_bars 或无更多数据。INSERT OR IGNORE 幂等。"""
    pair = f"{symbol}/USDT"
    all_rows, since = [], None
    while len(all_rows) < max_bars:
        try:
            o = ex.fetch_ohlcv(pair, interval, since=since, limit=1000)
        except Exception as e:
            print(f"  {symbol} {interval} 抓取失败: {str(e)[:60]}"); break
        if not o: break
        all_rows += o
        if len(o) < 1000: break
        since = o[-1][0] + 1
        time.sleep(ex.rateLimit/1000)
    ins = 0
    for t,op,hi,lo,cl,vol in all_rows[:max_bars]:
        try:
            conn.execute('INSERT OR IGNORE INTO klines(symbol,interval,open_time,open,high,low,close,volume,close_time) VALUES(?,?,?,?,?,?,?,?,?)',
                (symbol, interval, t, op, hi, lo, cl, vol, t))
            ins += conn.total_changes  # net new tracked below
        except sqlite3.Error: pass
    conn.commit()
    return all_rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="1d,4h")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--exchange", default="binance")
    args = ap.parse_args()
    ex = getattr(ccxt, args.exchange)()
    conn = sqlite3.connect(DB); _ensure_table(conn)
    syms = args.symbols.split(","); intervals = args.intervals.split(",")
    for interval in intervals:
        print(f"=== interval={interval} ===")
        for s in syms:
            rows = fetch_symbol(ex, conn, s, interval)
            if rows:
                first = datetime.fromtimestamp(rows[0][0]/1000, tz=timezone.utc).date()
                flag = " ←短史" if len(rows) < 200 else ""
                print(f"  {s:<8} {len(rows):>5}根 起{first}{flag}")
            else:
                print(f"  {s:<8} 无数据")
    # 入库自检
    cur = conn.execute('SELECT interval,COUNT(*),COUNT(DISTINCT symbol) FROM klines GROUP BY interval')
    print("入库汇总:", cur.fetchall())
    conn.close()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑日线 + 4h 入库**

Run: `python3 fetch_historical_klines.py --intervals 1d,4h`
Expected: 打印每币根数(多数 1000 根日线/起 2023-09),入库汇总含 ('1d',~28000,~30) 与 ('4h',...)

- [ ] **Step 3: 验证幂等**

Run: `python3 fetch_historical_klines.py --intervals 1d` 再跑一次,对比 `sqlite3 data/klines.db "SELECT COUNT(*) FROM klines WHERE interval='1d'"` 两次相等。
Expected: 行数不变(INSERT OR IGNORE 幂等)

- [ ] **Step 4: Commit**

```bash
git add fetch_historical_klines.py
git commit -m "feat(pattern-lab): 分页历史OHLC抓取器(多币/多周期)落klines.db"
```

---

### Task 2: 形态库(附录A,固定阈值)

**Files:**
- Create: `utils/candlestick_patterns.py`
- Test: `tests/test_candlestick_patterns.py`

- [ ] **Step 1: 写失败测试(golden + near-miss)**

```python
# tests/test_candlestick_patterns.py
from utils.candlestick_patterns import detect_patterns
def _b(o,h,l,c): return {"open":o,"high":h,"low":l,"close":c}

def test_hammer_golden():
    bars=[_b(100,101,90,100.5)]  # 长下影 小实体 短上影
    hits=detect_patterns(bars)
    assert ("Hammer",+1) in hits

def test_hammer_near_miss_no_long_lower_wick():
    bars=[_b(100,101,99.5,100.5)]  # 无长下影
    hits=detect_patterns(bars)
    assert not any(n=="Hammer" for n,_ in hits)

def test_bullish_engulfing_golden():
    bars=[_b(100,100.5,98,98.5), _b(98,101,97.8,100.8)]  # 阴后大阳吞没
    hits=detect_patterns(bars)
    assert ("Bullish Engulfing",+1) in hits

def test_doji_golden():
    bars=[_b(100,102,98,100.05)]  # 极小实体
    hits=detect_patterns(bars)
    assert ("Doji",0) in hits
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_candlestick_patterns.py -v`
Expected: FAIL(ImportError: detect_patterns)

- [ ] **Step 3: 实现 utils/candlestick_patterns.py**

实现附录A全部 ~28 形态。骨架(固定阈值常量,逐形态判定,返回命中列表)。`detect_patterns(bars)` 接收末尾 N 根 dict 列表(o/h/l/c),逐识别器判定最后一根(及所需前序),返回 `[(name, direction)]`。

```python
"""手写标准蜡烛形态库(固定阈值,禁调)。observability-only 研究用。
每函数判定序列末端是否命中,detect_patterns 汇总。direction: +1看涨/-1看跌/0中性。"""
EPS = 1e-9
def _f(b): 
    o,h,l,c=b["open"],b["high"],b["low"],b["close"]
    rng=max(h-l,EPS); body=abs(c-o); up=h-max(o,c); lo=min(o,c)-l
    return o,h,l,c,rng,body,up,lo,(c>o)

def _single(b):
    o,h,l,c,rng,body,up,lo,bull=_f(b); out=[]
    if body<=0.1*rng: out.append(("Doji",0))
    if body<=0.3*rng and up>=body and lo>=body and body>0.1*rng: out.append(("Spinning Top",0))
    if bull and body>=0.9*rng: out.append(("Bullish Marubozu",+1))
    if (not bull) and body>=0.9*rng: out.append(("Bearish Marubozu",-1))
    if lo>=2*body and up<=0.3*body and body>0: out.append(("Hammer",+1))
    if up>=2*body and lo<=0.3*body and body>0: out.append(("Shooting Star",-1))
    if body<=0.1*rng and lo>=2*body and up<=0.1*rng: out.append(("Dragonfly Doji",+1))
    if body<=0.1*rng and up>=2*body and lo<=0.1*rng: out.append(("Gravestone Doji",-1))
    return out
# ... _double(prev,cur) / _triple(b1,b2,b3) / _five(...) 按附录A实现 Engulfing/Harami/
#     Piercing/DarkCloud/Tweezer/Kicker/Star/Inside/Outside/AbandonedBaby/Soldiers/Crows/ThreeMethods

def detect_patterns(bars):
    """bars: list[dict] 升序,判定末端形态。返回 [(name,direction)]。"""
    out=[]
    if len(bars)>=1: out+=_single(bars[-1])
    if len(bars)>=2: out+=_double(bars[-2],bars[-1])
    if len(bars)>=3: out+=_triple(bars[-3],bars[-2],bars[-1])
    if len(bars)>=5: out+=_five(bars[-5:])
    return out
```

(实现者按附录A把 `_double/_triple/_five` 全部补全,阈值取附录A常量。)

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_candlestick_patterns.py -v`
Expected: PASS(全部 golden + near-miss)

- [ ] **Step 5: Commit**

```bash
git add utils/candlestick_patterns.py tests/test_candlestick_patterns.py
git commit -m "feat(pattern-lab): ~28种标准蜡烛形态库(固定阈值)+golden/near-miss单测"
```

---

### Task 3: 红线守卫(先行,锁住 observability-only)

**Files:**
- Modify: `tests/test_cf_red_line_guard.py`

- [ ] **Step 1: 加守卫测试**

```python
def test_decision_paths_do_not_read_pattern_research():
    """daily-pattern-edge-lab: 决策/风控路径禁读形态研究模块/产物。"""
    import inspect
    forbidden = ("candlestick_patterns", "cf_pattern_edge_discovery")
    for modpath in ["agents.trading.judge","agents.trading.executor",
                    "agents.trading.portfolio_risk_guard","agents.trading.reviewer",
                    "agents.trading.position_analyst"]:
        mod = __import__(modpath, fromlist=["x"])
        src = inspect.getsource(mod)
        for f in forbidden:
            assert f not in src, f"{modpath} 不得引用研究模块 {f}"
```

- [ ] **Step 2: 运行(此时应 PASS,因尚未泄漏)**

Run: `python3 -m pytest tests/test_cf_red_line_guard.py::test_decision_paths_do_not_read_pattern_research -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cf_red_line_guard.py
git commit -m "test(pattern-lab): 加形态研究产物红线禁读守卫"
```

---

### Task 4: 回测驱动 cf_pattern_edge_discovery.py

**Files:**
- Create: `cf_pattern_edge_discovery.py`
- Reuse: `utils/counterfactual_pnl.py::resolve_counterfactual`, `utils/cf_honesty_gate.py::summarize_bucket`, `utils/candlestick_patterns.py::detect_patterns`

- [ ] **Step 1: load — 读 klines.db + ATR(14) + 上下文 6 桶**

```python
"""日线蜡烛形态 edge 发现(observability-only,严禁决策路径读取)。
load→fire→settle→aggregate→gate→report,镜像 cf_oi_divergence_ab.py。"""
import sqlite3, math
from collections import defaultdict
from utils.counterfactual_pnl import resolve_counterfactual
from utils.cf_honesty_gate import summarize_bucket
from utils.candlestick_patterns import detect_patterns
DB="data/klines.db"; ATR_N=14; RANGE_N=20; MA_N=50
SL_ATR=1.5; TP_ATR=3.0; MAX_HOLD_DAYS=10
def load(interval="1d"):
    conn=sqlite3.connect(DB)
    rows=conn.execute("SELECT symbol,open_time,open,high,low,close FROM klines WHERE interval=? ORDER BY symbol,open_time",(interval,)).fetchall()
    bysym=defaultdict(list)
    for s,t,o,h,l,c in rows: bysym[s].append({"open_time":t,"open":o,"high":h,"low":l,"close":c})
    return bysym
def atr(bars,i,n=ATR_N):
    if i<n: return None
    trs=[max(bars[j]["high"]-bars[j]["low"], abs(bars[j]["high"]-bars[j-1]["close"]), abs(bars[j]["low"]-bars[j-1]["close"])) for j in range(i-n+1,i+1)]
    return sum(trs)/n
def context(bars,i):
    if i<MA_N: return None
    win=bars[i-RANGE_N+1:i+1]; hi=max(b["high"] for b in win); lo=min(b["low"] for b in win)
    rp=(bars[i]["close"]-lo)/max(hi-lo,1e-9)
    ma=sum(b["close"] for b in bars[i-MA_N+1:i+1])/MA_N
    trend="up" if bars[i]["close"]>ma else "down"
    rp_b="low" if rp<0.25 else ("high" if rp>0.75 else "mid")
    return f"{rp_b}|{trend}"
```

- [ ] **Step 2: fire — 逐 bar 形态识别 + 簇去重**

```python
def fire(bysym):
    """返回 [(sym, i, pattern_name, direction, ctx)]。簇去重: 同sym+pattern+dir在5根内只取一次。"""
    sig=[]
    for sym,bars in bysym.items():
        last={}
        for i in range(MA_N, len(bars)-1):
            ctx=context(bars,i)
            if ctx is None: continue
            for name,d in detect_patterns(bars[max(0,i-4):i+1]):
                if d==0: continue
                key=(name,d)
                if key in last and i-last[key]<5: continue
                last[key]=i
                sig.append((sym,i,name,d,ctx))
    return sig
```

- [ ] **Step 3: settle — ATR 退出 + resolve_counterfactual**

```python
def settle(bars, i, direction, atr_val, size=100.0):
    entry=bars[i]["close"]
    side="long" if direction==1 else "short"
    sl = entry-SL_ATR*atr_val if side=="long" else entry+SL_ATR*atr_val
    tp = entry+TP_ATR*atr_val if side=="long" else entry-TP_ATR*atr_val
    fut=bars[i+1:i+1+MAX_HOLD_DAYS]
    if len(fut)<2: return None
    cf_bars=[{"open_time":b["open_time"],"high":b["high"],"low":b["low"],"close":b["close"]} for b in fut]
    rec={"symbol":"x","side":side,"entry_price":entry,"stop_loss":sl,"take_profit":[tp],
         "leverage":1,"size_usdt":size,"funding_rate":0.0,"created_at":bars[i]["open_time"]/1000.0}
    r=resolve_counterfactual(rec, cf_bars, max_hold_sec=MAX_HOLD_DAYS*86400)
    if r.net_usdt is None: return None
    sl_dist=abs(entry-sl)/entry
    risk=size*sl_dist
    return (r.net_usdt/risk) if risk>0 else None
```

- [ ] **Step 4: aggregate + gate + report(OOS三分/FDR/诚实门/加权)**

```python
def _seg(open_time_ms):
    y=__import__("datetime").datetime.utcfromtimestamp(open_time_ms/1000).year
    return "train" if y<=2024 else ("val" if y==2025 else "test")
def bh_fdr(pvals, q=0.10):
    """Benjamini-Hochberg: 返回每个 p 是否拒绝 H0 的布尔。"""
    idx=sorted(range(len(pvals)), key=lambda k:pvals[k]); m=len(pvals); rej=[False]*m; kmax=-1
    for rank,k in enumerate(idx,1):
        if pvals[k]<= rank/m*q: kmax=rank
    for rank,k in enumerate(idx,1):
        if rank<=kmax: rej[k]=True
    return rej
def main():
    bysym=load("1d")
    sig=fire(bysym)
    # 结算并按 (pattern,dir,ctx) × seg 累积净R
    buckets=defaultdict(lambda: defaultdict(list))  # key -> seg -> [R]
    for sym,i,name,d,ctx in sig:
        a=atr(bysym[sym],i)
        if not a: continue
        R=settle(bysym[sym],i,d,a)
        if R is None: continue
        buckets[(name,d,ctx)][_seg(bysym[sym][i]["open_time"])].append(R)
    # gate: 三段同号 + 诚实门(test段) + FDR
    rows=[]
    for key,segs in buckets.items():
        tr,va,te=segs.get("train",[]),segs.get("val",[]),segs.get("test",[])
        if min(len(tr),len(va),len(te))<5: continue
        m=lambda x:sum(x)/len(x)
        same_sign = (m(tr)>0)==(m(va)>0)==(m(te)>0)
        summ=summarize_bucket(wins=sum(1 for r in te if r>0),losses=sum(1 for r in te if r<=0),net_usdt_samples=te)
        # 单样本 t 近似 p(test段)
        import statistics
        sd=statistics.pstdev(te) or 1e-9; t=m(te)/(sd/math.sqrt(len(te))); 
        from math import erf,sqrt
        p=2*(1-0.5*(1+erf(abs(t)/sqrt(2))))
        rows.append({"key":key,"n_test":len(te),"mean_te":m(te),"same_sign":same_sign,
                     "honest":summ["verdict"],"ci":summ["net_pnl_ci"],"p":p})
    rej=bh_fdr([r["p"] for r in rows]) if rows else []
    for r,fdr_ok in zip(rows,rej): r["fdr_ok"]=fdr_ok
    # 加权: 三关全过才非零
    print(f"{'形态|方向|上下文':<40}{'n_test':>7}{'净R_test':>9}{'三段同号':>8}{'诚实门':>14}{'FDR':>5}{'权重':>8}")
    for r in sorted(rows,key=lambda x:-x["mean_te"]):
        passed = r["same_sign"] and r["honest"]!="INSUFFICIENT_SAMPLE" and r["ci"][0]>0 and r["fdr_ok"]
        w=max(0.0,r["mean_te"]) if passed else 0.0
        k=f"{r['key'][0]}|{r['key'][1]:+d}|{r['key'][2]}"
        print(f"{k:<40}{r['n_test']:>7}{r['mean_te']:>+8.3f}{str(r['same_sign']):>8}{r['honest']:>14}{str(r['fdr_ok']):>5}{w:>+8.3f}")
    passed_rows=[r for r in rows if r["same_sign"] and r["honest"]!="INSUFFICIENT_SAMPLE" and r["ci"][0]>0 and r["fdr_ok"]]
    print(f"\n过三关(三段同号+诚实门+FDR)的形态: {len(passed_rows)}")
    if not passed_rows: print("→ 无形态过关 → 日线尺度形态无可信 edge(干净证伪)。")
    else:
        print("→ 候选形态(待 4h 确认集解封验证):")
        for r in passed_rows: print(f"   {r['key']} 净R_test={r['mean_te']:+.3f}")
    print("\nobservability-only —— 仅量化,不据此自动改 config/上 live。")
if __name__=="__main__": main()
```

- [ ] **Step 5: 跑骨架**

Run: `python3 cf_pattern_edge_discovery.py`
Expected: 打印每(形态×上下文)桶三段统计 + 过关形态数 + 诚实结论(过关候选 或 干净证伪)

- [ ] **Step 6: Commit**

```bash
git add cf_pattern_edge_discovery.py
git commit -m "feat(pattern-lab): 形态edge发现驱动(ATR退出+OOS三分+FDR+诚实门+加权)"
```

---

### Task 5: 全量回归 + 诚实汇报

- [ ] **Step 1: 形态库 + 红线守卫单测**

Run: `python3 -m pytest tests/test_candlestick_patterns.py tests/test_cf_red_line_guard.py -q`
Expected: PASS

- [ ] **Step 2: 全量 pytest 防回归**

Run: `python3 -m pytest -q`
Expected: 1359 passed(+ 新增形态单测),无新 fail(8 known event-loop fail 不计)

- [ ] **Step 3: 勾选 tasks.md + 最终诚实汇报**

更新 `openspec/changes/daily-pattern-edge-lab/tasks.md` 全勾;汇报:有无形态过三关;若有→进 4h 确认;若无→干净证伪结论;结论写入项目记忆。

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/daily-pattern-edge-lab/tasks.md
git commit -m "chore(pattern-lab): 完成tasks + 首版edge报告诚实汇报"
```

---

## Self-Review

- **Spec coverage**:历史抓取(Task1)/形态库(Task2)/红线(Task3)/回测三分FDR诚实门加权(Task4)/回归汇报(Task5) — delta spec 6 条 Requirement 全覆盖。
- **Placeholder**:`_double/_triple/_five` 标注"按附录A补全"——实现者照锁定阈值表填,非模糊占位(阈值在 design 附录A明确)。
- **Type 一致**:`detect_patterns(bars)→[(name,dir)]`、`settle()→净R float|None`、`resolve_counterfactual` 入参契约(side/entry_price/stop_loss/take_profit/leverage/size_usdt/funding_rate/created_at)与 utils/counterfactual_pnl.py 一致。
