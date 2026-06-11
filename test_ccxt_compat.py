import ccxt
import utils.ccxt_compat  # 安装 shim（import 即生效）


def test_keysort_tolerates_none_key():
    ex = ccxt.okx()
    out = ex.keysort({None: 1, "b": 2, "a": 3})  # 不应抛 TypeError
    assert list(out.keys()) == [None, "a", "b"]   # None 排首，其余按 str 升序


def test_keysort_all_str_unchanged():
    ex = ccxt.okx()
    assert list(ex.keysort({"b": 1, "a": 2, "c": 3}).keys()) == ["a", "b", "c"]


def test_install_is_idempotent():
    import importlib
    importlib.reload(utils.ccxt_compat)  # 二次安装不报错、不叠加
    ex = ccxt.okx()
    assert ex.keysort({None: 1}) == {None: 1}


def test_markets_by_id_with_none_id_does_not_crash():
    # 复现根因：markets_by_id 含 None 键时排序不崩
    ex = ccxt.okx()
    d = {None: {"id": None}, "BTC-USDT-SWAP": {"id": "BTC-USDT-SWAP"}}
    assert ex.keysort(d) is not None
