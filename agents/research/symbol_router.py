"""标的路由 Agent - 管理活跃交易标的集，处理标的轮换"""

import time
import json
import os
from agents.base import BaseAgent


class SymbolRouter(BaseAgent):
    name = "symbol_router"
    subscriptions = ["research_result"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self._active_symbols = []
        self._symbol_meta = {}
        self._max_active = (config or {}).get('max_active_symbols', 5)
        self._last_update_time = 0
        self._min_rotation_interval = 3600

    async def setup(self):
        self.logger.info("标的路由Agent就绪")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_result':
            await self._handle_research_result(msg['payload'])

    async def _handle_research_result(self, payload: dict):
        selected = payload.get('selected', [])
        if not selected:
            self.logger.warning(
                f"[路由] 研判未选出任何标的，保留当前活跃标的: {self._active_symbols}"
            )
            # 通知运营者，避免无声失败
            await self.publish("telegram_alert", {
                "level": "warning",
                "message": f"研判层全拒：本轮未选出任何标的，继续监控旧标的 {self._active_symbols}",
            })
            await self.publish("symbol_update", {
                "active_symbols": self._active_symbols,
                "added": [],
                "removed": [],
                "unchanged": True,
                "reason": "no_selected_symbols",
            })
            return

        now = time.time()
        if now - self._last_update_time < self._min_rotation_interval and self._active_symbols:
            self.logger.info("[路由] 轮换冷却中，跳过本次更新")
            return

        new_symbols = [s['symbol'] for s in selected[:self._max_active]]
        old_symbols = self._active_symbols.copy()

        added = [s for s in new_symbols if s not in old_symbols]
        removed = [s for s in old_symbols if s not in new_symbols]

        removed_action = {}
        for s in removed:
            removed_action[s] = "close_at_market"

        self._active_symbols = new_symbols
        self._symbol_meta = {s['symbol']: s for s in selected[:self._max_active]}
        self._last_update_time = now

        await self.publish("symbol_update", {
            "active_symbols": new_symbols,
            "added": added,
            "removed": removed,
            "removed_action": removed_action,
            "symbol_meta": self._symbol_meta,
        })

        self.logger.info(
            f"[路由] 活跃标的更新: {new_symbols} "
            f"(+{added}, -{removed})"
        )

        for symbol in removed:
            if removed_action.get(symbol) == "close_at_market":
                await self.publish("trade_decision", {
                    "action": "close",
                    "symbol": symbol,
                    "confidence": 100,
                    "size_pct": 1.0,
                    "reasoning": "标的轮换，平仓退出",
                }, symbol=symbol)
                self.logger.info(f"[路由] 发送平仓指令: {symbol}")

    def _get_position_symbols(self) -> list:
        """读取持仓标的列表（统一为内部规范 BASE-USDT）。

        fail-safe：文件缺失/损坏 → 返回 []，不抛异常。
        持仓信息不可得时，轮换退化为旧强平行为，绝不产生无人看管持仓。
        """
        from utils.state_paths import get_state_paths
        from utils.symbol import to_internal
        positions_file = get_state_paths().positions
        if not os.path.exists(positions_file):
            return []
        try:
            with open(positions_file, 'r') as f:
                positions = json.load(f)
            return [to_internal(s) for s in positions.keys()]
        except Exception as e:
            self.logger.warning(f"[路由] 读取持仓失败，退化为旧轮换行为: {e}")
            return []

    @property
    def active_symbols(self) -> list:
        return self._active_symbols.copy()
