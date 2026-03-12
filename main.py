import asyncio
from binance_streams import MarketScanner


async def main():
    scanner = MarketScanner()
    await scanner.run()


if __name__ == "__main__":
    asyncio.run(main())
