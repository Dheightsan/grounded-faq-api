"""Block answers that carry numbers, times, URLs or emails not found in the facts."""

import re

FALLBACK = "No tengo ese dato."

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _digits(token: str) -> str:
    """12.990 and 12,990 and 12990 all compare equal."""
    return re.sub(r"[.,]", "", token)


def _urls(text: str) -> set[str]:
    return {u.rstrip(".,;:)!?\"'").lower() for u in URL.findall(text)}


def is_grounded(answer: str, fact_texts: list[str]) -> bool:
    source = " ".join(fact_texts)

    known_numbers = {_digits(n) for n in NUMBER.findall(source)}
    if any(_digits(n) not in known_numbers for n in NUMBER.findall(answer)):
        return False

    if any(t not in set(TIME.findall(source)) for t in TIME.findall(answer)):
        return False

    if not _urls(answer) <= _urls(source):
        return False

    known_emails = {e.lower() for e in EMAIL.findall(source)}
    return all(e.lower() in known_emails for e in EMAIL.findall(answer))
