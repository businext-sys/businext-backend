import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def generate_review_response(
    review_text: str,
    review_rating: int,
    business_name: str,
) -> str:
    """Generate an AI response draft for a Google Maps review.

    Returns the generated response text in Spanish.
    """
    if not client:
        raise AIServiceError("Servicio de IA no configurado. Falta la clave de API.")

    if review_rating >= 4:
        tone = "agradecido y personalizado"
    elif review_rating == 3:
        tone = "amable y constructivo"
    else:
        tone = "profesional, empático y orientado a resolver el problema"

    system_prompt = (
        "Eres un asistente que genera respuestas profesionales para reseñas de Google Maps "
        "en nombre de un negocio. Las respuestas deben ser en español, concisas (2-4 oraciones), "
        "y apropiadas para publicar directamente en Google Maps."
    )

    user_prompt = (
        f"Negocio: {business_name}\n"
        f"Calificación: {review_rating}/5 estrellas\n"
        f"Reseña: {review_text or '(Sin texto, solo calificación)'}\n\n"
        f"Genera una respuesta con tono {tone}. "
        "No uses emojis excesivos. Sé genuino y específico al contenido de la reseña."
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=300,
        temperature=0.7,
    )

    return response.choices[0].message.content or ""


def generate_business_summary(
    reviews: list[dict],
    business_name: str,
) -> dict:
    """Generate an AI summary analyzing all reviews for a business.

    Returns a dict with: overall_sentiment, positive_themes, negative_themes,
    recommendations, review_count_analyzed, generated_at.
    """
    if not client:
        raise AIServiceError("Servicio de IA no configurado. Falta la clave de API.")

    # Build a condensed review digest
    review_texts = []
    for r in reviews[:100]:  # Cap at 100 reviews to fit context
        rating = r.get("review_rating", "?")
        text = r.get("review_text", "(sin texto)")
        review_texts.append(f"- {rating}⭐: {text}")

    reviews_digest = "\n".join(review_texts)

    system_prompt = (
        "Eres un analista de negocios que genera resúmenes ejecutivos basados en reseñas "
        "de Google Maps. Responde SIEMPRE en formato JSON válido en español."
    )

    user_prompt = (
        f"Negocio: {business_name}\n"
        f"Total de reseñas analizadas: {len(reviews)}\n\n"
        f"Reseñas:\n{reviews_digest}\n\n"
        "Genera un análisis en formato JSON con exactamente estas claves:\n"
        '{\n'
        '  "overall_sentiment": "positivo" | "neutro" | "negativo",\n'
        '  "positive_themes": ["tema1", "tema2", "tema3"],\n'
        '  "negative_themes": ["tema1", "tema2", "tema3"],\n'
        '  "recommendations": ["recomendación1", "recomendación2", "recomendación3"]\n'
        '}\n'
        "Identifica los 3 temas positivos y negativos más mencionados. "
        "Las recomendaciones deben ser accionables y específicas."
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=500,
        temperature=0.5,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"
    result = json.loads(content)

    result["review_count_analyzed"] = len(reviews)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    return result


def generate_weekly_summary(
    business_name: str,
    week_label: str,
    finances_data: list[dict],
    reservations_data: list[dict],
    prev_finances_data: list[dict],
    prev_reservations_data: list[dict],
    prev_week_narrative: str | None = None,
) -> dict:
    """Generate an AI weekly business summary with KPIs and client narrative.

    Returns a dict with keys: narrative, kpis (JSON string), client_narrative.
    """
    if not client:
        raise AIServiceError("Servicio de IA no configurado. Falta la clave de API.")

    # ── Compute KPIs ────────────────────────────────────────────────
    def _compute_kpis(finances: list[dict], reservations: list[dict]) -> dict:
        income = sum(f["amount"] for f in finances if f.get("type") == "INCOME")
        total_res = len([r for r in reservations if r.get("status") == "COMPLETED"])
        product_sales = len([f for f in finances if f.get("type") == "INCOME" and f.get("reservation_id") is None])
        return {"income": income, "reservations": total_res, "product_sales": product_sales}

    current = _compute_kpis(finances_data, reservations_data)
    prev = _compute_kpis(prev_finances_data, prev_reservations_data)

    def _pct(cur: float, prv: float) -> float:
        if prv == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - prv) / prv * 100, 1)

    # ── Daily breakdown (Mon=0 .. Sun=6) ────────────────────────────
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    daily_income = [0.0] * 7
    daily_reservations = [0] * 7
    daily_products = [0] * 7

    for f in finances_data:
        if f.get("type") == "INCOME" and f.get("created_at"):
            try:
                dt = datetime.fromisoformat(f["created_at"])
                wd = dt.weekday()
                daily_income[wd] += f["amount"]
                if f.get("reservation_id") is None:
                    daily_products[wd] += 1
            except (ValueError, IndexError):
                pass

    for r in reservations_data:
        if r.get("status") == "COMPLETED" and r.get("reservation_start_date"):
            try:
                dt = datetime.fromisoformat(r["reservation_start_date"])
                daily_reservations[dt.weekday()] += 1
            except (ValueError, IndexError):
                pass

    # Round daily income
    daily_income = [round(v, 2) for v in daily_income]

    # Best/worst day by income
    day_income_map: dict[str, float] = {}
    for i, name in enumerate(day_names):
        if daily_income[i] > 0:
            day_income_map[name] = daily_income[i]

    best_day = max(day_income_map, key=day_income_map.get, default="N/A") if day_income_map else "N/A"  # type: ignore[arg-type]
    worst_day = min(day_income_map, key=day_income_map.get, default="N/A") if len(day_income_map) > 1 else "N/A"  # type: ignore[arg-type]

    # Top service
    service_count: dict[str, int] = {}
    for r in reservations_data:
        if r.get("status") == "COMPLETED" and r.get("service"):
            service_count[r["service"]] = service_count.get(r["service"], 0) + 1
    top_service = max(service_count, key=service_count.get, default="N/A") if service_count else "N/A"  # type: ignore[arg-type]

    kpis = {
        "total_income": round(current["income"], 2),
        "total_reservations": current["reservations"],
        "total_product_sales": current["product_sales"],
        "prev_week_income": round(prev["income"], 2),
        "prev_week_reservations": prev["reservations"],
        "prev_week_product_sales": prev["product_sales"],
        "income_change_pct": _pct(current["income"], prev["income"]),
        "reservations_change_pct": _pct(current["reservations"], prev["reservations"]),
        "product_sales_change_pct": _pct(current["product_sales"], prev["product_sales"]),
        "best_day": best_day,
        "worst_day": worst_day,
        "top_service": top_service,
        "daily_income": daily_income,
        "daily_reservations": daily_reservations,
        "daily_products": daily_products,
    }

    # ── Employee stats ──────────────────────────────────────────────
    employee_income: dict[str, float] = {}
    employee_reservations: dict[str, int] = {}
    employee_products: dict[str, int] = {}

    for f in finances_data:
        if f.get("type") != "INCOME":
            continue
        creator = f.get("creator", "Desconocido")
        employee_income[creator] = employee_income.get(creator, 0) + f["amount"]
        if f.get("reservation_id") is None:
            employee_products[creator] = employee_products.get(creator, 0) + 1

    for r in reservations_data:
        if r.get("status") == "COMPLETED" and r.get("in_charge"):
            employee_reservations[r["in_charge"]] = employee_reservations.get(r["in_charge"], 0) + 1

    all_employees = set(employee_income.keys()) | set(employee_reservations.keys())
    employee_stats = []
    for name in all_employees:
        inc = round(employee_income.get(name, 0), 2)
        res = employee_reservations.get(name, 0)
        prod = employee_products.get(name, 0)
        avg_ticket = round(inc / res, 2) if res > 0 else 0
        employee_stats.append({
            "name": name,
            "income": inc,
            "reservations": res,
            "product_sales": prod,
            "avg_ticket": avg_ticket,
        })

    # Sort by income descending — first one is the star
    employee_stats.sort(key=lambda x: -x["income"])
    kpis["employee_stats"] = employee_stats
    star_employee = employee_stats[0]["name"] if employee_stats else "N/A"
    kpis["star_employee"] = star_employee

    kpis_str = json.dumps(kpis, ensure_ascii=False)

    # ── Build service breakdown for richer context ──────────────────
    service_income: dict[str, float] = {}
    for f in finances_data:
        if f.get("type") == "INCOME" and f.get("reservation_id") is not None:
            service_income[f["concept"]] = service_income.get(f["concept"], 0) + f["amount"]

    service_breakdown = ", ".join(
        f"{name}: {count} reservas" for name, count in service_count.items()
    ) or "Sin datos"

    income_breakdown = ", ".join(
        f"{name}: {amt:.0f}€" for name, amt in sorted(service_income.items(), key=lambda x: -x[1])
    ) or "Sin datos"

    employee_breakdown = "\n".join(
        f"  - {s['name']}: {s['income']}€ ingresos, {s['reservations']} reservas, {s['product_sales']} productos vendidos, ticket medio {s['avg_ticket']}€"
        for s in employee_stats
    ) or "Sin datos"

    # Daily breakdown for AI context
    daily_breakdown = ", ".join(
        f"{day_names[i]}: {daily_income[i]:.0f}€ ({daily_reservations[i]} res.)"
        for i in range(7)
        if daily_income[i] > 0 or daily_reservations[i] > 0
    ) or "Sin datos"

    # ── AI narrative ────────────────────────────────────────────────
    system_prompt = (
        "Eres un asesor de confianza para dueños de pequeños negocios en España. "
        "Hablas en español de forma cercana, clara y directa — como un buen amigo que entiende de números. "
        "NUNCA uses jerga técnica. En vez de decir 'un incremento del 63%', di algo como "
        "'ingresaste casi el doble que la semana pasada'. "
        "Traduce los datos en lo que significan para el negocio en el día a día. "
        "Responde SIEMPRE en formato JSON válido."
    )

    # Build previous context block if available
    prev_context = ""
    if prev_week_narrative:
        prev_context = (
            f"\n── CONTEXTO: LO QUE DIJIMOS LA SEMANA PASADA ──\n"
            f"{prev_week_narrative}\n"
            f"Usa esta información para hacer comparaciones naturales y notar tendencias. "
            f"Por ejemplo: 'la semana pasada mencioné que los martes iban flojos, y esta semana "
            f"han mejorado mucho' o 'el problema con las ventas de productos sigue sin resolverse'. "
            f"NO copies frases del resumen anterior — solo úsalo como contexto.\n"
        )

    user_prompt = (
        f"Negocio: {business_name}\n"
        f"Semana analizada: {week_label}\n"
        f"{prev_context}\n"
        f"── DATOS DE ESTA SEMANA ──\n"
        f"Ingresos totales: {current['income']}€\n"
        f"Reservas completadas: {current['reservations']}\n"
        f"Ventas de productos: {current['product_sales']}\n"
        f"Desglose diario: {daily_breakdown}\n"
        f"Desglose por servicio: {service_breakdown}\n"
        f"Ingresos por servicio: {income_breakdown}\n"
        f"Mejor día (más ingresos): {best_day}\n"
        f"Peor día (menos ingresos): {worst_day}\n"
        f"Servicio más solicitado: {top_service}\n\n"
        f"── RENDIMIENTO POR EMPLEADO ──\n"
        f"{employee_breakdown}\n"
        f"Empleado estrella (más ingresos): {star_employee}\n\n"
        f"── SEMANA ANTERIOR (para comparar) ──\n"
        f"Ingresos: {prev['income']}€ | Reservas: {prev['reservations']} | Productos: {prev['product_sales']}\n\n"
        "Genera un JSON con EXACTAMENTE estas 4 claves:\n\n"
        "1. \"narrative\": Un texto de 4-6 oraciones que explique cómo le fue al negocio. "
        "Estructura: primero lo positivo (qué fue bien), luego lo que podría mejorar, "
        "y cierra con una observación útil. NO repitas porcentajes ni números crudos — "
        "tradúcelos a lenguaje humano (ej: 'tuviste un tercio más de clientes', "
        "'los martes fueron tu día estrella'). Que el dueño sienta que alguien "
        "analizó su negocio de verdad."
    )

    if prev_week_narrative:
        user_prompt += (
            " Si tienes contexto de la semana anterior, haz referencias naturales "
            "como 'la semana pasada te comenté que...' o 'recuerda que mencioné...'. "
            "Esto da continuidad y muestra que estás siguiendo la evolución del negocio."
        )

    user_prompt += (
        "\n\n"
        "2. \"client_narrative\": Un texto de 3-4 oraciones SOLO sobre los clientes y servicios. "
        "¿Qué servicios atraen más gente? ¿Hay alguno que genera más dinero por visita? "
        "¿Qué día conviene impulsar más? Da un consejo concreto y práctico, "
        "no genérico. NO repitas lo que ya dijiste en narrative.\n\n"
        "3. \"team_narrative\": Un texto de 2-3 oraciones sobre el rendimiento del equipo. "
        "Destaca al empleado estrella de la semana y explica por qué (no solo porque facturó más, "
        "sino qué hizo diferente — más reservas, mejor ticket medio, más productos vendidos). "
        "Si hay diferencias notables entre empleados, menciónalas de forma constructiva. "
        "Ejemplo: 'Carlos fue tu empleado estrella — atendió a más clientes y además vendió "
        "productos en casi cada visita. Ana tuvo menos reservas pero su ticket medio fue más alto, "
        "lo que indica que sus servicios son más premium.'\n\n"
        "4. \"opportunities\": Una lista de EXACTAMENTE 3 objetos JSON. Cada objeto tiene:\n"
        "   - \"text\": El consejo en 1-2 oraciones, accionable y basado en datos reales.\n"
        "   - \"impact\": \"alto\", \"medio\" o \"bajo\" — según el impacto potencial en el negocio.\n"
        "   Asigna impacto \"alto\" a la oportunidad que más dinero o clientes puede generar, "
        "\"medio\" a las de mejora gradual, y \"bajo\" a optimizaciones menores.\n"
        "   Ejemplos del estilo:\n"
        '   {\"text\": \"El tinte es tu servicio más rentable (45€ por visita) pero pocos clientes lo piden — prueba a ofrecerlo como complemento del corte\", \"impact\": \"alto\"}\n'
        '   {\"text\": \"Los domingos apenas generas ingresos — una promo de fin de semana podría llenar esos huecos\", \"impact\": \"medio\"}\n'
        '   {\"text\": \"Las ventas de productos subieron esta semana — coloca los productos cerca de la caja para aprovechar las compras impulsivas\", \"impact\": \"bajo\"}\n'
        "Que cada oportunidad se sienta como un consejo real, no como una frase de manual.\n"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1200,
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"
    result = json.loads(content)

    # Normalize opportunities — support both old string[] and new {text, impact}[] format
    raw_opps = result.get("opportunities", [])
    normalized_opps = []
    for opp in raw_opps:
        if isinstance(opp, str):
            normalized_opps.append({"text": opp, "impact": "medio"})
        elif isinstance(opp, dict):
            normalized_opps.append({
                "text": opp.get("text", str(opp)),
                "impact": opp.get("impact", "medio"),
            })
    result["opportunities"] = normalized_opps

    # Store extra data inside kpis JSON for simplicity (no schema change needed)
    kpis["opportunities"] = result.get("opportunities", [])
    kpis["team_narrative"] = result.get("team_narrative", "")
    kpis_str = json.dumps(kpis, ensure_ascii=False)

    return {
        "narrative": result.get("narrative", ""),
        "kpis": kpis_str,
        "client_narrative": result.get("client_narrative"),
    }


class AIServiceError(Exception):
    pass
