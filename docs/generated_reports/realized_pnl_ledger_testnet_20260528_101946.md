# 真实已实现 PnL 账本 — OKX testnet 验收报告

生成时间：2026-05-28T10:21:20.442880+00:00
账户：OKX demo trading（.env.testnet 子账户）
标的：BTC-USDT-SWAP，size_usdt=10.0，leverage=3
参数：T2_SL_PCT=0.0008，T2_WAIT_TIMEOUT=300s

汇总：PASS=0 / FAIL=1 / SKIP=0 / total=1

## 案例摘要

| case | result | notes |
|---|---|---|
| T0 | FAIL | 至少一个 OKX REST 端点未通过 code=0 校验 |

## 详情（每个 case 的 ledger_diff / resolution）

### T0 — FAIL
- executed_at: 2026-05-28T10:20:29.238277+00:00
- notes: 至少一个 OKX REST 端点未通过 code=0 校验
- resolution:
  ```json
  {}
  ```

## Go/No-Go

**NO-GO**：阻断项 ['T0']（PRD §8 完成定义要求 T0/T1/T2/T5 全 PASS）