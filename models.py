from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

MINSK_TZ = timezone(timedelta(hours=3))


@dataclass
class Apartment:
    source: str  # kufar, onliner, realt
    external_id: str
    url: str
    rooms: Optional[int] = None
    price_byn: Optional[float] = None
    price_usd: Optional[float] = None
    area: Optional[float] = None
    address: Optional[str] = None
    district: Optional[str] = None
    is_owner: Optional[bool] = None  # True = owner, False = agency
    has_dishwasher: Optional[bool] = None
    has_pet_restriction: Optional[bool] = None
    updated_at: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    description: Optional[str] = None
    photos: list[str] = field(default_factory=list)

    @property
    def unique_key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def yandex_maps_url(self) -> str | None:
        if self.lat and self.lon:
            return f"https://yandex.by/maps/?pt={self.lon},{self.lat}&z=16&l=map"
        return None

    @property
    def updated_at_formatted(self) -> str:
        if not self.updated_at:
            return "н/д"
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(self.updated_at, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=MINSK_TZ)
                return dt.astimezone(MINSK_TZ).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                continue
        return self.updated_at

    def format_message(self) -> str:
        price_parts = []
        if self.price_usd is not None:
            price_parts.append(f"{self.price_usd:.0f} $")
        if self.price_byn is not None:
            price_parts.append(f"{self.price_byn:.0f} BYN")
        price_str = " / ".join(price_parts) if price_parts else "н/д"

        rooms_str = f"{self.rooms}-комнатная квартира" if self.rooms else "Квартира"

        address_str = self.address or "н/д"
        if self.yandex_maps_url:
            address_str = f'<a href="{self.yandex_maps_url}">{address_str}</a>'

        district_line = f"\n📍 {self.district}" if self.district else ""

        dishwasher = "н/д"
        if self.has_dishwasher is True:
            dishwasher = "✅ #посудомойка_есть"
        elif self.has_dishwasher is False:
            dishwasher = "❌ #посудомойки_нет"

        return (
            f"🏠 <b>{rooms_str}</b>\n"
            f"💰 Цена: {price_str}\n"
            f"📐 Площадь: {self.area or 'н/д'} м²\n"
            f"📍 Адрес: {address_str}{district_line}\n"
            f"🕐 Обновлено: {self.updated_at_formatted}\n"
            f"🍽 Посудомойка: {dishwasher}\n"
            f"\n🔗 {self.source.capitalize()}: <a href=\"{self.url}\">Открыть объявление</a>"
            f"\n#{self.source}_{self.external_id}"
            + ("\n\n@zoraleta @aksnickolas" if self.has_dishwasher else "")
        )
