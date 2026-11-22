"""Opening hours, services and free slots for one business."""

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class Service(BaseModel):
    id: str
    name: str
    minutes: int
    price: int


class Agenda(BaseModel):
    business: str
    timezone: str
    slot_minutes: int = 30
    min_notice_minutes: int = 60
    days_ahead: int = 7
    hours: dict[str, tuple[str, str]]
    services: list[Service]

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def opening(self, day: date) -> tuple[datetime, datetime] | None:
        span = self.hours.get(WEEKDAYS[day.weekday()])
        if not span:
            return None
        start, end = (time.fromisoformat(t) for t in span)
        return datetime.combine(day, start), datetime.combine(day, end)

    def free_slots(
        self,
        day: date,
        service: Service,
        busy: list[tuple[datetime, datetime]],
        now: datetime,
    ) -> list[datetime]:
        """Start times on `day` where `service` fits, skipping busy ranges.

        All datetimes are naive and in the business's local time.
        """
        opening = self.opening(day)
        if not opening:
            return []
        open_at, close_at = opening
        earliest = now + timedelta(minutes=self.min_notice_minutes)
        length = timedelta(minutes=service.minutes)
        step = timedelta(minutes=self.slot_minutes)

        slots = []
        start = open_at
        while start + length <= close_at:
            end = start + length
            if start >= earliest and not any(b0 < end and b1 > start for b0, b1 in busy):
                slots.append(start)
            start += step
        return slots


def load_agenda(path: str | Path) -> Agenda:
    with Path(path).open(encoding="utf-8") as f:
        return Agenda(**json.load(f))


def day_label(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day:%d-%m}"


def price_label(amount: int) -> str:
    return "$" + f"{amount:,}".replace(",", ".")
