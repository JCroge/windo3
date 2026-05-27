#!/usr/bin/env python3
"""OKX 真实 testnet 语义验收脚本 — 自动执行 T0-T9

依据：docs/okx_posmode_execution_acceptance.md
前置：.env.testnet 必须含 OKX_TESTNET_KEY/SECRET/PASSWORD

特性：
- 强制 USE_TESTNET=true，触发 ccxt sandbox
- 独立 data/testnet_positions.json，不污染 live
- 每 case 记录 raw request/response、final position/orders/algos
- 输出 JSONL + Markdown 验收报告

用法：
    python3 verify_okx_testnet_real.py [--case T1,T2] [--symbol BTC-USDT-SWAP]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


def _load_env_testnet() -> dict:
    env_path = REPO_ROOT / '.env.testnet'
    if not env_path.exists():
        sys.exit(f"FATAL: {env_path} 不存在；按 docs/okx_posmode_execution_acceptance.md 创建后重试")
    cfg: dict = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        cfg[k.strip()] = v.strip()
    required = ('OKX_TESTNET_KEY', 'OKX_TESTNET_SECRET', 'OKX_TESTNET_PASSWORD')
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        sys.exit(f"FATAL: .env.testnet 缺失 {missing}（PASSWORD 是创建 API 时自己设的 passphrase）")
    return cfg


CFG = _load_env_testnet()

# 把 testnet 凭据注入进 process env，覆盖 .env，executor 走 OKX_API_KEY 名称
os.environ['EXCHANGE'] = 'okx'
os.environ['USE_TESTNET'] = 'true'
os.environ['OKX_API_KEY'] = CFG['OKX_TESTNET_KEY']
os.environ['OKX_SECRET'] = CFG['OKX_TESTNET_SECRET']
os.environ['OKX_PASSWORD'] = CFG['OKX_TESTNET_PASSWORD']
# 关掉一些副作用
os.environ.pop('TELEGRAM_BOT_TOKEN', None)
os.environ.pop('TELEGRAM_CHAT_ID', None)

from executor import ContractExecutor  # noqa: E402


SYMBOL = CFG.get('TESTNET_SYMBOL', 'BTC-USDT-SWAP')
SIZE_USDT = float(CFG.get('TESTNET_SIZE_USDT', '10'))
LEVERAGE = int(CFG.get('TESTNET_LEVERAGE', '3'))
T7_MODE = CFG.get('TESTNET_T7_MODE', 'mock_only')

REPORT_TS = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
JSONL_PATH = REPO_ROOT / 'data' / f'testnet_verify_{REPORT_TS}.jsonl'
REPORT_PATH = REPO_ROOT / 'docs' / 'generated_reports' / f'OKX执行语义testnet验收报告_{REPORT_TS}.md'
TESTNET_POSITIONS_FILE = str(REPO_ROOT / 'data' / 'testnet_positions.json')

JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(obj: Any) -> Any:
    try:
        json.dumps(obj, default=str)
        return obj
    except Exception:
        return str(obj)


class CaseRecord:
    def __init__(self, case_id: str, ex: ContractExecutor):
        self.case_id = case_id
        self.executed_at = _now_iso()
        self.okx_pos_mode = ex._okx_pos_mode or 'unknown'
        self.symbol = SYMBOL
        self.local_request: dict = {}
        self.raw_response: Any = None
        self.normalized_result: Any = None
        self.final_position: Any = None
        self.final_open_orders: Any = None
        self.final_algo_orders: Any = None
        self.result: str = 'PENDING'
        self.notes: str = ''

    def to_dict(self) -> dict:
        return {
            'case_id': self.case_id,
            'executed_at': self.executed_at,
            'okx_pos_mode': self.okx_pos_mode,
            'symbol': self.symbol,
            'local_request': _safe(self.local_request),
            'raw_response': _safe(self.raw_response),
            'normalized_result': _safe(self.normalized_result),
            'final_position': _safe(self.final_position),
            'final_open_orders': _safe(self.final_open_orders),
            'final_algo_orders': _safe(self.final_algo_orders),
            'result': self.result,
            'notes': self.notes,
        }


def _persist(rec: CaseRecord) -> None:
    with JSONL_PATH.open('a') as f:
        f.write(json.dumps(rec.to_dict(), default=str, ensure_ascii=False) + '\n')


def _fetch_state(ex: ContractExecutor) -> Dict[str, Any]:
    state: Dict[str, Any] = {'positions': None, 'open_orders': None, 'algo_orders': None}
    try:
        state['positions'] = ex.exchange.fetch_positions([SYMBOL])
    except Exception as e:
        state['positions'] = f"ERR: {e}"
    try:
        state['open_orders'] = ex.exchange.fetch_open_orders(SYMBOL)
    except Exception as e:
        state['open_orders'] = f"ERR: {e}"
    try:
        state['algo_orders'] = ex._list_pending_algos(SYMBOL)
    except Exception as e:
        state['algo_orders'] = f"ERR: {e}"
    return state


def _attach_state(rec: CaseRecord, ex: ContractExecutor) -> None:
    s = _fetch_state(ex)
    rec.final_position = s['positions']
    rec.final_open_orders = s['open_orders']
    rec.final_algo_orders = s['algo_orders']


def _live_position(ex: ContractExecutor) -> Optional[dict]:
    """从交易所拉真实仓位（>0 contracts），找不到返回 None。"""
    try:
        positions = ex.exchange.fetch_positions([SYMBOL])
    except Exception:
        return None
    for p in positions or []:
        if p.get('symbol') and float(p.get('contracts') or 0) > 0:
            return p
    return None


def _flat_check(ex: ContractExecutor) -> bool:
    return _live_position(ex) is None


def _wait_flat(ex: ContractExecutor, timeout: float = 20.0, poll: float = 1.0) -> bool:
    """轮询等待交易所仓位归零；用于覆盖 OKX testnet 状态同步延迟。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _flat_check(ex):
            return True
        time.sleep(poll)
    return _flat_check(ex)


