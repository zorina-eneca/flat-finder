import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncGenerator

import aiohttp
from aiostream import stream


from config import load_filters
from models import Apartment
from scrapers.kufar import scrape_kufar, enrich_kufar_apartment
from scrapers.onliner import scrape_onliner, enrich_onliner_apartment
from scrapers.realt import scrape_realt

logger = logging.getLogger(__name__)

SEEN_FILE = Path(__file__).parent / "data" / "seen_ads.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def _load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            return set(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return set()


def _save_seen(seen: set[str]):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(
        json.dumps(list(seen), ensure_ascii=False),
        encoding="utf-8",
    )


async def run_scan(batch_size: int = 5) -> AsyncGenerator[list[Apartment], None]:
    """Run full scan across all sources and yield new matching apartment batches."""
    filters = load_filters()
    seen = _load_seen()

    try:
        connector = aiohttp.TCPConnector(limit=5)
        async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
            kufar_gen = scrape_kufar(session, max_pages=3)
            onliner_gen = scrape_onliner(session, max_pages=3)
            realt_gen = scrape_realt(session, max_pages=2)
            appartment_parser = stream.merge(
                kufar_gen,
                onliner_gen,
                realt_gen
            )
            batch: list[Apartment] = []

            # Read from 3 parsers whichever if first, collect in batches, yield further
            async with appartment_parser.stream() as parser:
                async for result in parser:
                    if isinstance(result, Exception):
                        logger.error("Scraper failed: %s", result)
                        continue
                    # Filter out already seen
                    if result.unique_key in seen:
                        logger.debug("Already seen apartment %s, skipping", result.unique_key)
                        continue
                    else:
                        seen.add(result.unique_key)
                    logger.info("Scraped apartment from %s", result.source)

                    # Quick filter before spending time on detail pages
                    if filters.rooms and result.rooms and result.rooms not in filters.rooms:
                        logger.debug("Apartment %s does not match room filter, skipping", result.unique_key)
                        continue
                    if result.price_usd is not None:
                        if result.price_usd < filters.price_min_usd:
                            logger.debug("Apartment %s is below price minimum, skipping", result.unique_key)
                            continue
                        if filters.price_max_usd is not None and result.price_usd > filters.price_max_usd:
                            logger.debug("Apartment %s is above price maximum, skipping", result.unique_key)
                            continue
                    if filters.only_owner and result.is_owner is False:
                        logger.debug("Apartment %s is not owned by owner, skipping", result.unique_key)
                        continue

                    # Enrich with detail page
                    try:
                        if result.source == "kufar":
                            logger.info("Enriching Kufar apartment %s", result.unique_key)
                            result = await enrich_kufar_apartment(session, result)
                        elif result.source == "onliner":
                            logger.info("Enriching Onliner apartment %s", result.unique_key)
                            result = await enrich_onliner_apartment(session, result)
                        # realt is already enriched from detail page
                    except Exception as e:
                        logger.warning("Enrichment failed for %s: %s", result.unique_key, e)

                    if filters.matches(result):
                        logger.info("Apartment %s matches filters, adding to batch, batch size: %d", result.unique_key, len(batch))
                        batch.append(result)
                        

                    if len(batch) >= batch_size:
                        logger.info("Yielding batch of %d apartments", len(batch))
                        yield batch
                        batch = []

                    # Small delay to be polite
                    await asyncio.sleep(0.3)

                if batch:
                    logger.info("Yielding final batch of %d apartments", len(batch))
                    yield batch
    finally:
        _save_seen(seen)
