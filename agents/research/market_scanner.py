"""市场扫描 Agent - 采集Top永续合约的多维度指标"""

import asyncio
import copy
import os
import time
import ccxt
import aiohttp
from dotenv import load_dotenv
from agents.base import BaseAgent

load_dotenv()


class MarketScanner(BaseAgent):
    name = "market_scanner"
    subscriptions = ["research_trigger"]

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.exchange = None
        self.top_n = 50
        self.min_volume_24h = 5_000_000
        self.research_min_volume_24h_usdt = float(self.config.get('research_min_volume_24h_usdt', 50_000_000))
        self.research_min_open_interest_usd = float(self.config.get('research_min_open_interest_usd', 10_000_000))
        self._current_cycle_id = None
        self._last_good_candidates = []
        self._last_good_total_scanned = 0
        self._last_good_filtered = 0
        self._last_good_liquidity_filter = None
        self._last_good_ts = None

    async def setup(self):
        from utils.exchange_factory import create_exchange
        self.exchange = create_exchange(self.config, purpose="market_scanner")

        self.logger.info(f"市场扫描Agent就绪: {self.config.get('exchange', 'okx')}")

    async def on_message(self, msg: dict):
        if msg['type'] == 'research_trigger':
            self._current_cycle_id = msg.get('payload', {}).get('cycle_id')
            await self._scan_market()

    async def _scan_market(self):
        try:
            self.logger.info("[扫描] 开始采集市场数据...")
            tickers = await self._fetch_tickers_with_retry()

            candidates = []
            # 非加密资产黑名单：与 multi_data_collector.py 保持一致，避免研判选了交易层会过滤
            NON_CRYPTO_BLACKLIST = {'CL', 'XAU', 'XAG', 'OIL', 'GAS', 'SPX', 'NDX'}
            for symbol, ticker in tickers.items():
                if not symbol.endswith('/USDT:USDT') and not symbol.endswith('-USDT-SWAP'):
                    continue
                if ':' in symbol:
                    clean_symbol = symbol.split(':')[0].replace('/', '-')
                else:
                    clean_symbol = symbol

                base = clean_symbol.split('-')[0].upper()
                if base in NON_CRYPTO_BLACKLIST:
                    continue

                volume_24h = float(ticker.get('quoteVolume', 0) or 0)
                if volume_24h == 0:
                    info = ticker.get('info', {})
                    vol_ccy = float(info.get('volCcy24h', 0) or 0)
                    last_price = float(ticker.get('last', 0) or 0)
                    if vol_ccy > 0 and last_price > 0:
                        volume_24h = vol_ccy * last_price
                    else:
                        base_vol = float(ticker.get('baseVolume', 0) or 0)
                        volume_24h = base_vol * last_price
                if volume_24h < self.min_volume_24h:
                    continue

                high = float(ticker.get('high', 0) or 0)
                low = float(ticker.get('low', 0) or 0)
                last = float(ticker.get('last', 0) or 0)
                change_pct = float(ticker.get('percentage', 0) or 0)

                volatility = ((high - low) / low * 100) if low > 0 else 0

                candidates.append({
                    "symbol": clean_symbol,
                    "raw_symbol": symbol,
                    "price": last,
                    "volume_24h": volume_24h,
                    "volatility_pct": round(volatility, 2),
                    "change_24h_pct": round(change_pct, 2),
                    "high_24h": high,
                    "low_24h": low,
                })

            # 综合排序：成交量权重 + 动量权重（有趋势的标的优先）
            for c in candidates:
                vol_score = min(c['volume_24h'] / 50_000_000, 1.0)  # 5000万封顶归一化
                momentum_score = min(abs(c['change_24h_pct']) / 10, 1.0)  # 10%封顶归一化
                c['_rank_score'] = vol_score * 0.5 + momentum_score * 0.5
            candidates.sort(key=lambda x: x['_rank_score'], reverse=True)
            top_candidates = candidates[:self.top_n]
            for c in top_candidates:
                del c['_rank_score']

            # 上线时间过滤：排除OKX上线不足1年的标的（月K线<12根）
            async def _check_age(c):
                inst_id = c['raw_symbol'].replace('/USDT:USDT', '-USDT-SWAP').replace('/', '-')
                c['_monthly_klines'] = await self._fetch_monthly_kline_count(inst_id)

            await asyncio.gather(*[_check_age(c) for c in top_candidates])
            before_filter = len(top_candidates)
            top_candidates = [c for c in top_candidates if c.pop('_monthly_klines', 0) >= 12]
            filtered_by_age = before_filter - len(top_candidates)
            if filtered_by_age > 0:
                self.logger.info(f"[扫描] 上线时间过滤: 移除{filtered_by_age}个不足1年的标的")

            async def _enrich(c):
                c['funding_rate'] = await self._fetch_funding(c['raw_symbol'])
                inst_id = c['raw_symbol'].replace('/USDT:USDT', '-USDT-SWAP').replace('/', '-')
                c['long_short_ratio'] = await self._fetch_long_short_ratio(inst_id)
                c['open_interest_usd'] = await self._fetch_open_interest(inst_id)
                c['sl_structure'] = await self._fetch_sl_structure(inst_id, c['price'])
                del c['raw_symbol']

            await asyncio.gather(*[_enrich(c) for c in top_candidates])

            top_candidates, liquidity_filter = self._apply_liquidity_hard_filter(top_candidates)
            self._remember_last_good(top_candidates, len(tickers), len(candidates), liquidity_filter)

            await self.publish("research_market_data", {
                "candidates": top_candidates,
                "total_scanned": len(tickers),
                "filtered": len(candidates),
                "cycle_id": self._current_cycle_id,
                "liquidity_filter": liquidity_filter,
            })

            self.logger.info(
                f"[扫描] 完成: {len(tickers)}个合约 → {len(candidates)}个符合条件 → "
                f"流动性过滤移除{liquidity_filter['removed']}个 → Top{len(top_candidates)}"
            )

        except Exception as e:
            self.logger.error(f"市场扫描失败: {e}")
            await self._publish_degraded_market_data(e)

    async def _fetch_tickers_with_retry(self):
        attempts = max(1, int(self.config.get('market_scan_retries', 3)))
        delay = max(0.0, float(self.config.get('market_scan_retry_delay', 1.0)))
        last_error = None
        loop = asyncio.get_event_loop()

        for attempt in range(1, attempts + 1):
            try:
                return await loop.run_in_executor(None, self.exchange.fetch_tickers)
            except Exception as e:
                last_error = e
                if attempt >= attempts:
                    break
                self.logger.warning(
                    f"[扫描] fetch_tickers失败({attempt}/{attempts})，{delay:.1f}s后重试: {e}"
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                    delay *= 2

        raise last_error

    def _liquidity_rejection_reason(self, candidate: dict):
        volume_24h = float(candidate.get('volume_24h') or 0)
        if volume_24h < self.research_min_volume_24h_usdt:
            return "volume_below_min"

        open_interest = candidate.get('open_interest_usd')
        if open_interest is None:
            return "open_interest_missing"
        if float(open_interest or 0) < self.research_min_open_interest_usd:
            return "open_interest_below_min"
        return None

    def _apply_liquidity_hard_filter(self, candidates: list) -> tuple[list, dict]:
        kept = []
        examples = []

        for candidate in candidates:
            reason = self._liquidity_rejection_reason(candidate)
            if reason is None:
                kept.append(candidate)
                continue

            if len(examples) < 5:
                examples.append({
                    "symbol": candidate.get("symbol"),
                    "volume_24h": candidate.get("volume_24h"),
                    "open_interest_usd": candidate.get("open_interest_usd"),
                    "reason": reason,
                })

        summary = {
            "min_volume_24h_usdt": self.research_min_volume_24h_usdt,
            "min_open_interest_usd": self.research_min_open_interest_usd,
            "removed": len(candidates) - len(kept),
            "kept": len(kept),
            "examples": examples,
        }

        if summary["removed"] > 0:
            sample = ", ".join(f"{item['symbol']}:{item['reason']}" for item in examples)
            self.logger.info(
                f"[扫描] 初选流动性硬过滤: 移除{summary['removed']}个，"
                f"保留{summary['kept']}个，样例={sample}"
            )

        return kept, summary

    def _remember_last_good(self, candidates: list, total_scanned: int, filtered: int, liquidity_filter: dict = None):
        if not candidates:
            return
        self._last_good_candidates = copy.deepcopy(candidates)
        self._last_good_total_scanned = total_scanned
        self._last_good_filtered = filtered
        self._last_good_liquidity_filter = copy.deepcopy(liquidity_filter)
        self._last_good_ts = time.time()

    async def _publish_degraded_market_data(self, error: Exception):
        if self._last_good_candidates:
            candidates = copy.deepcopy(self._last_good_candidates)
            total_scanned = self._last_good_total_scanned
            filtered = self._last_good_filtered
            liquidity_filter = copy.deepcopy(self._last_good_liquidity_filter)
            fallback_source = "last_good"
            stale = True
            last_good_age_sec = round(time.time() - self._last_good_ts, 1) if self._last_good_ts else None
        else:
            candidates = []
            total_scanned = 0
            filtered = 0
            liquidity_filter = None
            fallback_source = "empty"
            stale = False
            last_good_age_sec = None

        payload = {
            "candidates": candidates,
            "total_scanned": total_scanned,
            "filtered": filtered,
            "cycle_id": self._current_cycle_id,
            "degraded": True,
            "stale": stale,
            "fallback_source": fallback_source,
            "error": str(error),
        }
        if last_good_age_sec is not None:
            payload["last_good_age_sec"] = last_good_age_sec
        if liquidity_filter is not None:
            payload["liquidity_filter"] = liquidity_filter

        await self.publish("research_market_data", payload)
        self.logger.warning(
            f"[扫描] 发布降级市场数据: cycle={self._current_cycle_id} "
            f"source={fallback_source} candidates={len(candidates)}"
        )

    async def _fetch_funding(self, symbol: str):
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            market = self.exchange.market(symbol)
            if not market.get('swap'):
                return None
            funding = await loop.run_in_executor(None, self.exchange.fetch_funding_rate, symbol)
            return funding.get('fundingRate', None)
        except Exception:
            return None

    async def _fetch_long_short_ratio(self, inst_id: str):
        """Binance公开接口：Top Trader多空比（OKX rubik接口已不可用）"""
        try:
            symbol = inst_id.replace('-USDT-SWAP', 'USDT').replace('-', '')
            url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
            params = {"symbol": symbol, "period": "1h", "limit": 1}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            if data:
                return float(data[0].get('longShortRatio', 1.0))
            return None
        except Exception:
            return None

    async def _fetch_open_interest(self, inst_id: str):
        """OKX公开接口：持仓量（USD）"""
        try:
            url = "https://www.okx.com/api/v5/public/open-interest"
            params = {"instType": "SWAP", "instId": inst_id}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            records = data.get('data', [])
            if records:
                return float(records[0].get('oiUsd', 0))
            return None
        except Exception:
            return None

    async def _fetch_monthly_kline_count(self, inst_id: str) -> int:
        """获取月K线数量，用于判断上线时间是否满1年（>=12根）"""
        try:
            url = "https://www.okx.com/api/v5/market/candles"
            params = {"instId": inst_id, "bar": "1M", "limit": "12"}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return 12  # API失败不过滤
                    data = await resp.json()
            rows = data.get('data', [])
            return len(rows)
        except Exception:
            return 12  # 异常不过滤

    async def _fetch_sl_structure(self, inst_id: str, price: float) -> dict:
        """预计算止损结构可行性：近20根1h K线的支撑/阻力距离和ATR"""
        try:
            url = "https://www.okx.com/api/v5/market/candles"
            params = {"instId": inst_id, "bar": "1H", "limit": "25"}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return {"sl_viable": True}  # 拉取失败不过滤
                    data = await resp.json()
            rows = data.get('data', [])
            if len(rows) < 10:
                return {"sl_viable": True}

            import pandas as pd
            klines = [[float(x) for x in row[:6]] for row in reversed(rows)]
            df = pd.DataFrame(klines, columns=['t', 'o', 'h', 'l', 'c', 'v'])

            # 用倒数第3~22根K线（排除最近2根未完成），计算近20根范围
            window = df.iloc[-22:-2]
            low_20 = float(window['l'].min())
            high_20 = float(window['h'].max())
            tr = pd.concat([df['h'] - df['l'],
                            (df['h'] - df['c'].shift()).abs(),
                            (df['l'] - df['c'].shift()).abs()], axis=1).max(axis=1)
            atr_pct = float(tr.rolling(14).mean().iloc[-2]) / price if price > 0 else 0.02

            support_dist = (price - low_20) / price  # 做多止损空间
            resist_dist = (high_20 - price) / price   # 做空止损空间

            # 可交易：止损空间在 1.5%~15% 之间（放得下且不过宽）
            long_viable = 0.015 <= support_dist <= 0.15
            short_viable = 0.015 <= resist_dist <= 0.15

            return {
                "support_dist_pct": round(support_dist * 100, 2),
                "resist_dist_pct": round(resist_dist * 100, 2),
                "atr_pct": round(atr_pct * 100, 2),
                "long_viable": long_viable,
                "short_viable": short_viable,
                "sl_viable": long_viable or short_viable,
            }
        except Exception:
            return {"sl_viable": True}  # 异常不过滤
