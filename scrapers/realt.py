import logging
import re
from typing import AsyncGenerator

import aiohttp

from models import Apartment
from scrapers.common import extract_next_data, check_pets_in_text

logger = logging.getLogger(__name__)

LIST_URL = "https://realt.by/rent/flat-for-long/"
DETAIL_URL_TEMPLATE = "https://realt.by/rent-flat-for-long/object/{code}/"


async def _get_listing_codes(session: aiohttp.ClientSession, max_pages: int = 3) -> AsyncGenerator[str, None]:
    """Collect listing codes from listing pages."""
    codes = set()
    for page in range(1, max_pages + 1):
        params = {"page": str(page)} if page > 1 else {}
        try:
            async with session.get(LIST_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.error("Realt list page %d returned %d", page, resp.status)
                    break
                html = await resp.text()
        except Exception as e:
            logger.error("Realt list page failed: %s", e)
            break

        # Extract listing codes from links
        found = re.findall(r'href="/rent-flat-for-long/object/(\d+)/"', html)
        if not found:
            break
        for item in found:
            if item not in codes:
                codes.add(item)
                yield item


async def _fetch_detail(session: aiohttp.ClientSession, code: str) -> Apartment | None:
    url = DETAIL_URL_TEMPLATE.format(code=code)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as e:
        logger.warning("Realt detail fetch failed for %s: %s", code, e)
        return None

    next_data = extract_next_data(html)
    if not next_data:
        # Fallback: try to parse from page text
        return None

    try:
        obj = next_data["props"]["pageProps"]["object"]
    except (KeyError, TypeError):
        return None

    rooms = obj.get("rooms")
    area = obj.get("areaTotal")
    address = obj.get("address", "")
    metro = obj.get("metroStationName")
    district = metro if metro else None

    # Price — priceRates uses ISO 4217 numeric codes: 840=USD, 933=BYN
    price_byn = None
    price_usd = None
    price_rates = obj.get("priceRates") or {}
    if price_rates:
        raw_usd = price_rates.get("840") or price_rates.get(840) or price_rates.get("USD")
        raw_byn = price_rates.get("933") or price_rates.get(933) or price_rates.get("BYN")
        if raw_usd is not None:
            try:
                price_usd = float(raw_usd)
            except (ValueError, TypeError):
                pass
        if raw_byn is not None:
            try:
                price_byn = float(raw_byn)
            except (ValueError, TypeError):
                pass

    # Location
    location = obj.get("location", [])
    lon, lat = None, None
    if isinstance(location, list) and len(location) == 2:
        try:
            lon, lat = float(location[0]), float(location[1])
        except (ValueError, TypeError):
            pass

    # Appliances & dishwasher
    appliances = obj.get("appliances", []) or []
    has_dishwasher = any("посудомоечн" in a.lower() for a in appliances)

    # Description & pet check
    description = obj.get("headline") or obj.get("description", "")
    description = re.sub(r'<[^>]+>', ' ', description)
    # Strip HTML tags for storage, but pass raw to check_pets_in_text
    has_pet_restriction = check_pets_in_text(description)

    is_owner = False if obj.get("agencyUuid") is not None else True
    # Agencies often have company-like names or specific markers

    updated_at = obj.get("updatedAt") or obj.get("createdAt", "")

    # Photos
    photos = []
    for slide in (obj.get("slides") or [])[:5]:
        if isinstance(slide, str):
            photos.append(slide)
        elif isinstance(slide, dict) and slide.get("url"):
            photos.append(slide["url"])

    return Apartment(
        source="realt",
        external_id=str(code),
        url=url,
        rooms=rooms,
        price_byn=price_byn,
        price_usd=price_usd,
        area=area,
        address=address,
        district=district,
        is_owner=is_owner,
        has_dishwasher=has_dishwasher,
        has_pet_restriction=has_pet_restriction,
        updated_at=updated_at,
        lat=lat,
        lon=lon,
        description=description[:500] if description else None,
        photos=photos,
    )


async def scrape_realt(session: aiohttp.ClientSession, max_pages: int = 3) -> AsyncGenerator[Apartment, None]:
    async for code in _get_listing_codes(session, max_pages):
        apt = await _fetch_detail(session, code)
        if apt:
            yield apt
