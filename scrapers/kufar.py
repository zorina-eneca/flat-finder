import logging
import re

import aiohttp

from models import Apartment
from scrapers.common import check_pets_in_text, check_dishwasher_in_text, extract_next_data

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.kufar.by/search-api/v1/search/rendered-paginated"
DETAIL_URL = "https://re.kufar.by/vi/{ad_id}"

# Kufar kitchen appliance codes for dishwasher (посудомоечная машина)
# Search API uses code 5, detail page uses code 3
DISHWASHER_CODE_SEARCH = "5"
DISHWASHER_CODE_DETAIL = "3"
DISHWASHER_CODES = {DISHWASHER_CODE_SEARCH, DISHWASHER_CODE_DETAIL}


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

    return extract_next_data(html)


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
            if not isinstance(ad, dict):
                continue
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
            address = None
            for ap in ad.get("account_parameters", []):
                if isinstance(ap, dict) and ap.get("p") == "address":
                    address = ap.get("v")
                    break
            district = _get_param_label(ad, "re_district")
            is_owner = not ad.get("company_ad", False)

            # Check dishwasher from kitchen params
            kitchen_items = _get_param_list(ad, "flat_kitchen")
            has_dishwasher = bool(DISHWASHER_CODES & set(kitchen_items))

            # Check pets in short body
            body_short = ad.get("body_short", "") or ""
            has_pet_restriction = check_pets_in_text(body_short)

            link = ad.get("ad_link", DETAIL_URL.format(ad_id=ad_id))

            # Photos
            photos = []
            for img in ad.get("images", [])[:5]:
                path = img.get("path", "")
                if path:
                    photos.append(f"https://rms.kufar.by/v1/list_thumbs_2x/{path}")

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
                photos=photos,
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
        # Kufar moved ad data from pageProps.adData to initialState.adView.data
        ad_data = (
            next_data.get("props", {})
            .get("initialState", {})
            .get("adView", {})
            .get("data", {})
        )
        if not ad_data:
            # Fallback to old structure
            props = next_data.get("props", {}).get("pageProps", {})
            ad_data = props.get("adData", props.get("ad", {}))
        if not ad_data:
            return apt

        # Re-check dishwasher from detail page ad_parameters
        # Detail page stores params in ad_data.initial.ad_parameters
        detail_params = ad_data.get("initial", {}).get("ad_parameters", [])
        if not apt.has_dishwasher:
            for p in detail_params:
                if p.get("p") == "flat_kitchen":
                    v = p.get("v")
                    items = [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v is not None else [])
                    if DISHWASHER_CODES & set(items):
                        apt.has_dishwasher = True
                    break

        body = ad_data.get("body", "") or ""
        if body:
            apt.description = body
            if check_pets_in_text(body):
                apt.has_pet_restriction = True
            if not apt.has_dishwasher and check_dishwasher_in_text(body):
                apt.has_dishwasher = True

        # Try to get address from detail if missing
        if not apt.address:
            addr = ad_data.get("address") or ad_data.get("addressWithDistrict")
            if addr:
                apt.address = addr
            else:
                for p in detail_params:
                    if p.get("p") == "address":
                        apt.address = p.get("v")
                        break
    except Exception as e:
        logger.warning("Kufar detail parse error for %s: %s", apt.external_id, e)

    return apt
