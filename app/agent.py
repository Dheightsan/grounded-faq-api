"""WhatsApp-style conversation: bookings, customer record and grounded FAQ.

The flow is a small state machine kept in the database per phone number, so
it works the same behind a webhook, n8n or the local CLI. Free text that is
not a command goes to the grounded FAQ, which never answers outside the facts.
"""

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.agenda import Agenda, day_label, price_label
from app.db import AgentDB
from app.guard import FALLBACK
from app.retrieval import Retriever, normalize
from app.service import Model, answer_question

STALE_AFTER = timedelta(minutes=30)
MAX_SLOTS = 10
MAX_DAYS = 6

YES = {"si", "s", "ok", "dale", "ya", "bueno", "claro", "confirmo", "acepto", "listo"}
NO = {"no", "n", "nop", "nopo", "mejor no"}
NAME = re.compile(r"[^\W\d_]+(?:[ '.-]+[^\W\d_]+)*\.?")
MENU_WORDS = {"menu", "inicio", "0", "hola", "buenas", "buenos dias", "buenas tardes", "volver"}
EXIT_HANDOFF = {"menu", "inicio", "volver"}
ERASE = "borrar mis datos"


class Reply(BaseModel):
    text: str
    handoff: bool = False


def _clean(text: str) -> str:
    text = normalize(text.strip())
    text = re.sub(r"[¿?¡!.,;:]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_word(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}\b", text) for w in words)


def _yes(text: str) -> bool:
    return text in YES or (text.split() or [""])[0] in YES


def _no(text: str) -> bool:
    return text in NO or (text.split() or [""])[0] in NO


def _pick(text: str, count: int) -> int | None:
    """1-based option number typed by the user, as a 0-based index."""
    if text.isdigit() and 1 <= int(text) <= count:
        return int(text) - 1
    return None


