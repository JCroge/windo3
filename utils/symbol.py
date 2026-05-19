"""Symbol 格式统一工具 — 系统内部唯一规范源

系统内 symbol 形态：
- internal  — `BASE-USDT`（系统内部规范，所有 agent state dict 的 key）
- ccxt      — `BASE/USDT:USDT`（ccxt unified format，用于 fetch_ticker/fetch_ohlcv 等）
- okx_inst  — `BASE-USDT-SWAP`（OKX REST API 的 instId）
- okx_uly   — `BASE-USDT`（OKX underlying，恰好与 internal 相同）

所有跨 agent 消息（msg['symbol'], payload['symbol']）应当用 internal 形式。
CCXT/OKX API 调用现场转换，**不要存进 state**。
"""


def base_of(symbol: str) -> str:
    """提取标的 base（去掉所有 USDT 后缀和分隔符）

    Examples:
        'SOL-USDT'        → 'SOL'
        'SOL/USDT:USDT'   → 'SOL'
        'SOL-USDT-SWAP'   → 'SOL'
        'SOLUSDT'         → 'SOL' (best effort, 但不推荐输入此格式)
    """
    if not symbol:
        return ''
    s = symbol.upper()
    # 去掉 ccxt 形式
    if '/' in s:
        s = s.split('/')[0]
        return s
    # 去掉 -USDT-SWAP / -USDT 后缀
    if s.endswith('-USDT-SWAP'):
        return s[:-10]
    if s.endswith('-USDT'):
        return s[:-5]
    # 处理无分隔符的 BASEUSDT（兼容旧数据）
    if s.endswith('USDT'):
        return s[:-4]
    return s


def to_internal(symbol: str) -> str:
    """转为系统内部规范格式 `BASE-USDT`

    所有 agent state dict 的 key 都应该用这个函数处理。
    """
    base = base_of(symbol)
    return f"{base}-USDT" if base else symbol


def to_ccxt(symbol: str) -> str:
    """转为 ccxt unified format `BASE/USDT:USDT`

    用于 exchange.fetch_ticker / fetch_ohlcv / fetch_funding_rate 等调用。
    """
    base = base_of(symbol)
    return f"{base}/USDT:USDT" if base else symbol


def to_okx_inst(symbol: str) -> str:
    """转为 OKX REST API instId 格式 `BASE-USDT-SWAP`"""
    base = base_of(symbol)
    return f"{base}-USDT-SWAP" if base else symbol


def to_okx_uly(symbol: str) -> str:
    """转为 OKX underlying 格式 `BASE-USDT`（与 internal 相同）"""
    return to_internal(symbol)
