import json
import re

# Pre-compile outside the function so it's only done once when your app starts
PET_PATTERN = re.compile(
    r'\b('
    r'без\s+животных|'
    r'без\s+питомцев|'
    r'без\s+домашних\s+животных|'
    r'животные\s+не\s+допускаются|'
    r'без\s+котов|'
    r'без\s+кошек|'
    r'с\s+животными\s+не\s+беспокоить|'
    r'с\s+питомцами\s+не\s+беспокоить|'
    r'с\s+животными\s+не\s+заселяю|'
    r'с\s+питомцами\s+не\s+заселяю|'
    r'с\s+животными\s+не\s+рассматриваю|'
    r'с\s+питомцами\s+не\s+рассматриваю|'
    r'с\s+животными\s+(и|или)\s+курящим|'
    r'животные\s+в\s+доме\s+не\s+приветствуются|'
    r'строго\s+без\s+животных'
    r')\b', 
    re.IGNORECASE | re.UNICODE
)

def check_pets_in_text(description: str) -> bool:
    """
    Analyzes a description string for pet restrictions.
    Expects a raw string (can include HTML tags or messy whitespace).
    """
    if not description:
        return False

    # 1. Clean HTML tags (if any)
    text = re.sub(r'<[^>]+>', ' ', description)

    # 2. Normalize whitespace (fixes the "БЕЗ \xa0 животных" issue)
    # .split() handles all unicode whitespace, \n, \r, \t, etc.
    normalized_text = " ".join(text.split())

    # 3. Search using pre-compiled regex
    return bool(PET_PATTERN.search(normalized_text))

def check_dishwasher_in_text(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return "посудомоечн" in lower or "посудомойк" in lower


def extract_next_data(html: str) -> dict | None:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
