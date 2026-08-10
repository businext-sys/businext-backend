"""
Envio de notificaciones push via Expo Push API (issue #031).

Sigue el mismo patron de degradacion elegante que email_service.py: si
algo falla (red, tokens invalidos, etc.), se loguea y no se interrumpe
el flujo principal (ej. la creacion de una BookingRequest no debe fallar
porque la notificacion push no se pudo enviar).
"""
import httpx

EXPO_PUSH_API_URL = "https://exp.host/--/api/v2/push/send"


def send_push_notifications(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Envia una notificacion push a una lista de Expo push tokens.

    No lanza excepciones: los errores se loguean y se ignoran, para no
    bloquear el flujo que dispara la notificacion (ej. creacion de un
    BookingRequest).
    """
    if not tokens:
        return

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            **({"data": data} if data else {}),
        }
        for token in tokens
    ]

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                EXPO_PUSH_API_URL,
                json=messages,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 400:
                print(
                    f"[push_notification_service] Expo Push API respondio "
                    f"{response.status_code}: {response.text}"
                )
    except Exception as e:
        print(f"[push_notification_service] Fallo al enviar push: {e}")
