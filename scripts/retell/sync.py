"""
Crea o actualiza en Retell el agente plantilla del agente de voz
(businext-sys/businext#117) a partir de `agent_template.json`, y conecta
numeros al webhook de llamada entrante.

    # Crear (la primera vez) o actualizar el agente; imprime su agent_id
    python scripts/retell/sync.py agent --base-url https://businext-backend.onrender.com [--voice-id <id>]

    # Conectar un numero de Retell al webhook (sin agente fijo: si el webhook
    # falla, Retell cuelga en vez de atender sin negocio)
    python scripts/retell/sync.py bind-number +34910000000 --base-url https://businext-backend.onrender.com

Necesita RETELL_API_KEY. Con RETELL_AGENT_ID actualiza ese agente (y su LLM);
sin ella crea uno nuevo, cuyo id hay que poner en RETELL_AGENT_ID en Render.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

API = "https://api.retellai.com"
TEMPLATE = Path(__file__).with_name("agent_template.json")


def _client() -> httpx.Client:
    api_key = os.getenv("RETELL_API_KEY")
    if not api_key:
        sys.exit("Falta RETELL_API_KEY")
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)


def _call(client: httpx.Client, method: str, path: str, body: dict | None = None) -> dict:
    response = client.request(method, path, json=body)
    if response.status_code >= 400:
        sys.exit(f"{method} {path} -> {response.status_code}: {response.text}")
    return response.json() if response.content else {}


def build_llm(template: dict, base_url: str) -> dict:
    llm = json.loads(json.dumps(template["llm"]))
    for tool in llm["general_tools"]:
        if tool["type"] == "custom":
            tool["url"] = f"{base_url.rstrip('/')}/retell/functions/{tool['name']}"
            tool["method"] = "POST"
    return llm


def sync_agent(base_url: str, voice_id: str | None) -> None:
    template = json.loads(TEMPLATE.read_text())
    llm = build_llm(template, base_url)
    agent = {k: v for k, v in template["agent"].items() if v is not None}
    # Eventos de la llamada (inicio, fin, analisis) -> registro de llamadas.
    agent["webhook_url"] = f"{base_url.rstrip('/')}/retell/webhook"
    if voice_id:
        agent["voice_id"] = voice_id

    with _client() as client:
        agent_id = os.getenv("RETELL_AGENT_ID")
        if agent_id:
            current = _call(client, "GET", f"/get-agent/{agent_id}")
            llm_id = current["response_engine"]["llm_id"]
            _call(client, "PATCH", f"/update-retell-llm/{llm_id}", llm)
            _call(client, "PATCH", f"/update-agent/{agent_id}", agent)
            print(f"Actualizado agent_id={agent_id} llm_id={llm_id}")
            return

        if "voice_id" not in agent:
            sys.exit("Para crear el agente hace falta --voice-id (una voz en español del dashboard de Retell)")
        llm_id = _call(client, "POST", "/create-retell-llm", llm)["llm_id"]
        created = _call(client, "POST", "/create-agent", {
            **agent, "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        })
        print(f"Creado agent_id={created['agent_id']} llm_id={llm_id}")
        print("Pon RETELL_AGENT_ID con ese agent_id en Render.")


def bind_number(phone_number: str, base_url: str) -> None:
    with _client() as client:
        _call(client, "PATCH", f"/update-phone-number/{phone_number}", {
            "inbound_webhook_url": f"{base_url.rstrip('/')}/retell/inbound",
            "inbound_agents": [],
        })
    print(f"{phone_number} -> {base_url.rstrip('/')}/retell/inbound")
    print("Falta asignarlo a su negocio en la tabla voicephonenumber (docs/sql/117-voice-agent.sql).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    agent = commands.add_parser("agent", help="Crear o actualizar el agente plantilla")
    agent.add_argument("--base-url", required=True)
    agent.add_argument("--voice-id")
    number = commands.add_parser("bind-number", help="Conectar un numero al webhook de entrada")
    number.add_argument("phone_number")
    number.add_argument("--base-url", required=True)
    args = parser.parse_args()

    if args.command == "agent":
        sync_agent(args.base_url, args.voice_id)
    else:
        bind_number(args.phone_number, args.base_url)


if __name__ == "__main__":
    main()
