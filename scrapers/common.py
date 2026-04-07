import json
import re

PET_KEYWORDS = [
    "без животных", "без питомцев", "без домашних животных",
    "животные не допускаются", "без котов", "без кошек", "без собак",
    "с животными не беспокоить", "с питомцами не беспокоить",
]


def check_pets_in_text(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in PET_KEYWORDS)


def check_dishwasher_in_text(text: str) -> bool:
    if not text:
        return False
    return "посудомоечн" in text.lower()


def extract_next_data(html: str) -> dict | None:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
