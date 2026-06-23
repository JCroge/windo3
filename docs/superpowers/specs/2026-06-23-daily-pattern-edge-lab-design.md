---
comet_change: daily-pattern-edge-lab
role: technical-design
canonical_spec: openspec
---

# Design Doc: daily-pattern-edge-lab

技术 RFC。需求事实源是 OpenSpec delta spec(`openspec/changes/daily-pattern-edge-lab/specs/pattern-edge-discovery/spec.md`),本文只定 HOW。

## Context

四轮诊断坐实现策略方向决策无 edge(根结点:赌动量但市场无动量,收益自相关≈0)。价格类 alpha 全证伪,套利/做市/carry 团队排除。形态思路在日内已证无 cost-surviving edge,唯一可能战场=日线/波段(成本可忽略 + 多体制)。复用既有 CF 基础设施(`resolve_counterfactual` / `cf_honesty_gate` / `data/klines.db` / repo 根 cf_*.py 模式)。第一原则:**防过拟合**——预登记 + 固定阈值 + OOS 三分 + 多重比较校正。

## Goals / Non-Goals

**Goals**:给「日线蜡烛形态有无可交易 edge」一个带 OOS + 多重比较校正的确定答案;observability-only;零新依赖。
**Non-Goals**:不接入 live;不自动改 config/上线权重;第一轮不优化退出参数、不做日内、4h 不进搜索维度。

## Decisions

### D1. 上下文桶(预登记锁死)
- `range_pos`:trailing **20 日** 高低窗口,`(close−low20)/(high20−low20)`;3 档 **低<0.25 / 中0.25–0.75 / 高>0.75**。
- `趋势`:`close vs MA50`(日线);上=升势 / 下=跌势。
- `前置移动`:前 **5 日收益**,仅作**报告协变量**,第一轮不做独立搜索维度(控多重比较)。
- 主上下文 = range_pos(3) × 趋势(2) = **6 桶**。
- *备选*:200 日 MA(对 2.75 年/新币太慢,否决);20 日 MA(太噪,否决)。

### D2. 形态库(`utils/candlestick_patterns.py`,手写,固定阈值)
原子:`body=|c−o|`、`rng=h−l`(防 0 除 → rng=max(h−l,ε))、`up=h−max(o,c)`、`lo=min(o,c)−l`、`bull=c>o`。
每识别器返回 `(name, direction∈{+1,−1,0})`,direction 为**预登记**方向假设。阈值见附录 A,全部固定常量,**无按结果调参入口**。
- *备选*:TA-Lib(未装 + C 编译痛点 → 否决,手写零依赖且定义可控)。

### D3. 退出策略(ATR-based,主测固定)
`ATR(14)` 日线。`SL=entry∓1.5×ATR`、`TP=entry±3.0×ATR`(~2:1)、最长持仓 **10 日**时间止损(到期按收盘 mark-out)。方向=形态预登记方向。结算经 `resolve_counterfactual`:SL/TP 路径**优先 4h bar 解析**(更细),无 4h 则日线 bar + 同根冲突 **SL-first 保守**。净 R = `net_usdt /(size×lev×sl_dist)`。退出参数扫描列**次要稳健性**,不进主测。

### D4. OOS 三分 + 多重比较校正
- 时间切分:**train 2023-24 / val 2025 / test 2026**,分段统计每(形态×上下文)桶。
- **多重比较:FDR(Benjamini-Hochberg, q=0.10)为主,Bonferroni 并列报告**。理由:发现/筛选场景,Bonferroni 控 FWER 在 ~56 检验下过严会杀真实薄 edge;真正严格防线是 **OOS 三段同号 + 4h 确认**(多道独立门),FDR 只是筛选段内二级守卫。
- 诚实门:复用 `cf_honesty_gate.summarize_bucket`(Wilson + bootstrap CI,n≥30)。

### D5. 加权(验证输出,非直觉)
`weight = max(0, OOS_test 净R/笔)`,**当且仅当**同时满足:诚实门(n≥30 且净 PnL CI 不跨 0) **AND** FDR 校正后显著 **AND** train/val/test 三段同号。否则 `weight=0`。最终方向信号 = `Σ(weight × direction)` 归一化。预期绝大多数权重=0。

### D6. 4h 确认解封口径
日线候选过关后,在 4h(interval='4h' 已落库,锁定状态)查**同形态同上下文**:判据 = **净 R 同号 且 ≥0**(4h 成本拖累更高,只要求同向、不要求同等显著)。**4h 翻号 → 日线结论标记可疑(疑时间框架特异侥幸)**。确认,非二次搜索。

### D7. 驱动结构(镜像 cf_oi_divergence_ab.py)
`cf_pattern_edge_discovery.py`:`load(klines.db + ATR + 上下文)` → `fire(逐 bar 形态识别 + 簇去重)` → `settle(ATR 退出 + resolve_counterfactual)` → `aggregate(6 桶 × 三段)` → `gate(FDR + 诚实门 + 三段同号 → 权重)` → `report`。

