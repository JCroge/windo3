"""持仓四方对账 — 交易所 vs Executor vs RiskGuard vs PaperExecutor

对账结果决定系统是否允许恢复新开仓。
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple


class PositionReconciler:
    """四方持仓对账"""

    def __init__(self, executor=None, exchange=None, logger=None):
        self.executor = executor
        self.exchange = exchange
        self.logger = logger
        self._last_result: Optional[dict] = None
        self._result_file = "data/reconcile_result.json"

    def reconcile(self, riskguard_positions: dict = None,
                  paper_positions: dict = None) -> dict:
        """执行四方对账

        Returns:
            {
                "status": "matched" | "mismatch" | "error",
                "exchange_query_ok": bool,
                "timestamp": float,
                "exchange_symbols": [...],
                "executor_symbols": [...],
                "riskguard_symbols": [...],
                "paper_symbols": [...],
                "issues": [...]
            }
        """
        issues = []

        # 1. 交易所真实持仓（fail-closed：查询失败直接返回 error）
        exchange_positions, exchange_ok = self._fetch_exchange_positions()
        if not exchange_ok:
            result = {
                "status": "error",
                "exchange_query_ok": False,
                "timestamp": time.time(),
                "exchange_symbols": [],
                "executor_symbols": [],
                "riskguard_symbols": [],
                "paper_symbols": [],
                "issues": [{"type": "exchange_query_failed",
                            "symbol": "*",
                            "detail": "交易所持仓查询失败，对账无法完成"}],
            }
            self._last_result = result
            self._save_result(result)
            if self.logger:
                self.logger.error("[对账] 交易所查询失败，返回 error 状态（fail-closed）")
            return result

        exchange_symbols_raw = set(exchange_positions.keys())

        # 2. Executor 本地持仓
        executor_symbols_raw = set()
        if self.executor:
            executor_positions = self.executor.get_all_positions()
            executor_symbols_raw = set(executor_positions.keys())

        # 3. RiskGuard 追踪持仓
        rg_symbols_raw = set()
        if riskguard_positions:
            rg_symbols_raw = set(riskguard_positions.keys())

        # 4. PaperExecutor 持仓（仅做参考对比）
        paper_symbols = set()
        if paper_positions:
            paper_symbols = set(paper_positions.keys())

        # 统一 symbol 格式后再比较
        def _norm_set(raw_set):
            return {self._normalize(s): s for s in raw_set}

        exchange_norm = _norm_set(exchange_symbols_raw)
        executor_norm = _norm_set(executor_symbols_raw)
        rg_norm = _norm_set(rg_symbols_raw)

        exchange_symbols = set(exchange_norm.keys())
        executor_symbols = set(executor_norm.keys())
        rg_symbols = set(rg_norm.keys())

        # 对比：交易所 vs Executor（normalized）
        for norm_sym in exchange_symbols - executor_symbols:
            issues.append({
                "type": "missing_in_executor",
                "symbol": exchange_norm[norm_sym],
                "detail": "交易所有持仓但Executor无记录",
            })

        for norm_sym in executor_symbols - exchange_symbols:
            issues.append({
                "type": "missing_in_exchange",
                "symbol": executor_norm[norm_sym],
                "detail": "Executor有记录但交易所无持仓（可能已被平仓）",
            })

        # 对比：Executor vs RiskGuard（normalized）
        if riskguard_positions is not None:
            for norm_sym in executor_symbols - rg_symbols:
                issues.append({
                    "type": "missing_in_risk_guard",
                    "symbol": executor_norm[norm_sym],
                    "detail": "Executor有持仓但RiskGuard未追踪",
                })
            for norm_sym in rg_symbols - executor_symbols:
                issues.append({
                    "type": "missing_in_executor",
                    "symbol": rg_norm[norm_sym],
                    "detail": "RiskGuard追踪但Executor无记录",
                })

        # 对比：实盘 vs Paper（仅标记，不阻塞）
        if paper_positions is not None:
            live_sides = {}
            if self.executor:
                for sym, pos in self.executor.get_all_positions().items():
                    live_sides[self._normalize(sym)] = pos.get('side')

            for sym, pos in paper_positions.items():
                norm = self._normalize(sym)
                if norm in live_sides:
                    if pos.get('side') != live_sides[norm]:
                        issues.append({
                            "type": "paper_live_mismatch",
                            "symbol": sym,
                            "detail": f"Paper={pos.get('side')} vs Live={live_sides[norm]}",
                        })

        # 外部平仓检测
        for sym in exchange_symbols_raw:
            ep = exchange_positions[sym]
            if ep.get('contracts', 0) == 0:
                issues.append({
                    "type": "external_close_detected",
                    "symbol": sym,
                    "detail": "交易所持仓量为0（可能SL/TP已触发）",
                })

        # 区分 blocking vs advisory issues
        ADVISORY_TYPES = {'paper_live_mismatch'}
        blocking_issues = [i for i in issues if i['type'] not in ADVISORY_TYPES]
        advisory_issues = [i for i in issues if i['type'] in ADVISORY_TYPES]

        status = "matched" if not blocking_issues else "mismatch"
        result = {
            "status": status,
            "exchange_query_ok": True,
            "timestamp": time.time(),
            "exchange_symbols": sorted(exchange_symbols_raw),
            "executor_symbols": sorted(executor_symbols_raw),
            "riskguard_symbols": sorted(rg_symbols_raw),
            "paper_symbols": sorted(paper_symbols),
            "issues": issues,
            "blocking_issues": blocking_issues,
            "advisory_issues": advisory_issues,
        }

        self._last_result = result
        self._save_result(result)

        if self.logger:
            if issues:
                self.logger.warning(f"[对账] 发现 {len(issues)} 个问题: "
                                    f"{[i['type'] for i in issues]}")
            else:
                self.logger.info("[对账] 四方持仓一致 ✓")

        return result

    def get_last_result(self) -> Optional[dict]:
        return self._last_result

    def _fetch_exchange_positions(self) -> Tuple[dict, bool]:
        """从交易所获取真实持仓。返回 (positions, success)。"""
        if not self.exchange:
            if self.logger:
                self.logger.error("[对账] exchange 未初始化，无法查询持仓")
            return {}, False
        try:
            raw = self.exchange.fetch_positions()
            positions = {}
            for pos in raw:
                contracts = float(pos.get('contracts', 0) or 0)
                if contracts > 0:
                    symbol = pos.get('symbol', '')
                    positions[symbol] = {
                        'symbol': symbol,
                        'side': pos.get('side', ''),
                        'contracts': contracts,
                        'notional': float(pos.get('notional', 0) or 0),
                        'unrealizedPnl': float(pos.get('unrealizedPnl', 0) or 0),
                    }
            return positions, True
        except Exception as e:
            if self.logger:
                self.logger.error(f"[对账] 交易所持仓查询失败: {e}")
            return {}, False

    def _normalize(self, symbol: str) -> str:
        """统一 symbol 格式"""
        s = symbol.replace('/USDT:USDT', '').replace('-SWAP', '').replace('-USDT', '')
        return s.upper()

    def _save_result(self, result: dict):
        try:
            os.makedirs(os.path.dirname(self._result_file) or '.', exist_ok=True)
            with open(self._result_file, 'w') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
