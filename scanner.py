import asyncio
import json
import logging
from pathlib import Path

import aiohttp

from config import Filters, load_filters
from models import Apartment
from scrapers.kufar import scrape_kufar, enrich_kufar_apartment
from scrapers.onliner import scrape_onliner, enrich_onliner_apartment
from scrapers.realt import scrape_realt

logger = logging.getLogger(__name__)

SEEN_FILE = Path(__file__).parent / "seen_ads.json"
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
    SEEN_FILE.write_text(
        json.dumps(list(seen), ensure_ascii=False),
        encoding="utf-8",
    )


async def run_scan() -> list[Apartment]:
    """Run full scan across all sources, return new apartments matching filters."""
    filters = load_filters()
    seen = _load_seen()
    all_apartments: list[Apartment] = []

    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        # Scrape all sources concurrently
        results = await asyncio.gather(
            scrape_kufar(session, max_pages=3),
            scrape_onliner(session, max_pages=3),
            scrape_realt(session, max_pages=2),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            source_name = ["kufar", "onliner", "realt"][i]
            if isinstance(result, Exception):
                logger.error("Scraper %s failed: %s", source_name, result)
                continue
            logger.info("Scraped %d apartments from %s", len(result), source_name)
            all_apartments.extend(result)

        # Filter out already seen
        new_apartments = [a for a in all_apartments if a.unique_key not in seen]
        logger.info("New apartments: %d (total scraped: %d)", len(new_apartments), len(all_apartments))

        if not new_apartments:
            return []

        # Enrich apartments that need detail page (for pet/dishwasher check)
        enriched = []
        for apt in new_apartments:
            # Quick filter before spending time on detail pages
            if filters.rooms and apt.rooms and apt.rooms not in filters.rooms:
                seen.add(apt.unique_key)
                continue
            if apt.price_usd is not None:
                if apt.price_usd < filters.price_min_usd:
                    seen.add(apt.unique_key)
                    continue
                if filters.price_max_usd is not None and apt.price_usd > filters.price_max_usd:
                    seen.add(apt.unique_key)
                    continue
            if filters.only_owner and apt.is_owner is False:
                seen.add(apt.unique_key)
                continue

            # Enrich with detail page
            try:
                if apt.source == "kufar":
                    apt = await enrich_kufar_apartment(session, apt)
                elif apt.source == "onliner":
                    apt = await enrich_onliner_apartment(session, apt)
                # realt is already enriched from detail page
            except Exception as e:
                logger.warning("Enrichment failed for %s: %s", apt.unique_key, e)

            enriched.append(apt)
            # Small delay to be polite
            await asyncio.sleep(0.3)

        # Apply full filters
        matched = [a for a in enriched if filters.matches(a)]
        logger.info("After filtering: %d apartments", len(matched))

        # Mark all new as seen
        for apt in new_apartments:
            seen.add(apt.unique_key)
        _save_seen(seen)

    return matched
