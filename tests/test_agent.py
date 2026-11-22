from datetime import datetime, timedelta

import pytest

from app.guard import FALLBACK
from tests.conftest import book, say

ANA = "56911111111"
LUIS = "56922222222"


def test_greeting_shows_menu(agent):
    text = say(agent, ANA, "Hola")
    assert "1. Reservar hora" in text
    assert "BORRAR MIS DATOS" in text


def test_full_booking_flow(agent):
    assert "¿Aceptas?" in say(agent, ANA, "hola, quiero reservar una hora")
    assert say(agent, ANA, "Sí") == "¿A nombre de quién dejo la hora?"
    services = say(agent, ANA, "Ana Pérez")
    assert services.startswith("Gracias, Ana.")
    assert "1. Corte de pelo ($12.000, 30 min)" in services
    days = say(agent, ANA, "1")
    assert "1. martes 24-11" in days  # Monday is closed
    slots = say(agent, ANA, "1")
    assert "1. 10:00" in slots and "2. 10:30" in slots
    confirm = say(agent, ANA, "2")
    assert confirm == (
        "Confirmo: Corte de pelo, martes 24-11 a las 10:30, a nombre de Ana Pérez. "
        "¿Está bien? (sí / no)"
    )
    done = say(agent, ANA, "si, dale")
    assert "quedó reservada" in done and "R-1" in done
    assert agent.db.customer_name(ANA) == "Ana Pérez"


def test_taken_slot_is_not_offered_again(agent):
    book(agent, ANA, slot="1")  # Tuesday 10:00
    slots = say(agent, LUIS, "reservar", "si", "Luis", "1", "1")
    assert "10:00" not in slots
    assert "1. 10:30" in slots


def test_slot_taken_while_confirming(agent):
    say(agent, ANA, "reservar", "si", "Ana", "1", "1", "1")  # Ana waits at the confirmation
    book(agent, LUIS, slot="1", name="Luis")  # Luis books Tuesday 10:00 first
    text = say(agent, ANA, "si")
    assert text.startswith("Esa hora se acaba de ocupar.")
    assert "10:00" not in text


def test_no_consent_saves_nothing(agent):
    say(agent, ANA, "reservar")
    assert "no guardé nada" in say(agent, ANA, "no")
    assert agent.db.customer_name(ANA) is None


def test_bad_name_is_asked_again(agent):
    say(agent, ANA, "reservar", "si")
    assert "solo tu nombre" in say(agent, ANA, "123")
    assert "¿Qué servicio quieres?" in say(agent, ANA, "María José O'Ryan")


def test_returning_customer_skips_consent(agent):
    book(agent, ANA)
    assert "¿Qué servicio quieres?" in say(agent, ANA, "agendar")


def test_list_and_cancel(agent):
    book(agent, ANA, slot="3")  # Tuesday 11:00
    listed = say(agent, ANA, "2")
    assert "Corte de pelo, martes 24-11 a las 11:00 (R-1)" in listed
    assert "¿Cuál quieres cancelar?" in say(agent, ANA, "cancelar")
    assert say(agent, ANA, "1") == "Listo, cancelé tu hora del martes 24-11 a las 11:00."
    assert say(agent, ANA, "2").startswith("No tienes horas")


def test_question_about_cancelling_is_faq_not_a_cancellation(agent):
    book(agent, ANA)
    text = say(agent, ANA, "¿cuál es la política de cancelación?")
    assert "2 horas de anticipación" in text


def test_erase_my_data(agent):
    book(agent, ANA)
    assert "¿Seguro?" in say(agent, ANA, "Borrar mis datos")
    assert say(agent, ANA, "sí") == "Listo, borré tus datos y tus horas."
    assert agent.db.customer_name(ANA) is None
    assert agent.db.upcoming(ANA, agent.now()) == []


def test_erase_can_be_declined(agent):
    book(agent, ANA)
    say(agent, ANA, "borrar mis datos")
    assert "No borré nada" in say(agent, ANA, "no")
    assert agent.db.customer_name(ANA) == "Ana Pérez"


def test_handoff_notice_once_then_quiet_until_menu(agent):
    reply = agent.handle(ANA, "quiero hablar con una persona")
    assert reply.handoff and "le avisé al equipo" in reply.text
    notice = agent.handle(ANA, "hola? alguien?")
    assert notice.handoff
    assert notice.text == "Te atiende una persona. Si quieres volver al menú, escribe MENU"
    for text in ["hola", "sigo esperando"]:
        quiet = agent.handle(ANA, text)
        assert quiet.handoff and quiet.text == ""
    assert "1. Reservar hora" in say(agent, ANA, "menu")


