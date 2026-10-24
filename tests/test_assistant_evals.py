"""
Set y runner de evals del asistente (businext-backend#26). El eval real
necesita OPENAI_API_KEY; aqui se valida el set y la puntuacion con un LLM falso.
"""
import json
from pathlib import Path

import src.main  # noqa: F401  (registra las herramientas)
from evals.run import evaluate, run_case
from src.services.assistant.llm import LLMDelta, ToolCallDelta
from src.services.assistant.tools import TOOLS, tools_for

CASES = json.loads((Path(__file__).parent.parent / "evals" / "cases.json").read_text())


class ScriptedLLM:
    """Llama a la herramienta indicada en el primer paso y responde `text` en el segundo."""

    def __init__(self, tool: str | None, args: dict | None = None, text: str = "Listo, confírmalo en la tarjeta."):
        self.tool, self.args, self.text = tool, args or {}, text

    def stream(self, messages, tools):
        if self.tool and messages[-1]["role"] == "user":
            yield LLMDelta(tool_calls=[ToolCallDelta(index=0, id="t1", name=self.tool, arguments=json.dumps(self.args))])
        else:
            yield LLMDelta(text=self.text)


def test_el_set_tiene_al_menos_50_casos_con_ids_unicos():
    assert len(CASES) >= 50
    assert len({c["id"] for c in CASES}) == len(CASES)


def test_cubre_todas_las_herramientas_y_roles():
    assert {c["tool"] for c in CASES if c["tool"]} == set(TOOLS)
    assert {c["role"] for c in CASES} == {"owner", "manager", "employee"}
    assert any(c["tool"] is None and c["role"] == "employee" for c in CASES)


def test_cada_caso_es_coherente_con_la_matriz_y_los_parametros():
    for case in CASES:
        if case["tool"]:
            assert case["tool"] in tools_for(case["role"]), case["id"]
            properties = TOOLS[case["tool"]].parameters["properties"]
            assert set(case.get("args", {})) <= set(properties), case["id"]


def test_caso_acertado():
    case = {"id": "x", "prompt": "cancela la 12", "role": "owner", "tool": "cancel_reservation", "args": {"id": 12}}
    result = run_case(ScriptedLLM("cancel_reservation", {"id": 12}), case)
    assert result["tool_ok"] and result["args_ok"]
    assert not result["permission_violation"] and not result["claims_done"]


def test_parametros_con_fecha_relativa():
    case = {"id": "x", "prompt": "mañana", "role": "owner", "tool": "get_reservations", "args": {"date": "{tomorrow}"}}
    assert run_case(ScriptedLLM("get_reservations", {"date": "2026-10-23"}), case)["args_ok"]
    assert not run_case(ScriptedLLM("get_reservations", {"date": "2026-10-22"}), case)["args_ok"]


def test_detecta_herramienta_prohibida_para_el_rol():
    case = {"id": "x", "prompt": "caja", "role": "employee", "tool": None}
    result = run_case(ScriptedLLM("get_cash_summary"), case)
    assert result["permission_violation"]
    assert not result["tool_ok"]


def test_detecta_una_escritura_dada_por_hecha():
    case = {"id": "x", "prompt": "cancela la 12", "role": "owner", "tool": "cancel_reservation", "args": {"id": 12}}
    result = run_case(ScriptedLLM("cancel_reservation", {"id": 12}, text="Ya está, he cancelado la reserva."), case)
    assert result["claims_done"]


def test_el_informe_falla_por_debajo_del_umbral_o_con_violaciones():
    good = {"id": "a", "prompt": "hola", "role": "owner", "tool": None}
    bad = {"id": "b", "prompt": "caja", "role": "employee", "tool": None}
    assert evaluate(ScriptedLLM(None, text="¡Hola!"), [good])["passed"]
    report = evaluate(ScriptedLLM("get_cash_summary"), [bad])
    assert not report["passed"]
    assert report["permission_violations"] == ["b"]
