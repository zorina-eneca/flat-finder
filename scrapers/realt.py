import logging
import re
from typing import AsyncGenerator

import aiohttp

from models import Apartment
from scrapers.common import PET_KEYWORDS, extract_next_data

logger = logging.getLogger(__name__)

LIST_URL = "https://realt.by/rent/flat-for-long/"
DETAIL_URL_TEMPLATE = "https://realt.by/rent-flat-for-long/object/{code}/"


async def _get_listing_codes(session: aiohttp.ClientSession, max_pages: int = 3) -> set[str]:
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
    description = obj.get("description", "") or ""
    # Strip HTML tags
    clean_desc = re.sub(r'<[^>]+>', ' ', description)
    has_pet_restriction = any(kw in clean_desc.lower() for kw in PET_KEYWORDS)

    # Also check full page text for pet keywords if not found in description
    if not has_pet_restriction:
        full_text = re.sub(r'<[^>]+>', ' ', html[:50000]).lower()
        has_pet_restriction = any(kw in full_text for kw in PET_KEYWORDS)

    # Check dishwasher in full text too
    if not has_dishwasher:
        full_text_lower = html[:50000].lower()
        # Check for dishwasher mention that's not crossed out
        if "посудомоечн" in full_text_lower:
            # Check it's not in a strikethrough/line-through context
            # Simple heuristic: if "line-through" appears near "посудомоечн", it's crossed out
            idx = full_text_lower.find("посудомоечн")
            nearby = full_text_lower[max(0, idx - 200):idx + 50]
            if "line-through" not in nearby and "<del>" not in nearby and "<s>" not in nearby:
                has_dishwasher = True

    is_owner = None
    contact_name = obj.get("contactName", "")
    # Agencies often have company-like names or specific markers
    seller_type = obj.get("sellerType", "")
    if seller_type:
        is_owner = seller_type.lower() in ("собственник", "owner")

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
        description=clean_desc[:500] if clean_desc else None,
        photos=photos,
    )


async def scrape_realt(session: aiohttp.ClientSession, max_pages: int = 3) -> AsyncGenerator[Apartment, None]:
    async for code in _get_listing_codes(session, max_pages):
        apt = await _fetch_detail(session, code)
        if apt:
            yield apt
