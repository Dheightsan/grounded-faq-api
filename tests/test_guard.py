from app.guard import is_grounded

FACTS = [
    "El taladro percutor de 650 W cuesta $42.990.",
    "Atendemos de 9:00 a 19:00 horas. Más datos en https://elroble.example/contacto.",
]


def test_real_price_passes():
    assert is_grounded("El taladro cuesta $42.990.", FACTS)


def test_price_without_symbol_or_dots_passes():
    assert is_grounded("Cuesta 42990 pesos.", FACTS)


def test_invented_price_is_blocked():
    assert not is_grounded("El taladro cuesta $39.990.", FACTS)


def test_invented_time_is_blocked():
    assert not is_grounded("Abrimos de 9:00 a 20:00.", FACTS)


def test_real_time_passes():
    assert is_grounded("Abrimos de 9:00 a 19:00.", FACTS)


def test_real_url_passes_even_with_trailing_dot():
    assert is_grounded("Escribe en https://elroble.example/contacto.", FACTS)


def test_invented_url_is_blocked():
    assert not is_grounded("Mira https://tienda-falsa.example/oferta", FACTS)


def test_invented_email_is_blocked():
    assert not is_grounded("Escribe a ventas@elroble.example", FACTS)


def test_text_without_figures_passes():
    assert is_grounded("Sí, el taladro es percutor.", FACTS)
