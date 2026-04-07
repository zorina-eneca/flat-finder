import logging
import re
import json
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from models import Apartment

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

PET_KEYWORDS = [
    "без животных", "без питомцев", "без домашних животных",
    "животные не допускаются", "без котов", "без кошек", "без собак",
]


async def _fetch_detail(session: aiohttp.ClientSession, url: str) -> dict:
    """Fetch detail page and extract area, description, dishwasher info."""
    result = {"area": None, "description": None, "has_dishwasher": None, "has_pet_restriction": None}
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return result
            html = await resp.text()
    except Exception as e:
        logger.warning("Onliner detail fetch failed for %s: %s", url, e)
        return result

    soup = BeautifulSoup(html, "html.parser")

    # Try to find area
    area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*м²', html)
    if area_match:
        try:
            result["area"] = float(area_match.group(1).replace(",", "."))
        except ValueError:
            pass

    # Check description for pet restrictions
    text = soup.get_text(" ", strip=True).lower()
    result["has_pet_restriction"] = any(kw in text for kw in PET_KEYWORDS)

    # Check for dishwasher
    result["has_dishwasher"] = "посудомоечн" in text

    result["description"] = text[:500]

    return result


async def scrape_onliner(session: aiohttp.ClientSession, max_pages: int = 3) -> list[Apartment]:
    apartments = []

    for page_num in range(1, max_pages + 1):
        params = {
            **BOUNDS,
            "currency": "USD",
            "page": str(page_num),
        }
        # Add all room types
        for room_type in RENT_TYPE_MAP.values():
            params.setdefault("rent_type[]", [])

        try:
            # Build URL manually because aiohttp doesn't handle repeated params well
            room_params = "&".join(f"rent_type[]={rt}" for rt in RENT_TYPE_MAP.values())
            base_params = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = f"{SEARCH_URL}?{room_params}&{base_params}"

            async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.error("Onliner search returned %d", resp.status)
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
            apartments.append(apt)

        # Check pagination
        page_info = data.get("page", {})
        if page_num >= page_info.get("last", 1):
            break

    return apartments


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
