"""统一 exchange factory — 所有 ccxt exchange 创建走此入口

确保 testnet/live 边界一致：use_testnet=True 时所有 client 都设置 sandbox。
"""

import os
import ccxt
from typing import Optional


def create_exchange(config: dict, exchange_id: str = None,
                    require_private: bool = False,
                    purpose: str = "") -> ccxt.Exchange:
    """创建 ccxt exchange 实例

    Args:
        config: 系统配置 dict（含 exchange, use_testnet 等）
        exchange_id: 交易所 ID（okx/binance），默认从 config 读取
        require_private: 是否需要私有 API（需要凭证）
        purpose: 用途说明（用于日志）

    Returns:
        配置好的 ccxt exchange 实例
    """
    exchange_id = exchange_id or config.get('exchange', 'okx')
    use_testnet = config.get('use_testnet', False)

    ex_config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    }

    if exchange_id == 'okx':
        api_key = os.getenv('OKX_API_KEY', '')
        secret = os.getenv('OKX_SECRET', '')
        password = os.getenv('OKX_PASSWORD', os.getenv('OKX_PASSPHRASE', ''))
        if api_key:
            ex_config['apiKey'] = api_key
            ex_config['secret'] = secret
            ex_config['password'] = password
        exchange = ccxt.okx(ex_config)
    elif exchange_id == 'binance':
        api_key = os.getenv('BINANCE_API_KEY', '')
        secret = os.getenv('BINANCE_SECRET', '')
        if api_key:
            ex_config['apiKey'] = api_key
            ex_config['secret'] = secret
        exchange = ccxt.binance(ex_config)
    else:
        raise ValueError(f"Unsupported exchange: {exchange_id}")

    if use_testnet:
        exchange.set_sandbox_mode(True)

    if require_private and not ex_config.get('apiKey'):
        raise RuntimeError(
            f"Exchange factory: {exchange_id} requires private API credentials "
            f"for purpose='{purpose}' but none provided"
        )

    return exchange