class Agent:
    def __init__(
        self,
        agenda: Agenda,
        db: AgentDB,
        retriever: Retriever,
        model: Model | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.agenda = agenda
        self.db = db
        self.retriever = retriever
        self.model = model
        self.tz = ZoneInfo(agenda.timezone)
        self.clock = clock or (lambda: datetime.now(self.tz))

    def now(self) -> datetime:
        """Current business-local time, naive, to the minute."""
        return self.clock().astimezone(self.tz).replace(tzinfo=None, second=0, microsecond=0)

    # entry point

    def handle(self, phone: str, text: str) -> Reply:
        now = self.now()
        t = _clean(text)
        saved = self.db.get_state(phone)
        state, data = ("idle", {})
        if saved:
            state, data, updated = saved
            if state != "handoff" and now - updated > STALE_AFTER:
                state, data = "idle", {}

        if t == ERASE:
            self.db.set_state(phone, "erase", {}, now)
            return Reply(
                text="Esto borra tu nombre, tus horas y esta conversación. ¿Seguro? (sí / no)"
            )
        # during a handoff a plain "hola" is for the person, not for the bot
        if t in (EXIT_HANDOFF if state == "handoff" else MENU_WORDS):
            self.db.clear_state(phone)
            return Reply(text=self.menu())

        handler = getattr(self, f"_on_{state}", self._on_idle)
        return handler(phone, t, text.strip(), data, now)

    def menu(self) -> str:
        return (
            f"Hola, soy el asistente de {self.agenda.business}. Escribe el número:\n"
            "1. Reservar hora\n"
            "2. Ver mis horas\n"
            "3. Cancelar una hora\n"
            "4. Hablar con una persona\n"
            "También puedes preguntarme por horarios, precios o dirección.\n"
            "Para borrar tus datos escribe BORRAR MIS DATOS."
        )

    # states

    def _on_idle(self, phone, t, raw, data, now) -> Reply:
        if t == "1" or _has_word(t, "reservar", "reserva", "agendar", "agenda", "agendame"):
            return self._start_booking(phone, now)
        if t == "2" or _has_word(t, "mis horas", "mis reservas", "mi hora", "mi reserva"):
            return Reply(text=self._list_upcoming(phone, now))
        if t == "3" or (_has_word(t, "cancelar", "anular") and len(t.split()) <= 5):
            return self._start_cancel(phone, now)
        if t == "4" or _has_word(t, "humano", "persona", "ejecutivo", "ejecutiva", "alguien"):
            self.db.set_state(phone, "handoff", {}, now)
            return Reply(
                text="Listo, le avisé al equipo. Te escriben por aquí en horario de atención. "
                "Si quieres volver al asistente escribe MENU.",
                handoff=True,
            )
        return self._faq(raw)

    def _on_handoff(self, phone, t, raw, data, now) -> Reply:
        # a person is handling this chat; the bot stays quiet until MENU
        return Reply(text="", handoff=True)

    def _on_consent(self, phone, t, raw, data, now) -> Reply:
        if _yes(t):
            self.db.set_state(phone, "name", {}, now)
            return Reply(text="¿A nombre de quién dejo la hora?")
        if _no(t):
            self.db.clear_state(phone)
            return Reply(text="Sin problema, no guardé nada. Escribe MENU si necesitas algo más.")
        return Reply(text="Responde sí o no, por favor.")

    def _on_name(self, phone, t, raw, data, now) -> Reply:
        name = re.sub(r"\s+", " ", raw)
        if not (2 <= len(name) <= 60) or not NAME.fullmatch(name):
            return Reply(text="Escríbeme solo tu nombre, por ejemplo: Ana Pérez.")
        self.db.save_customer(phone, name, now)
        return self._ask_service(phone, now, prefix=f"Gracias, {name.split()[0]}. ")

    def _on_service(self, phone, t, raw, data, now) -> Reply:
        i = _pick(t, len(self.agenda.services))
        if i is None:
            return self._not_understood()
        return self._ask_day(phone, self.agenda.services[i].id, now)

    def _on_day(self, phone, t, raw, data, now) -> Reply:
        days = data["days"]
        i = _pick(t, len(days))
        if i is None:
            return self._not_understood()
        return self._ask_slot(phone, data["service"], days[i], now)

    def _on_slot(self, phone, t, raw, data, now) -> Reply:
        slots = data["slots"]
        later = data.get("later")
        i = _pick(t, len(slots) + (1 if later else 0))
        if i is None:
            return self._not_understood()
        if i == len(slots):
            return self._ask_slot(phone, data["service"], data["day"], now, offset=later)
        start = datetime.fromisoformat(slots[i])
        service = self.agenda.service(data["service"])
        name = self.db.customer_name(phone)
        self.db.set_state(
            phone, "confirm", {"service": service.id, "start": start.isoformat()}, now
        )
        return Reply(
            text=f"Confirmo: {service.name}, {day_label(start.date())} a las {start:%H:%M}, "
            f"a nombre de {name}. ¿Está bien? (sí / no)"
        )

    def _on_confirm(self, phone, t, raw, data, now) -> Reply:
        if _no(t):
            self.db.clear_state(phone)
            return Reply(text="No reservé nada. Escribe 1 para elegir otra hora o MENU.")
        if not _yes(t):
            return Reply(text="Responde sí o no, por favor.")
        service = self.agenda.service(data["service"])
        start = datetime.fromisoformat(data["start"])
        end = start + timedelta(minutes=service.minutes)
        if start < now:
            return self._ask_slot(
                phone, service.id, start.date().isoformat(), now, prefix="Esa hora ya pasó. "
            )
        booking_id = self.db.add_booking(phone, service.id, start, end, now)
        if booking_id is None:
            return self._ask_slot(
                phone,
                service.id,
                start.date().isoformat(),
                now,
                prefix="Esa hora se acaba de ocupar. ",
            )
        self.db.clear_state(phone)
        return Reply(
            text=f"Listo, quedó reservada: {service.name}, {day_label(start.date())} a las "
            f"{start:%H:%M}. Tu código es R-{booking_id}. Para cancelar escribe CANCELAR."
        )

    def _on_cancel(self, phone, t, raw, data, now) -> Reply:
        ids = data["ids"]
        i = _pick(t, len(ids))
        if i is None:
            return self._not_understood()
        upcoming = {b.id: b for b in self.db.upcoming(phone, now)}
        booking = upcoming.get(ids[i])
        self.db.clear_state(phone)
        if not booking or not self.db.cancel_booking(phone, booking.id):
            return Reply(text="Esa hora ya no estaba activa. Escribe 2 para ver tus horas.")
        return Reply(
            text=f"Listo, cancelé tu hora del {day_label(booking.start.date())} "
            f"a las {booking.start:%H:%M}."
        )

    def _on_erase(self, phone, t, raw, data, now) -> Reply:
        if _yes(t):
            self.db.erase_customer(phone)
            return Reply(text="Listo, borré tus datos y tus horas.")
        self.db.clear_state(phone)
        return Reply(text="No borré nada. Escribe MENU si necesitas algo más.")

    # steps

    def _start_booking(self, phone, now) -> Reply:
        if self.db.customer_name(phone):
            return self._ask_service(phone, now)
        self.db.set_state(phone, "consent", {}, now)
        return Reply(
            text="Para reservar necesito tu nombre. Lo guardo junto a este número solo para "
            "gestionar tus horas, y lo puedes borrar cuando quieras escribiendo BORRAR MIS DATOS. "
            "¿Aceptas? (sí / no)"
        )

    def _ask_service(self, phone, now, prefix: str = "") -> Reply:
        self.db.set_state(phone, "service", {}, now)
        lines = [
            f"{n}. {s.name} ({price_label(s.price)}, {s.minutes} min)"
            for n, s in enumerate(self.agenda.services, 1)
        ]
        return Reply(text=prefix + "¿Qué servicio quieres?\n" + "\n".join(lines))

    def _slots(self, service_id: str, day_iso: str, now: datetime) -> list[datetime]:
        service = self.agenda.service(service_id)
        day = datetime.fromisoformat(day_iso).date()
        opening = self.agenda.opening(day)
        if not opening:
            return []
        busy = self.db.busy(*opening)
        return self.agenda.free_slots(day, service, busy, now)

    def _ask_day(self, phone, service_id, now) -> Reply:
        days = []
        for offset in range(self.agenda.days_ahead + 1):
            day = (now + timedelta(days=offset)).date().isoformat()
            if self._slots(service_id, day, now):
                days.append(day)
            if len(days) == MAX_DAYS:
                break
        if not days:
            self.db.clear_state(phone)
            return Reply(
                text="No quedan horas libres esta semana. Escribe 4 para hablar con una persona."
            )
        self.db.set_state(phone, "day", {"service": service_id, "days": days}, now)
        lines = [
            f"{n}. {day_label(datetime.fromisoformat(d).date())}" for n, d in enumerate(days, 1)
        ]
        return Reply(text="¿Qué día?\n" + "\n".join(lines))

    def _ask_slot(
        self, phone, service_id, day_iso, now, prefix: str = "", offset: int = 0
    ) -> Reply:
        free = self._slots(service_id, day_iso, now)
        if not free:
            return self._ask_day(phone, service_id, now)
        if offset >= len(free):  # slots were taken meanwhile
            offset = 0
        slots = free[offset : offset + MAX_SLOTS]
        later = offset + MAX_SLOTS if len(free) > offset + MAX_SLOTS else None
        data = {
            "service": service_id,
            "day": day_iso,
            "slots": [s.isoformat() for s in slots],
            "later": later,
        }
        self.db.set_state(phone, "slot", data, now)
        lines = [f"{n}. {s:%H:%M}" for n, s in enumerate(slots, 1)]
        if later:
            lines.append(f"{len(slots) + 1}. Más tarde")
        label = day_label(slots[0].date())
        return Reply(text=f"{prefix}Horas libres el {label}:\n" + "\n".join(lines))

    def _list_upcoming(self, phone, now) -> str:
        upcoming = self.db.upcoming(phone, now)
        if not upcoming:
            return "No tienes horas reservadas. Escribe 1 para reservar."
        lines = [self._booking_line(b) for b in upcoming]
        return "Tus próximas horas:\n" + "\n".join(f"- {line}" for line in lines)

    def _start_cancel(self, phone, now) -> Reply:
        upcoming = self.db.upcoming(phone, now)
        if not upcoming:
            return Reply(text="No tienes horas reservadas.")
        self.db.set_state(phone, "cancel", {"ids": [b.id for b in upcoming]}, now)
        lines = [f"{n}. {self._booking_line(b)}" for n, b in enumerate(upcoming, 1)]
        return Reply(text="¿Cuál quieres cancelar?\n" + "\n".join(lines))

    def _booking_line(self, b) -> str:
        service = self.agenda.service(b.service_id)
        name = service.name if service else b.service_id
        return f"{name}, {day_label(b.start.date())} a las {b.start:%H:%M} (R-{b.id})"

    def _faq(self, raw: str) -> Reply:
        answer = answer_question(raw, self.retriever, self.model)
        if answer.grounded:
            return Reply(text=answer.answer)
        return Reply(
            text=f"{FALLBACK} Escribe MENU para ver las opciones o 4 para hablar con una persona."
        )

    def _not_understood(self) -> Reply:
        return Reply(
            text="No entendí. Responde con el número de la opción, o escribe 0 para volver al menú."
        )
