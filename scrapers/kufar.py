import logging
import re
import json
from typing import AsyncIterator

import aiohttp
from bs4 import BeautifulSoup

from models import Apartment

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"
DETAIL_URL = "https://re.kufar.by/vi/{ad_id}"

# Kufar kitchen appliance codes: 5 = dishwasher (посудомоечная машина)
DISHWASHER_CODE = "5"

PET_KEYWORDS = [
    "без животных", "без питомцев", "без домашних животных",
    "животные не допускаются", "без котов", "без кошек", "без собак",
]


def _get_param(ad: dict, key: str) -> str | None:
    for p in ad.get("ad_parameters", []):
        if p.get("p") == key:
            return p.get("v")
    return None


def _get_param_list(ad: dict, key: str) -> list[str]:
    for p in ad.get("ad_parameters", []):
        if p.get("p") == key:
            v = p.get("v")
            if isinstance(v, list):
                return [str(x) for x in v]
            if v is not None:
                return [str(v)]
    return []


def _get_param_label(ad: dict, key: str) -> str | None:
    for p in ad.get("ad_parameters", []):
        if p.get("p") == key:
            return p.get("vl")
    return None


def _parse_coords(ad: dict) -> tuple[float | None, float | None]:
    raw = _get_param(ad, "coordinates")
    if isinstance(raw, list) and len(raw) == 2:
        try:
            return float(raw[1]), float(raw[0])  # lat, lon
        except (ValueError, TypeError):
            pass
    return None, None


async def _fetch_detail(session: aiohttp.ClientSession, ad_id: str) -> dict | None:
    url = DETAIL_URL.format(ad_id=ad_id)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception as e:
        logger.warning("Kufar detail fetch failed for %s: %s", ad_id, e)
        return None

    # Extract __NEXT_DATA__
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _check_pets_in_text(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in PET_KEYWORDS)


def _check_dishwasher_in_text(text: str) -> bool:
    if not text:
        return False
    return "посудомоечн" in text.lower()


async def scrape_kufar(session: aiohttp.ClientSession, max_pages: int = 3) -> list[Apartment]:
    apartments = []
    cursor = None

    for page in range(max_pages):
        params = {
            "cat": "1010",
            "typ": "let",
            "rgn": "7",
            "cur": "BYN",
            "sort": "lst.d",
            "size": "30",
        }
        if cursor:
            params["cursor"] = cursor

        try:
            async with session.get(SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.error("Kufar search returned %d", resp.status)
                    break
                data = await resp.json()
        except Exception as e:
            logger.error("Kufar search failed: %s", e)
            break

        ads = data.get("ads", [])
        if not ads:
            break

        for ad in ads:
            ad_id = str(ad.get("ad_id", ad.get("list_id", "")))
            if not ad_id:
                continue

            rooms_str = _get_param(ad, "rooms")
            rooms = int(rooms_str) if rooms_str and rooms_str.isdigit() else None

            price_byn = None
            price_usd = None
            raw_byn = ad.get("price_byn")
            raw_usd = ad.get("price_usd")
            if raw_byn:
                try:
                    price_byn = float(raw_byn) / 100
                except (ValueError, TypeError):
                    pass
            if raw_usd:
                try:
                    price_usd = float(raw_usd) / 100
                except (ValueError, TypeError):
                    pass

            area_str = _get_param(ad, "size")
            area = None
            if area_str:
                try:
                    area = float(area_str)
                except (ValueError, TypeError):
                    pass

            lat, lon = _parse_coords(ad)
            address = ad.get("account_parameters", {}).get("address", None)
            district = _get_param_label(ad, "re_district")
            is_owner = not ad.get("company_ad", False)

            # Check dishwasher from kitchen params
            kitchen_items = _get_param_list(ad, "flat_kitchen")
            has_dishwasher = DISHWASHER_CODE in kitchen_items

            # Check pets in short body
            body_short = ad.get("body_short", "") or ""
            has_pet_restriction = _check_pets_in_text(body_short)

            link = ad.get("ad_link", DETAIL_URL.format(ad_id=ad_id))

            apt = Apartment(
                source="kufar",
                external_id=ad_id,
                url=link,
                rooms=rooms,
                price_byn=price_byn,
                price_usd=price_usd,
                area=area,
                address=address,
                district=district,
                is_owner=is_owner,
                has_dishwasher=has_dishwasher,
                has_pet_restriction=has_pet_restriction,
                updated_at=ad.get("list_time", ""),
                lat=lat,
                lon=lon,
                description=body_short,
            )
            apartments.append(apt)

        # Pagination
        pagination = data.get("pagination", {})
        pages = pagination.get("pages", [])
        next_page = next((p for p in pages if p.get("label") == "next"), None)
        if next_page and next_page.get("token"):
            cursor = next_page["token"]
        else:
            break

    return apartments


async def enrich_kufar_apartment(session: aiohttp.ClientSession, apt: Apartment) -> Apartment:
    """Fetch detail page to get full description, check pets and dishwasher more thoroughly."""
    next_data = await _fetch_detail(session, apt.external_id)
    if not next_data:
        return apt

    try:
        props = next_data.get("props", {}).get("pageProps", {})
        ad_data = props.get("adData", props.get("ad", {}))
        if not ad_data:
            return apt

        body = ad_data.get("body", "") or ""
        if body:
            apt.description = body
            if _check_pets_in_text(body):
                apt.has_pet_restriction = True
            if not apt.has_dishwasher and _check_dishwasher_in_text(body):
                apt.has_dishwasher = True

        # Try to get address from detail if missing
        if not apt.address:
            for p in ad_data.get("ad_parameters", []):
                if p.get("p") == "address":
                    apt.address = p.get("v")
                    break
    except Exception as e:
        logger.warning("Kufar detail parse error for %s: %s", apt.external_id, e)

    return apt
