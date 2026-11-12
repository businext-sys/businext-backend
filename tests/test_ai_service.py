import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services import ai_service

LONG = "Esta semana ingresaste bastante más que la anterior. " * 6


def openai_response(payload: dict):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


@pytest.fixture
def openai(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(ai_service, "client", fake)
    return fake


def weekly_summary() -> dict:
    return ai_service.generate_weekly_summary("Barbería Sol", "2-8 nov", [], [], [], [])


def test_el_resumen_semanal_cabe_en_un_vistazo(openai):
    openai.chat.completions.create.return_value = openai_response({
        "narrative": LONG,
        "client_narrative": LONG,
        "team_narrative": LONG,
        "opportunities": [{"text": LONG, "impact": "alto"}, LONG, LONG, LONG],
    })
    summary = weekly_summary()
    kpis = json.loads(summary["kpis"])

    assert summary["narrative"] == "Esta semana ingresaste bastante más que la anterior."
    assert summary["client_narrative"].count(".") == 2
    assert kpis["team_narrative"].count(".") == 2
    assert kpis["opportunities"] == [
        {"text": "Esta semana ingresaste bastante más que la anterior.", "impact": "alto"},
        {"text": "Esta semana ingresaste bastante más que la anterior.", "impact": "medio"},
        {"text": "Esta semana ingresaste bastante más que la anterior.", "impact": "medio"},
    ]


def test_el_resumen_semanal_mantiene_sus_campos(openai):
    openai.chat.completions.create.return_value = openai_response({"narrative": "Semana tranquila."})
    summary = weekly_summary()

    assert set(summary) == {"narrative", "kpis", "client_narrative"}
    assert summary["client_narrative"] is None
    assert json.loads(summary["kpis"])["opportunities"] == []


def test_el_resumen_de_llamada_son_dos_frases(openai):
    openai.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=LONG))]
    )
    summary = ai_service.generate_call_summary("Agent: Hola\nUser: Quiero cita")

    assert summary.count(".") == 2
    assert len(summary) <= ai_service.CALL_SUMMARY_MAX_CHARS


def test_el_resumen_de_llamada_sin_clave_falla(monkeypatch):
    monkeypatch.setattr(ai_service, "client", None)
    with pytest.raises(ai_service.AIServiceError):
        ai_service.generate_call_summary("Agent: Hola")
