import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "data" / "user_config.json"


@dataclass
class Filters:
    rooms: list[int] = field(default_factory=lambda: [1, 2, 3])
    price_min_usd: int = 0
    price_max_usd: int | None = None
    area_min: int = 0
    area_max: int | None = None
    only_owner: bool = False
    scan_interval_minutes: int = 30

    def matches(self, apt) -> bool:
        if self.rooms and apt.rooms and apt.rooms not in self.rooms:
            return False
        if apt.price_usd is not None:
            if apt.price_usd < self.price_min_usd:
                return False
            if self.price_max_usd is not None and apt.price_usd > self.price_max_usd:
                return False
        if apt.area is not None:
            if apt.area < self.area_min:
                return False
            if self.area_max is not None and apt.area > self.area_max:
                return False
        if self.only_owner and apt.is_owner is False:
            return False
        if apt.has_pet_restriction:
            return False
        return True


def load_filters() -> Filters:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return Filters(**data)
    return Filters()


def save_filters(filters: Filters):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(filters), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