def test_handoff_expires_after_12_hours(agent, clock):
    agent.handle(ANA, "4")
    clock.set(2026, 11, 23, 20, 0)
    assert agent.handle(ANA, "sigo esperando").handoff
    clock.set(2026, 11, 23, 21, 30)
    reply = agent.handle(ANA, "horario")
    assert not reply.handoff and reply.text


@pytest.mark.parametrize(
    "text", ["cuanto cuesta para una persona", "somos tres personas", "hay alguien en la barberia"]
)
def test_loose_words_do_not_trigger_handoff(agent, text):
    assert not agent.handle(ANA, text).handoff


@pytest.mark.parametrize("text", ["humano por favor", "quiero un ejecutivo", "Hablar con alguien"])
def test_clear_phrases_trigger_handoff(agent, text):
    assert agent.handle(ANA, text).handoff


def test_ya_no_does_not_confirm(agent):
    say(agent, ANA, "reservar", "si", "Ana", "1", "1", "1")
    assert say(agent, ANA, "ya no") == "Responde sí o no, por favor."
    assert agent.db.upcoming(ANA, agent.now()) == []
    assert "quedó reservada" in say(agent, ANA, "¡Sí!")


def test_ambiguous_answer_does_not_erase(agent):
    book(agent, ANA)
    say(agent, ANA, "borrar mis datos")
    assert say(agent, ANA, "ya no quiero que lo borres") == "Responde sí o no, por favor."
    assert agent.db.customer_name(ANA) == "Ana Pérez"
    assert "No borré nada" in say(agent, ANA, "no, gracias")
    assert agent.db.customer_name(ANA) == "Ana Pérez"


def test_stale_confirmation_explains(agent, clock):
    say(agent, ANA, "reservar", "si", "Ana", "1", "1", "1")
    clock.set(2026, 11, 23, 9, 45)
    text = say(agent, ANA, "si")
    assert text.startswith("Esa conversación ya venció.") and "1" in text
    assert agent.db.upcoming(ANA, agent.now()) == []


def test_two_active_bookings_at_most(agent):
    book(agent, ANA, slot="1")
    book(agent, ANA, slot="3")
    text = say(agent, ANA, "reservar")
    assert text.startswith("Ya tienes 2 horas reservadas")
    say(agent, ANA, "cancelar", "1")
    assert "¿Qué servicio quieres?" in say(agent, ANA, "reservar")


def test_cap_is_checked_again_at_confirmation(agent):
    say(agent, ANA, "reservar", "si", "Ana", "1", "1", "1")  # waits at the confirmation
    for hour in (14, 15):
        start = datetime(2026, 11, 24, hour, 0)
        agent.db.add_booking(ANA, "corte", start, start + timedelta(minutes=30), agent.now())
    assert say(agent, ANA, "si").startswith("Ya tienes 2 horas reservadas")


def test_processed_ids_are_purged_after_a_week(agent):
    day = datetime(2026, 11, 1, 9, 0)
    assert agent.db.first_time("old", day)
    assert agent.db.first_time("new", day + timedelta(days=8))
    assert agent.db.first_time("old", day + timedelta(days=8))  # purged, seen as new again
    assert not agent.db.first_time("new", day + timedelta(days=8))


def test_later_slots_page(agent):
    first = say(agent, ANA, "reservar", "si", "Ana", "1", "1")
    assert "10. 14:30" in first and "11. Más tarde" in first
    later = say(agent, ANA, "11")
    assert "1. 15:00" in later and "8. 18:30" in later
    assert "Más tarde" not in later
    assert "a las 18:30" in say(agent, ANA, "8")


def test_faq_answers_from_facts(agent):
    assert "$12.000" in say(agent, ANA, "¿Cuánto cuesta el corte de pelo?")
    assert "Ñuñoa" in say(agent, ANA, "¿dónde queda la barbería?")


def test_faq_without_fact_says_so(agent):
    text = say(agent, ANA, "¿tienen wifi gratis?")
    assert text.startswith(FALLBACK)


def test_invalid_option(agent):
    say(agent, ANA, "reservar", "si", "Ana")
    assert say(agent, ANA, "7").startswith("No entendí.")


def test_stale_flow_is_dropped(agent, clock):
    say(agent, ANA, "reservar", "si", "Ana", "1")  # waiting for the day
    clock.set(2026, 11, 23, 9, 45)
    assert say(agent, ANA, "2").startswith("No tienes horas")  # "2" is the main menu again


def test_minimum_notice_on_the_same_day(agent, clock):
    clock.set(2026, 11, 24, 10, 20)  # Tuesday, already open
    slots = say(agent, ANA, "reservar", "si", "Ana", "1", "1")
    assert "martes 24-11" in slots
    assert "1. 11:30" in slots