## Risks / Trade-offs

- [数据有功效≠有 edge] → 诚实先验偏怀疑;骨架买确定答案非盈利保证;两种结局都有价值。
- [新币短史 TRUMP/TON/HYPE] → 按可得历史加权,短史标注不凑数。
- [日线 SL/TP 同根歧义] → 优先 4h 解析,否则 SL-first 保守。
- [形态多→多重比较膨胀] → FDR + 预登记锁死形态集,严禁测试中追加/调阈值(防 p-hacking)。
- [红线泄漏] → `test_decision_paths_do_not_read_pattern_research` 守卫。

## Migration Plan

纯新增 + 改造孤立玩具脚本(`fetch_historical_klines.py` 无下游)。回滚=删新文件 + 还原玩具脚本。不碰 live/config/状态文件。

## Open Questions(已在本设计关闭)

D1–D6 已将 proposal/design.md 列的 Open Questions 定到可实施粒度。剩余仅"4h 解封后是否扩 universe"留候选出现后再议。

---

## 附录 A:预登记形态阈值表(锁死,实现照此)

| 形态 | 结构 | 判定(固定阈值) | 方向 |
|---|---|---|---|
| Doji 十字 | 单K | `body≤0.1·rng` | 0 |
| Spinning Top 陀螺 | 单K | `body≤0.3·rng 且 up≥body 且 lo≥body` | 0 |
| Bullish Marubozu 光头光脚阳 | 单K | `bull 且 body≥0.9·rng` | +1 |
| Bearish Marubozu | 单K | `!bull 且 body≥0.9·rng` | −1 |
| Hammer 锤子 | 单K | `lo≥2·body 且 up≤0.3·body 且 body>0` | +1 |
| Inverted Hammer 倒锤 | 单K | `up≥2·body 且 lo≤0.3·body 且 body>0`(跌势上下文) | +1 |
| Hanging Man 上吊 | 单K | 同 Hammer 形(升势/高 range_pos 上下文) | −1 |
| Shooting Star 流星 | 单K | 同倒锤形(升势/高 range_pos 上下文) | −1 |
| Dragonfly Doji 蜻蜓 | 单K | `body≤0.1·rng 且 lo≥2·rng_body 且 up≈0` | +1 |
| Gravestone Doji 墓碑 | 单K | `body≤0.1·rng 且 up≥2·rng_body 且 lo≈0` | −1 |
| Bullish Engulfing 看涨吞没 | 双K | `昨!bull 且 今bull 且 今o≤昨c 且 今c≥昨o 且 今body>昨body` | +1 |
| Bearish Engulfing | 双K | 镜像 | −1 |
| Bullish Harami 看涨孕线 | 双K | `昨!bull 大实体 且 今bull 且 今body⊂昨body 且 昨body≥1.5·今body` | +1 |
| Bearish Harami(含双阳抱阴变体) | 双K | 镜像 | −1 |
| Piercing Line 刺透 | 双K | `昨!bull 且 今bull 且 今o<昨l 且 今c>昨实体中点` | +1 |
| Dark Cloud Cover 乌云盖顶 | 双K | 镜像 | −1 |
| Tweezer Bottom 镊子底 | 双K | `两根 low 近似相等(±0.1%) 且 处低位` | +1 |
| Tweezer Top 镊子顶 | 双K | `两根 high 近似相等 且 处高位` | −1 |
| Bullish Kicker 看涨跳空反扑 | 双K | `昨!bull 且 今bull 且 今o>昨o(向上跳空)` | +1 |
| Bearish Kicker | 双K | 镜像 | −1 |
| Morning Star 启明星 | 三K | `1大阴 + 2小实体(可跳空) + 3大阳收回1实体过半` | +1 |
| Evening Star 黄昏星 | 三K | 镜像 | −1 |
| Three Inside Up 三内升 | 三K | `看涨孕线 + 第3根收高于第1根实体顶` | +1 |
| Three Inside Down | 三K | 镜像 | −1 |
| Three Outside Up 三外升 | 三K | `看涨吞没 + 第3根继续收高` | +1 |
| Three Outside Down | 三K | 镜像 | −1 |
| Bullish Abandoned Baby 看涨弃婴 | 三K | `1阴 + 2十字向下跳空孤立 + 3阳向上跳空` | +1 |
| Bearish Abandoned Baby | 三K | 镜像 | −1 |
| Three White Soldiers 三白兵 | 三K | `连续3阳,逐根收高,各 body≥0.6·rng,小上影` | +1 |
| Three Black Crows 三黑鸦 | 三K | 镜像 | −1 |
| Rising Three Methods 上升三法 | 五K | `1大阳 + 3小阴(未破1根 low) + 5大阳收新高` | +1(延续) |
| Falling Three Methods 下降三法 | 五K | 镜像 | −1(延续) |

注:同形不同位置(Hammer/Hanging Man、Inverted Hammer/Shooting Star)由上下文桶(趋势 + range_pos)区分,识别器返回形态名 + 形态固有方向,上下文在回测分桶时叠加。
