import asyncio
import yaml
from core.aggregator import TickerAggregator
from core.detector import ArbitrageDetector
from utils.logger import setup_logger

logger = setup_logger('main')

async def main():
    with open('config.yaml') as f:
        config = yaml.safe_load(f)

    aggregator = TickerAggregator(
        exchanges=config['exchanges'],
        symbols=config['symbols']
    )
    detector = ArbitrageDetector()

    logger.info("套利系统启动...")

    while True:
        try:
            # 获取行情
            tickers = await aggregator.fetch_all()

            # 检测套利机会
            opportunities = detector.detect(tickers)

            if opportunities:
                for opp in opportunities:
                    logger.info(f"套利机会: {opp}")

            # 等待下一次检查
            await asyncio.sleep(config['arbitrage']['check_interval'])

        except KeyboardInterrupt:
            logger.info("系统停止")
            break
        except Exception as e:
            logger.error(f"错误: {e}")
            await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(main())