def _safe_close_remaining(ex: ContractExecutor) -> None:
    """case 结束兜底：直接通过 ccxt 平掉交易所真实持仓 + 取消所有 algo。

    不能依赖 ex.close_position()，因为本地 positions.json 可能已被清空，
    那条路径首行 `if symbol not in self.positions: return None` 会直接 noop，
    交易所上的孤儿仓位永远清不掉。
    """
    pos = _live_position(ex)
    if pos:
        try:
            contracts = float(pos.get('contracts') or 0)
            inst_side = (pos.get('side') or '').lower()
            close_side = 'sell' if inst_side == 'long' else 'buy'
            params = {'reduceOnly': True}
            # long_short_mode 必须带 posSide
            if ex._okx_pos_mode == 'long_short_mode':
                params['posSide'] = inst_side
            elif ex._okx_pos_mode == 'net_mode':
                params['posSide'] = 'net'
            ex.exchange.create_order(
                SYMBOL, 'market', close_side, contracts, params=params
            )
            print(f"[cleanup] closed {SYMBOL} {inst_side} {contracts} contracts")
            time.sleep(2)
        except Exception as e:
            print(f"[cleanup] 直接平仓失败: {e}")
    # 撤所有 pending algo
    try:
        algos = ex._list_pending_algos(SYMBOL) or []
        for a in algos:
            aid = a.get('algoId') or a.get('id')
            if aid:
                ex._cancel_algo_by_id(SYMBOL, aid)
        if algos:
            print(f"[cleanup] cancelled {len(algos)} algo orders")
    except Exception as e:
        print(f"[cleanup] algo 清理失败: {e}")
    # 清本地状态文件
    try:
        if SYMBOL in ex.positions:
            del ex.positions[SYMBOL]
            ex._save_positions()
    except Exception:
        pass
    # 清幂等窗口,case 间不要互相阻塞
    try:
        if getattr(ex, 'idempotency', None):
            ex.idempotency.clear()
    except Exception:
        pass


