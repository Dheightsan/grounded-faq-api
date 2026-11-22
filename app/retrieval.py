"""BM25 over the fact list, pure Python."""

import math
import re
import unicodedata
from collections import Counter

from app.store import Fact

STOPWORDS = {
    "a", "al", "algo", "con", "cual", "cuales", "cuanto", "cuantos", "de", "del", "el",
    "ella", "en", "es", "esta", "estan", "este", "hay", "la", "las", "lo", "los", "me",
    "mi", "para", "por", "que", "se", "si", "son", "su", "sus", "tu", "un", "una", "y",
    "yo", "como", "donde", "cuando", "hasta", "q", "o", "le", "les", "nos", "te",
    "mis", "no", "ya", "hola", "buena", "bueno", "buenos", "dia", "tarde", "noche",
    "quiero", "queria", "quisiera", "saber", "puedo", "puede", "pueden", "tiene",
    "tienen", "tengo", "necesito", "porfa", "favor", "gracia", "oye", "usted",
    "ustedes", "tambien", "muy",
}  # fmt: skip

MIN_SCORE = 1.0
# share of the question's weight that the best fact has to cover
MIN_COVERAGE = 0.5


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def near(a: str, b: str) -> bool:
    """True if a and b are one edit apart (a typo)."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in re.findall(r"[a-z0-9]+", normalize(text)):
        if word in STOPWORDS:
            continue
        # crude plural handling so "clavos" matches "clavo"
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        tokens.append(word)
    return tokens


class Retriever:
    def __init__(self, facts: list[Fact], k1: float = 1.5, b: float = 0.75):
        self.facts = facts
        self.k1 = k1
        self.b = b
        self.docs = [Counter(tokenize(f"{f.topic} {f.text} {f.keywords}")) for f in facts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.df: Counter[str] = Counter()
        for doc in self.docs:
            self.df.update(doc.keys())

    def _idf(self, term: str) -> float:
        n = len(self.docs)
        df = self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _resolve(self, term: str) -> str | None:
        """Map a typo to a known word. None means it was a misspelled stopword."""
        if term in self.df or len(term) < 5:
            return term
        known = [w for w in self.df if near(term, w)]
        if known:
            return max(known, key=lambda w: self.df[w])
        if any(near(term, w) for w in STOPWORDS if len(w) >= 4):
            return None
        return term

    def _coverage(self, terms: list[str], i: int) -> float:
        total = sum(self._idf(t) for t in terms)
        found = sum(self._idf(t) for t in terms if t in self.docs[i])
        return found / total if total else 0.0

    def _score(self, terms: list[str], i: int) -> float:
        doc, length = self.docs[i], self.lengths[i]
        score = 0.0
        for term in terms:
            tf = doc.get(term, 0)
            if not tf:
                continue
            norm = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
            score += self._idf(term) * tf * (self.k1 + 1) / norm
        return score

    def search(self, query: str, limit: int = 3) -> list[tuple[Fact, float]]:
        """Top facts for the query, or [] if the best one is a weak match.

        Weak means a score under MIN_SCORE, or a best fact that leaves out
        words that carry most of the question (a service the shop does not
        have, a phone number it never gave).
        """
        terms = [t for t in map(self._resolve, tokenize(query)) if t]
        if not terms or not self.facts:
            return []
        scored = [(i, self._score(terms, i)) for i in range(len(self.facts))]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        if not scored or scored[0][1] < MIN_SCORE:
            return []
        if self._coverage(terms, scored[0][0]) < MIN_COVERAGE:
            return []
        return [(self.facts[i], score) for i, score in scored[:limit]]

    def by_topic(self, topic: str | None) -> list[Fact]:
        if not topic:
            return list(self.facts)
        wanted = normalize(topic)
        return [f for f in self.facts if normalize(f.topic) == wanted]
