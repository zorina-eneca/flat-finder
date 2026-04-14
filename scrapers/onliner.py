from typing import AsyncGenerator
import logging
import re
import json

import aiohttp

from models import Apartment
from scrapers.common import check_pets_in_text, check_dishwasher_in_text

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ak.api.onliner.by/search/apartments"

# Minsk bounding box
BOUNDS = {
    "bounds[lb][lat]": "53.7097",
    "bounds[lb][long]": "27.2667",
    "bounds[rt][lat]": "54.0856",
    "bounds[rt][long]": "27.8572",
}

RENT_TYPE_MAP = {1: "1_room", 2: "2_rooms", 3: "3_rooms", 4: "4_rooms"}


async def _fetch_detail(session: aiohttp.ClientSession, url: str) -> dict:
    """Fetch detail page and extract area, description, dishwasher info."""
    result = {"area": None, "description": None, "has_dishwasher": None, "has_pet_restriction": None}
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("Onliner detail %s returned %d", url, resp.status)
                return result
            html = await resp.text()
    except Exception as e:
        logger.warning("Onliner detail fetch failed for %s: %s", url, e)
        return result

    # Try __NEXT_DATA__ first
    next_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if next_match:
        try:
            next_data = json.loads(next_match.group(1))
            apt_data = next_data.get("props", {}).get("pageProps", {}).get("apartment", {})
            if apt_data:
                area = apt_data.get("area", {})
                if isinstance(area, dict):
                    result["area"] = area.get("total")
                elif isinstance(area, (int, float)):
                    result["area"] = float(area)
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: regex for area in HTML
    if result["area"] is None:
        # Look for patterns like "35.6 м²" or "48 м²"
        area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]', html)
        if area_match:
            try:
                result["area"] = float(area_match.group(1).replace(",", "."))
            except ValueError:
                pass

    # Get full text for keyword checks
    text = re.sub(r'<[^>]+>', ' ', html).lower()

    # Check description for pet restrictions
    result["has_pet_restriction"] = check_pets_in_text(text)

    # Check for dishwasher
    result["has_dishwasher"] = check_dishwasher_in_text(text)

    return result


async def scrape_onliner(session: aiohttp.ClientSession, max_pages: int = 3) -> AsyncGenerator[Apartment, None]:

    for page_num in range(1, max_pages + 1):
        try:
            parts = [f"rent_type[]={rt}" for rt in RENT_TYPE_MAP.values()]
            for k, v in BOUNDS.items():
                parts.append(f"{k}={v}")
            parts.append("currency=USD")
            parts.append(f"page={page_num}")
            full_url = f"{SEARCH_URL}?{'&'.join(parts)}"

            logger.info("Onliner URL: %s", full_url)
            async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Onliner search returned %d: %s", resp.status, body[:500])
                    break
                data = await resp.json(content_type=None)
        except Exception as e:
            logger.error("Onliner search failed: %s", e)
            break

        items = data.get("apartments", [])
        if not items:
            break

        for item in items:
            apt_id = str(item.get("id", ""))
            if not apt_id:
                continue

            # Parse rooms from rent_type
            rent_type = item.get("rent_type", "")
            rooms = None
            if rent_type:
                m = re.match(r"(\d+)_room", rent_type)
                if m:
                    rooms = int(m.group(1))

            # Parse prices
            price_byn = None
            price_usd = None
            price_data = item.get("price", {})
            converted = price_data.get("converted", {})
            byn_data = converted.get("BYN", {})
            usd_data = converted.get("USD", {})
            if byn_data.get("amount"):
                try:
                    price_byn = float(byn_data["amount"])
                except (ValueError, TypeError):
                    pass
            if usd_data.get("amount"):
                try:
                    price_usd = float(usd_data["amount"])
                except (ValueError, TypeError):
                    pass

            # Location
            location = item.get("location", {})
            address = location.get("address") or location.get("user_address")
            lat = location.get("latitude")
            lon = location.get("longitude")

            # Owner
            contact = item.get("contact", {})
            is_owner = contact.get("owner", None)

            url = item.get("url", f"https://r.onliner.by/ak/apartments/{apt_id}")
            updated_at = item.get("last_time_up") or item.get("created_at", "")

            # Photo (single thumbnail from list API)
            photo = item.get("photo")
            photos = [photo] if photo else []

            apt = Apartment(
                source="onliner",
                external_id=apt_id,
                url=url,
                rooms=rooms,
                price_byn=price_byn,
                price_usd=price_usd,
                area=None,  # not in list API, will enrich from detail
                address=address,
                district=None,
                is_owner=is_owner,
                has_dishwasher=None,
                has_pet_restriction=None,
                updated_at=updated_at,
                lat=lat,
                lon=lon,
                photos=photos,
            )
            yield apt

        # Check pagination
        page_info = data.get("page", {})
        if page_num >= page_info.get("last", 1):
            break


async def enrich_onliner_apartment(session: aiohttp.ClientSession, apt: Apartment) -> Apartment:
    """Fetch detail page to get area, description, dishwasher, pet check."""
    detail = await _fetch_detail(session, apt.url)
    if detail["area"] is not None:
        apt.area = detail["area"]
    if detail["has_dishwasher"] is not None:
        apt.has_dishwasher = detail["has_dishwasher"]
    if detail["has_pet_restriction"] is not None:
        apt.has_pet_restriction = detail["has_pet_restriction"]
    if detail["description"]:
        apt.description = detail["description"]
    return apt