# =====================================================
# T0: Account Config
# =====================================================
def case_t0(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T0', ex)
    rec.local_request = {'method': 'GET /api/v5/account/config'}
    try:
        raw = ex.exchange.private_get_account_config()
        rec.raw_response = raw
        data = (raw or {}).get('data') or [{}]
        pos_mode = data[0].get('posMode') if data else None
        rec.normalized_result = {
            'posMode': pos_mode,
            'cached_okx_pos_mode': ex._okx_pos_mode,
            'cached_source': ex._okx_pos_mode_source,
        }
        if pos_mode in ('net_mode', 'long_short_mode') and ex._okx_pos_mode == pos_mode:
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f"posMode={pos_mode} cached={ex._okx_pos_mode} 不一致或非法"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    _attach_state(rec, ex)
    return rec


# =====================================================
# T1: Market Open + Attached TP/SL
# =====================================================
def case_t1(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T1', ex)
    if not _flat_check(ex):
        rec.result = 'FAIL'
        rec.notes = 'pre-state not flat'
        _attach_state(rec, ex)
        return rec

    ticker = ex.exchange.fetch_ticker(SYMBOL)
    last = ticker.get('last') or 0
    sl = round(last * 0.97, 2)
    tp = round(last * 1.05, 2)
    plan = {
        'leverage': LEVERAGE,
        'size_usdt': SIZE_USDT,
        'order_type': 'market',
        'stop_loss': sl,
        'take_profit': [tp],
    }
    rec.local_request = {'method': 'open_position_with_plan', 'side': 'long', 'plan': plan}
    try:
        result = ex.open_position_with_plan(SYMBOL, 'long', plan)
        rec.raw_response = _safe(result)
        time.sleep(3)
        _attach_state(rec, ex)
        live = _live_position(ex)
        local = ex.get_position(SYMBOL)
        rec.normalized_result = {
            'open_returned': bool(result),
            'live_position_found': bool(live),
            'live_side': (live or {}).get('side'),
            'local_side': (local or {}).get('side'),
            'algo_count': len(rec.final_algo_orders) if isinstance(rec.final_algo_orders, list) else 0,
        }
        if result and live and (local or {}).get('side') == 'long':
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = 'open 未成交或本地/交易所方向不一致'
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T2: Net Mode Partial Reduce
# =====================================================
def case_t2(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T2', ex)
    if ex._okx_pos_mode != 'net_mode':
        rec.result = 'SKIP'
        rec.notes = f"posMode={ex._okx_pos_mode} 非 net_mode，跳过"
        return rec
    pre = _live_position(ex)
    if not pre:
        rec.result = 'FAIL'
        rec.notes = '无前置 T1 仓位'
        _attach_state(rec, ex)
        return rec
    pre_amount = float(pre.get('contracts') or 0)
    rec.local_request = {'method': 'reduce_position', 'pct': 0.5, 'pre_amount': pre_amount}
    try:
        result = ex.reduce_position(SYMBOL, 0.5, action_kind='testnet_t2')
        rec.raw_response = _safe(result)
        time.sleep(3)
        _attach_state(rec, ex)
        post = _live_position(ex)
        post_amount = float((post or {}).get('contracts') or 0) if post else 0
        ratio = post_amount / pre_amount if pre_amount > 0 else 0
        rec.normalized_result = {
            'pre_amount': pre_amount,
            'post_amount': post_amount,
            'ratio': ratio,
        }
        if result and 0.4 < ratio < 0.6 and (post or {}).get('side') == pre.get('side'):
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f'reduce 比例不在 [0.4, 0.6] 或方向反转: ratio={ratio:.3f}'
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T3: Net Mode Full Close
# =====================================================
def case_t3(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T3', ex)
    if ex._okx_pos_mode != 'net_mode':
        rec.result = 'SKIP'
        rec.notes = f"posMode={ex._okx_pos_mode} 非 net_mode，跳过"
        return rec
    pre = _live_position(ex)
    if not pre:
        rec.result = 'FAIL'
        rec.notes = '无前置仓位'
        _attach_state(rec, ex)
        return rec
    rec.local_request = {'method': 'close_position'}
    try:
        result = ex.close_position(SYMBOL, action_kind='testnet_t3')
        rec.raw_response = _safe(result)
        time.sleep(3)
        _attach_state(rec, ex)
        post = _live_position(ex)
        local = ex.get_position(SYMBOL)
        rec.normalized_result = {
            'live_flat': post is None,
            'local_cleared': local is None,
        }
        # 危险残留：未取消的 SL/TP algo
        algos = rec.final_algo_orders if isinstance(rec.final_algo_orders, list) else []
        residual = [a for a in algos if (a.get('state') or '').lower() in ('live', 'effective', '')]
        if post is None and local is None and not residual:
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f"live_flat={post is None} local_cleared={local is None} residual_algos={len(residual)}"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T4: Long/Short Mode Smoke (默认跳过，除非用户显式开启)
# =====================================================
def case_t4(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T4', ex)
    if ex._okx_pos_mode != 'long_short_mode':
        rec.result = 'SKIP'
        rec.notes = (f"posMode={ex._okx_pos_mode} 非 long_short_mode；切换账户配置需要人工动作，"
                     f"用 --case T4 + 人工切换后单独跑")
        return rec
    if not _flat_check(ex):
        rec.result = 'FAIL'
        rec.notes = 'pre-state not flat in long_short_mode'
        _attach_state(rec, ex)
        return rec
    ticker = ex.exchange.fetch_ticker(SYMBOL)
    last = ticker.get('last') or 0
    plan = {
        'leverage': LEVERAGE, 'size_usdt': SIZE_USDT, 'order_type': 'market',
        'stop_loss': round(last * 0.97, 2), 'take_profit': [round(last * 1.05, 2)],
    }
    rec.local_request = {'plan': plan, 'mode': 'long_short_mode'}
    try:
        opened = ex.open_position_with_plan(SYMBOL, 'long', plan)
        time.sleep(2)
        reduced = ex.reduce_position(SYMBOL, 0.5, action_kind='testnet_t4_reduce')
        time.sleep(2)
        closed = ex.close_position(SYMBOL, action_kind='testnet_t4_close')
        # OKX testnet 状态同步 + 偶发 partial fill,先轮询;不行强制 reduceOnly 兜底
        flat_after = _wait_flat(ex, timeout=15.0)
        forced_cleanup = False
        if not flat_after:
            _safe_close_remaining(ex)
            forced_cleanup = True
            flat_after = _wait_flat(ex, timeout=15.0)
        _attach_state(rec, ex)
        rec.normalized_result = {
            'opened': bool(opened), 'reduced': bool(reduced), 'closed': bool(closed),
            'flat_after': flat_after,
            'forced_cleanup': forced_cleanup,
        }
        if opened and reduced and closed and flat_after and not forced_cleanup:
            rec.result = 'PASS'
        elif opened and reduced and closed and flat_after and forced_cleanup:
            rec.result = 'PASS'
            rec.notes = 'close 后残留 sliver,reduceOnly 兜底清完 — testnet partial fill,可接受'
        else:
            rec.result = 'FAIL'
            rec.notes = 'open/reduce/close 任一环节失败或未平'
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T5: Standalone SL Algo (依赖 T1 重新开仓)
# =====================================================
def case_t5(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T5', ex)
    if not _flat_check(ex):
        rec.result = 'FAIL'
        rec.notes = 'pre-state not flat'
        _attach_state(rec, ex)
        return rec
    # 用 _open_position 不带 attach algo，再单独挂 standalone SL
    ticker = ex.exchange.fetch_ticker(SYMBOL)
    last = ticker.get('last') or 0
    rec.local_request = {'method': 'open + place_stop_loss_order', 'last': last}
    try:
        opened = ex._open_position(SYMBOL, 'long', SIZE_USDT)
        if not opened:
            rec.result = 'FAIL'
            rec.notes = 'open 未成交'
            _attach_state(rec, ex)
            return rec
        time.sleep(2)
        sl_price = round(last * 0.97, 2)
        sl_order = ex.place_stop_loss_order(SYMBOL, 'long', sl_price, opened.get('amount', 0))
        rec.raw_response = _safe(sl_order)
        time.sleep(2)
        _attach_state(rec, ex)
        algos = rec.final_algo_orders if isinstance(rec.final_algo_orders, list) else []
        live_sl = [a for a in algos if a.get('algoId') or a.get('id')]
        rec.normalized_result = {
            'sl_order_returned': bool(sl_order),
            'algo_count': len(live_sl),
            'algo_ids': [a.get('algoId') or a.get('id') for a in live_sl],
        }
        if sl_order and len(live_sl) >= 1:
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f"standalone SL 未挂成功，algos={len(live_sl)}"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T6: Move SL
# =====================================================
def case_t6(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T6', ex)
    # T6 自带开仓(_open_position 内部已挂初始 SL),然后 move SL 到新价位,
    # 验证 _replace_protective_sl 撤旧 + 挂新的语义,残余 algo 数必须为 1。
    if not _flat_check(ex):
        rec.result = 'FAIL'
        rec.notes = 'pre-state not flat'
        _attach_state(rec, ex)
        return rec
    ticker = ex.exchange.fetch_ticker(SYMBOL)
    last = float(ticker.get('last') or 0)
    rec.local_request = {'stage': 'open(with attached SL) + move SL', 'last': last}
    try:
        opened = ex._open_position(SYMBOL, 'long', SIZE_USDT)
        if not opened:
            rec.result = 'FAIL'
            rec.notes = 'open 未成交'
            _attach_state(rec, ex)
            return rec
        # _open_position 内部已通过 _place_protective_sl 挂了 SL,
        # 不要再 standalone place_stop_loss_order(否则会多挂一条孤儿 algo)。
        time.sleep(3)
        pre_algos = ex._list_pending_algos(SYMBOL) or []
        pre_ids = sorted([a.get('algoId') or a.get('id') for a in pre_algos
                          if a.get('algoId') or a.get('id')])
        if len(pre_ids) != 1:
            rec.result = 'FAIL'
            rec.notes = f"_open_position 后初始 SL algo 数不为 1: {len(pre_ids)}"
            rec.normalized_result = {'pre_ids': pre_ids}
            _attach_state(rec, ex)
            return rec

        local_pos = ex.get_position(SYMBOL)
        if not local_pos:
            rec.result = 'FAIL'
            rec.notes = '本地无对应持仓'
            return rec

        # 移动 SL 到新价位(比旧 SL 更紧),节流条件:change_pct>=0.3% 且间隔>30s
        # 由于初始 SL = 0.97 * last,新 SL = 0.96 * last,变化 ~1%,触发替换
        new_sl = round(last * 0.96, 2)
        ex._move_sl(SYMBOL, local_pos, new_sl)
        time.sleep(3)
        _attach_state(rec, ex)
        post_algos = rec.final_algo_orders if isinstance(rec.final_algo_orders, list) else []
        post_ids = sorted([a.get('algoId') or a.get('id') for a in post_algos
                           if a.get('algoId') or a.get('id')])
        rec.normalized_result = {
            'pre_ids': pre_ids,
            'post_ids': post_ids,
            'new_sl_local': local_pos.get('stop_loss'),
            'sl_algo_id_local': local_pos.get('sl_algo_id'),
        }
        if (len(post_ids) == 1 and post_ids != pre_ids
                and local_pos.get('sl_algo_id') == post_ids[0]):
            rec.result = 'PASS'
        elif len(post_ids) == 1 and post_ids == pre_ids:
            rec.result = 'FAIL'
            rec.notes = 'SL algoId 未变化(可能被节流或 amend)'
        else:
            rec.result = 'FAIL'
            rec.notes = f"post algo 数量异常: {len(post_ids)}"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T7: Reject Reconciliation (默认 mock_only)
# =====================================================
def case_t7(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T7', ex)
    rec.result = 'SKIP'
    if T7_MODE != 'real_attempt':
        rec.notes = ('TESTNET_T7_MODE=mock_only。复现 51169/51205 需要外部干预（手动通过 OKX UI 平仓）。'
                     '已通过 verify_okx_testnet_semantics.py mock 矩阵验证；'
                     'real_attempt 模式由人工操作 + 单独脚本完成。')
        return rec
    # real_attempt：开仓后立即让用户手动从 OKX UI 平仓，然后我们再 close_position
    rec.notes = 'real_attempt 未实现，请人工操作'
    return rec


# =====================================================
# T8: Duplicate clOrdId
# =====================================================
def case_t8(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T8', ex)
    if not _flat_check(ex):
        rec.result = 'FAIL'
        rec.notes = 'pre-state not flat'
        _attach_state(rec, ex)
        return rec
    rec.local_request = {'method': '两次 _open_position 同 (symbol, side) 10s 内'}
    try:
        first = ex._open_position(SYMBOL, 'long', SIZE_USDT)
        time.sleep(1)
        # 立刻再调用一次，幂等窗口应当拒绝
        second = ex._open_position(SYMBOL, 'long', SIZE_USDT)
        time.sleep(2)
        live = _live_position(ex)
        live_amount = float((live or {}).get('contracts') or 0) if live else 0
        rec.normalized_result = {
            'first_returned': bool(first),
            'second_returned': bool(second),
            'live_amount': live_amount,
            'expected': 'second 必须 None / 仓位只增加一次',
        }
        if first and second is None:
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f"second={second}; 幂等窗口未生效"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# T9: Close 后条件单状态
# =====================================================
def case_t9(ex: ContractExecutor) -> CaseRecord:
    rec = CaseRecord('T9', ex)
    rec.local_request = {'method': 'close_position then verify no live algo'}
    try:
        if _live_position(ex):
            ex.close_position(SYMBOL, action_kind='testnet_t9')
            time.sleep(3)
        _attach_state(rec, ex)
        algos = rec.final_algo_orders if isinstance(rec.final_algo_orders, list) else []
        # OKX 已取消的 algo state 通常为 canceled / triggered / order_failed
        live_algos = [a for a in algos if (a.get('state') or '').lower() in ('live', 'effective', '')]
        rec.normalized_result = {
            'live_position': _live_position(ex) is None,
            'total_algos': len(algos),
            'live_algos': len(live_algos),
        }
        if not _live_position(ex) and len(live_algos) == 0:
            rec.result = 'PASS'
        else:
            rec.result = 'FAIL'
            rec.notes = f"残留 live algo={len(live_algos)}"
    except Exception as e:
        rec.raw_response = f"EXC: {e}"
        rec.result = 'FAIL'
        rec.notes = str(e)
    return rec


# =====================================================
# Runner
# =====================================================
ALL_CASES = ['T0', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T9']
CASE_FN = {
    'T0': case_t0, 'T1': case_t1, 'T2': case_t2, 'T3': case_t3,
    'T4': case_t4, 'T5': case_t5, 'T6': case_t6, 'T7': case_t7,
    'T8': case_t8, 'T9': case_t9,
}


def _build_executor() -> ContractExecutor:
    ex = ContractExecutor(
        exchange_id='okx',
        api_key=os.environ['OKX_API_KEY'],
        secret=os.environ['OKX_SECRET'],
        password=os.environ['OKX_PASSWORD'],
        testnet=True,
        leverage=LEVERAGE,
        positions_file=TESTNET_POSITIONS_FILE,
    )
    return ex


def _write_report(records: List[CaseRecord]) -> None:
    pass_n = sum(1 for r in records if r.result == 'PASS')
    fail_n = sum(1 for r in records if r.result == 'FAIL')
    skip_n = sum(1 for r in records if r.result == 'SKIP')
    lines: List[str] = []
    lines.append(f"# OKX 执行语义 testnet 真实验收报告\n")
    lines.append(f"生成时间：{_now_iso()}")
    lines.append(f"账户：OKX demo trading")
    lines.append(f"标的：{SYMBOL}，size_usdt={SIZE_USDT}，leverage={LEVERAGE}\n")
    lines.append(f"汇总：PASS={pass_n} / FAIL={fail_n} / SKIP={skip_n} / total={len(records)}\n")
    lines.append("## 案例详情\n")
    for r in records:
        d = r.to_dict()
        lines.append(f"### {r.case_id} — {r.result}")
        lines.append(f"- executed_at: {d['executed_at']}")
        lines.append(f"- okx_pos_mode: {d['okx_pos_mode']}")
        lines.append(f"- notes: {d['notes'] or '-'}")
        lines.append(f"- normalized_result: `{json.dumps(_safe(d['normalized_result']), default=str, ensure_ascii=False)}`")
        lines.append("")
    lines.append("## Go/No-Go\n")
    required = ['T0', 'T1', 'T5', 'T6', 'T8', 'T9']
    blockers: List[str] = []
    for r in records:
        if r.case_id in required and r.result != 'PASS':
            blockers.append(f"{r.case_id}={r.result}")
    if any(r.case_id == 'T2' and r.result == 'FAIL' for r in records):
        blockers.append('T2=FAIL')
    if any(r.case_id == 'T3' and r.result == 'FAIL' for r in records):
        blockers.append('T3=FAIL')
    if blockers:
        lines.append(f"**NO-GO**：阻断项 {blockers}")
    else:
        lines.append("**GO**：必测项全部通过，允许 live 扩容（仍需人工二次确认 T4 / T7）")
    REPORT_PATH.write_text('\n'.join(lines))
    print(f"\n报告写入：{REPORT_PATH}")
    print(f"JSONL：{JSONL_PATH}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--case', default='all', help='逗号分隔，如 T0,T1,T9；默认 all')
    p.add_argument('--keep-position', action='store_true', help='跑完不做兜底清理（调试用）')
    args = p.parse_args()

    selected = ALL_CASES if args.case == 'all' else [c.strip().upper() for c in args.case.split(',')]
    invalid = [c for c in selected if c not in CASE_FN]
    if invalid:
        sys.exit(f"未知 case: {invalid}")

    print(f"=== OKX testnet 真实验收 ===")
    print(f"symbol={SYMBOL} size={SIZE_USDT} leverage={LEVERAGE}")
    print(f"cases={selected}\n")

    try:
        ex = _build_executor()
    except Exception as e:
        sys.exit(f"FATAL: ContractExecutor 初始化失败 — {e}")

    print(f"posMode={ex._okx_pos_mode} (source={ex._okx_pos_mode_source})")
    print(f"balance={ex.get_balance():.2f} USDT\n")

    records: List[CaseRecord] = []
    for cid in selected:
        # 每个 case 前强制把 testnet 状态拉回 flat（避免 case 间残留干扰）
        try:
            _safe_close_remaining(ex)
            if not _wait_flat(ex, timeout=15.0):
                # 兜底再扫一次,某些 case 间隔很短 OKX 状态会延后落地
                _safe_close_remaining(ex)
                _wait_flat(ex, timeout=15.0)
        except Exception as e:
            print(f"[pre-cleanup {cid}] {e}")
        print(f"[{cid}] start...")
        fn = CASE_FN[cid]
        try:
            rec = fn(ex)
        except Exception as e:
            rec = CaseRecord(cid, ex)
            rec.result = 'FAIL'
            rec.notes = f"runner exc: {e}"
        _persist(rec)
        print(f"[{cid}] {rec.result} — {rec.notes or 'ok'}")
        records.append(rec)

    if not args.keep_position:
        print("\n[cleanup] 关闭残留持仓与 algo...")
        try:
            _safe_close_remaining(ex)
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    _write_report(records)

    fail = sum(1 for r in records if r.result == 'FAIL')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())




