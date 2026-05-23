# OKX 执行语义 Testnet 验收报告

日期：2026-05-22  
状态：mock 通过，testnet 待执行  
关联待解决事项：`docs/待解决事项.md`  
关联脚本：`verify_okx_testnet_semantics.py`

## 1. 验收目的

在扩大 live 灰度前，验证 OKX testnet 下单语义与系统预期一致。确保：
- 条件单（TP/SL）行为可预测
- 拒单有明确终态
- 平仓后无残留条件单

## 2. Mock Exchange 验收结果

执行日期：2026-05-22  
执行人：自动化脚本 (`verify_okx_testnet_semantics.py`)  
环境：mock exchange  
详细报告：`docs/generated_reports/OKX执行语义mock验收报告_20260522.md`

| Case | 描述 | 状态 |
|------|------|------|
| 1 | Market Open + Attached TP/SL | 通过 |
| 2 | Limit Open Timeout | 通过 |
| 3 | Insufficient Balance | 通过 |
| 4 | Min Amount | 通过 |
| 5 | ReduceOnly Close | 通过 |
| 6 | Move SL | 通过 |
| 7 | Close 后条件单状态 | 通过 |
| 8 | Duplicate clOrdId / Idempotency | 通过 |

## 3. Testnet 验收记录

```
执行日期：待执行
执行人：
环境：OKX testnet
OKX API version：

Case 1-8: 待执行
```

## 4. 结论

- Mock exchange 8 case 全部通过
- OKX testnet 未执行
- 是否允许小额 live 灰度：否（需 testnet 验证）
- 是否允许 paper/mock 继续：是
- 残余风险：mock 无法验证网络延迟、真实撮合、条件单触发时序
- 后续动作：连接 OKX testnet 执行真实验证后方可进入 live 灰度评审
