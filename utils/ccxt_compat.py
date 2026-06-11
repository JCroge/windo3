"""ccxt 兼容性 shim：使 keysort 容忍 None 键。

OKX 偶尔返回 id=None 的畸形市场，ccxt keysort 用 dict(sorted(items())) 排序
markets_by_id 时 None<str 抛 TypeError，导致 load_markets 崩溃。
本 shim 让 None 键确定性排在最前，不再抛错。import 本模块即安装（幂等）。
不升级 ccxt，规避「ccxt 升级须 testnet 重验收」红线。
"""
import ccxt  # ccxt.Exchange 即 ccxt.base.exchange.Exchange，所有交易所子类继承之

_PATCH_FLAG = "_keysort_none_safe"


def _safe_keysort(self, dictionary):
    return dict(sorted(dictionary.items(), key=lambda kv: (kv[0] is not None, str(kv[0]))))


def install():
    if getattr(ccxt.Exchange, _PATCH_FLAG, False):
        return
    ccxt.Exchange.keysort = _safe_keysort
    setattr(ccxt.Exchange, _PATCH_FLAG, True)


install()
