from app.retrieval import Retriever, normalize, tokenize
from app.store import load_facts


def make() -> Retriever:
    return Retriever(load_facts())


def test_normalize_strips_accents():
    assert normalize("Ñuñoa Látex") == "nunoa latex"


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("¿Cuánto cuestan los clavos?") == ["cuestan", "clavo"]


def test_price_question_ranks_price_fact_first():
    hits = make().search("cuanto cuesta el taladro")
    assert hits[0][0].id == "precio-taladro"


def test_schedule_question_ranks_schedule_fact():
    hits = make().search("a que hora abren el sabado")
    assert hits[0][0].id == "horario-2"


def test_delivery_by_commune():
    hits = make().search("despachan a Maipú?")
    assert hits[0][0].id == "despacho-2"


def test_accents_do_not_matter():
    a = make().search("garantia de las herramientas electricas")
    b = make().search("garantía de las herramientas eléctricas")
    assert a[0][0].id == b[0][0].id == "garantia-1"


def test_unknown_question_returns_nothing():
    assert make().search("cual es la capital de Francia") == []


def test_empty_question_returns_nothing():
    assert make().search("   ") == []


def test_by_topic():
    r = make()
    assert {f.topic for f in r.by_topic("Precios")} == {"precios"}
    assert len(r.by_topic("precios")) == 8
    assert r.by_topic("nada") == []
