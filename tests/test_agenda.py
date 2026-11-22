from datetime import date, datetime

from app.agenda import day_label, load_agenda, price_label
from tests.conftest import DATA

AGENDA = load_agenda(DATA / "agenda.json")
CORTE = AGENDA.service("corte")
CORTE_BARBA = AGENDA.service("corte-barba")
MONDAY_EARLY = datetime(2026, 11, 23, 8, 0)


def test_closed_on_monday():
    assert AGENDA.free_slots(date(2026, 11, 23), CORTE, [], MONDAY_EARLY) == []


def test_tuesday_slots_every_half_hour():
    slots = AGENDA.free_slots(date(2026, 11, 24), CORTE, [], MONDAY_EARLY)
    assert slots[0] == datetime(2026, 11, 24, 10, 0)
    assert slots[-1] == datetime(2026, 11, 24, 18, 30)
    assert len(slots) == 18


def test_long_service_must_end_before_closing():
    slots = AGENDA.free_slots(date(2026, 11, 28), CORTE_BARBA, [], MONDAY_EARLY)
    assert slots[-1] == datetime(2026, 11, 28, 14, 0)  # Saturday closes at 15:00


def test_busy_range_blocks_overlapping_starts():
    busy = [(datetime(2026, 11, 24, 10, 30), datetime(2026, 11, 24, 11, 0))]
    slots = AGENDA.free_slots(date(2026, 11, 24), CORTE_BARBA, busy, MONDAY_EARLY)
    assert datetime(2026, 11, 24, 10, 0) not in slots  # 10:00-11:00 would overlap
    assert datetime(2026, 11, 24, 10, 30) not in slots
    assert datetime(2026, 11, 24, 11, 0) in slots


def test_minimum_notice_today():
    now = datetime(2026, 11, 24, 10, 20)
    slots = AGENDA.free_slots(date(2026, 11, 24), CORTE, [], now)
    assert slots[0] == datetime(2026, 11, 24, 11, 30)


def test_labels():
    assert day_label(date(2026, 11, 25)) == "miércoles 25-11"
    assert price_label(16000) == "$16.000"
