from src.services.text_limits import shorten


def test_deja_igual_un_texto_corto():
    assert shorten("Los sábados fueron tu mejor día.", 1, 120) == "Los sábados fueron tu mejor día."


def test_se_queda_con_las_primeras_frases():
    text = "Ingresaste más que la semana pasada. Los martes siguen flojos. ¡Prueba una promo!"
    assert shorten(text, 2, 240) == "Ingresaste más que la semana pasada. Los martes siguen flojos."


def test_no_parte_importes_con_decimales():
    assert shorten("El ticket medio fue de 18.50€ por visita. Sube poco a poco.", 1, 120) == (
        "El ticket medio fue de 18.50€ por visita."
    )


def test_corta_por_palabra_si_pasa_de_los_caracteres():
    assert shorten("Ofrece el tinte como complemento del corte a todos tus clientes", 1, 30) == "Ofrece el tinte como…"


def test_texto_vacio():
    assert shorten("", 1, 120) == ""
