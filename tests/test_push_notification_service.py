"""Tests for src/services/push_notification_service.py."""
from unittest.mock import MagicMock, patch

from src.services.push_notification_service import send_push_notifications


class TestSendPushNotifications:
    def test_no_tokens_does_not_call_http(self):
        with patch("httpx.Client") as mock_client_cls:
            send_push_notifications([], "Titulo", "Cuerpo")
            mock_client_cls.assert_not_called()

    def test_sends_one_message_per_token(self):
        mock_response = MagicMock(status_code=200)
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            send_push_notifications(
                ["token-1", "token-2"],
                "Nueva solicitud",
                "Ana solicito Corte de cabello",
                data={"type": "booking_request", "bookingRequestId": 42},
            )

        post_call = mock_client.__enter__.return_value.post
        post_call.assert_called_once()
        args, kwargs = post_call.call_args
        assert args[0] == "https://exp.host/--/api/v2/push/send"
        messages = kwargs["json"]
        assert len(messages) == 2
        assert {m["to"] for m in messages} == {"token-1", "token-2"}
        assert all(m["title"] == "Nueva solicitud" for m in messages)
        assert all(m["data"]["bookingRequestId"] == 42 for m in messages)

    def test_does_not_raise_on_http_error(self):
        mock_response = MagicMock(status_code=400, text="Bad Request")
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            # Must not raise even when Expo returns an error.
            send_push_notifications(["token-1"], "T", "B")

    def test_does_not_raise_on_network_exception(self):
        with patch("httpx.Client", side_effect=Exception("network down")):
            # Must not raise when the connection fails.
            send_push_notifications(["token-1"], "T", "B")
