"""Push notifications via the Expo Push API."""
import httpx

EXPO_PUSH_API_URL = "https://exp.host/--/api/v2/push/send"


def send_push_notifications(
    tokens: list[str],
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to a list of Expo push tokens.

    Never raises: failures are logged so they cannot break the caller.
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
                    f"[push_notification_service] Expo Push API responded "
                    f"{response.status_code}: {response.text}"
                )
    except Exception as e:
        print(f"[push_notification_service] Failed to send push: {e}")
