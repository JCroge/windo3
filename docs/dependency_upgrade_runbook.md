# 依赖升级验收流程

## 概述

本项目使用 `requirements.lock` 锁定精确版本。任何依赖升级必须经过验收流程。

## 升级步骤

### 1. 修改版本

编辑 `requirements.lock` 中目标包的版本号。

### 2. 安装并验证

```bash
pip install -r requirements.lock
python3 -m pytest -q
```

### 3. ccxt 升级专项验收

ccxt 是交易所通信核心库，升级后必须额外验证：

#### 3.1 语义验证清单

| Case | 验证内容 | 命令/方法 |
|------|----------|-----------|
| 1 | OKX SWAP market 结构含 contractSize | `exchange.load_markets(); m['BTC/USDT:USDT']['contractSize']` |
| 2 | set_leverage 正常 | `exchange.set_leverage(3, 'BTC/USDT:USDT')` |
| 3 | create_order 参数兼容 | testnet 下单验证 |
| 4 | fetch_order_book 返回格式 | `ob['asks'][0]` 为 `[price, qty]` |
| 5 | fetch_positions 返回格式 | 含 `contracts`, `side`, `unrealizedPnl` |
| 6 | algo order (SL/TP) 参数 | attachAlgoOrds 或 stopLoss/takeProfit 参数 |
| 7 | fetch_funding_rate_history | 返回含 `fundingRate`, `timestamp` |
| 8 | amount_to_precision | 精度格式化正确 |

#### 3.2 OKX Testnet 端到端

```bash
USE_TESTNET=true python3 -c "
from utils.exchange_factory import create_exchange
from utils.config_loader import load_config
cfg = load_config(strict_live_check=False)
ex = create_exchange(cfg, require_private=True, purpose='ccxt_upgrade_test')
ex.load_markets()
m = ex.markets.get('BTC/USDT:USDT', {})
print(f'contractSize: {m.get(\"contractSize\")}')
print(f'precision: {m.get(\"precision\")}')
ob = ex.fetch_order_book('BTC/USDT:USDT', limit=5)
print(f'asks[0]: {ob[\"asks\"][0]}')
print('PASS: ccxt upgrade semantic check')
"
```

#### 3.3 回归测试

```bash
python3 -m pytest -q test_okx_contract_size.py test_okx_support.py
```

### 4. 其他关键包升级注意

| 包 | 风险点 |
|----|--------|
| openai | chat completions API 兼容性 |
| aiohttp | connector/timeout 参数变更 |
| pandas | DataFrame API 废弃警告 |

### 5. 提交

升级验证通过后，同时更新 `requirements.lock` 和 `requirements.txt`（保持 lock 为精确版本，txt 为最低版本）。

## 禁止事项

- 不得在未执行 OKX testnet 验证的情况下升级 ccxt
- 不得跳过全量 pytest 回归
- 不得在 live 环境直接升级（先 testnet 验证）
