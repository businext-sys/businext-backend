import os

import resend

resend.api_key = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "noreply@businext.app")
APP_URL = os.environ.get("APP_URL", "http://localhost:3000")


def send_email(to: str, subject: str, html: str, reply_to: str | None = None) -> str | None:
    """Send an email via Resend. Returns the email ID or None on failure."""
    if not resend.api_key:
        print("[email_service] RESEND_API_KEY not configured, skipping email")
        return None
    try:
        params: dict = {
            "from": FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if reply_to:
            params["reply_to"] = reply_to
        result = resend.Emails.send(params)
        return result.get("id") if isinstance(result, dict) else None
    except Exception as e:
        print(f"[email_service] Failed to send email to {to}: {e}")
        return None


# ─── Shared layout ────────────────────────────────────────────────────────────

def _wrap(body: str, business_name: str, business_phone: str | None = None, business_email: str | None = None) -> str:
    """Wrap email body in a styled container."""
    contact_parts = []
    if business_phone:
        contact_parts.append(f'<a href="tel:{business_phone}" style="color:#2563eb; text-decoration:none;">{business_phone}</a>')
    if business_email:
        contact_parts.append(f'<a href="mailto:{business_email}" style="color:#2563eb; text-decoration:none;">{business_email}</a>')

    contact_html = ""
    if contact_parts:
        contact_html = f"""
        <div style="padding:12px 24px; background:#f9fafb; border-top:1px solid #e5e7eb; text-align:center;">
          <p style="margin:0 0 4px; font-size:12px; font-weight:600; color:#6b7280;">Contacto</p>
          <p style="margin:0; font-size:13px; color:#374151;">{" &nbsp;&middot;&nbsp; ".join(contact_parts)}</p>
        </div>
        """

    return f"""
    <div style="background-color:#f4f4f5; padding:32px 16px; font-family:'Segoe UI',Roboto,sans-serif;">
      <div style="max-width:480px; margin:0 auto; background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.08);">
        <!-- Header -->
        <div style="background:#111827; padding:20px 24px;">
          <h1 style="margin:0; font-size:18px; font-weight:700; color:#ffffff; letter-spacing:-0.3px;">Businext</h1>
        </div>
        <!-- Body -->
        <div style="padding:28px 24px;">
          {body}
        </div>
        <!-- Contact -->
        {contact_html}
        <!-- Footer -->
        <div style="padding:16px 24px; border-top:1px solid #e5e7eb; text-align:center;">
          <p style="margin:0; font-size:12px; color:#9ca3af;">{business_name} &middot; Powered by Businext</p>
        </div>
      </div>
    </div>
    """


def _detail_row(label: str, value: str) -> str:
    return f"""
    <tr>
      <td style="padding:8px 12px; font-size:13px; color:#6b7280; font-weight:600; width:120px; vertical-align:top;">{label}</td>
      <td style="padding:8px 12px; font-size:14px; color:#111827;">{value}</td>
    </tr>
    """


def _details_table(*rows: tuple[str, str]) -> str:
    inner = "".join(_detail_row(label, value) for label, value in rows)
    return f"""
    <table style="width:100%; border-collapse:collapse; background:#f9fafb; border-radius:8px; overflow:hidden; margin:16px 0;">
      {inner}
    </table>
    """


def _button(text: str, url: str, color: str = "#2563eb") -> str:
    return f"""
    <div style="text-align:center; margin:24px 0;">
      <a href="{url}" style="display:inline-block; background:{color}; color:#ffffff; padding:12px 28px; border-radius:8px; text-decoration:none; font-size:14px; font-weight:600;">{text}</a>
    </div>
    """


# ─── Email Templates ─────────────────────────────────────────────────────────


def email_request_received_client(
    client_name: str,
    service: str,
    date_str: str,
    business_name: str,
    employee_name: str | None = None,
    business_phone: str | None = None,
    business_email: str | None = None,
    location_name: str | None = None,
    location_address: str | None = None,
) -> tuple[str, str]:
    """Confirmation email to client after submitting a request."""
    subject = f"Solicitud recibida — {business_name}"

    employee_row = _detail_row("Profesional", employee_name) if employee_name else ""
    location_row = _detail_row("Local", location_name) if location_name else ""
    address_row = _detail_row("Dirección", location_address) if location_address else ""

    body = f"""
    <h2 style="margin:0 0 8px; font-size:20px; color:#111827;">¡Hola {client_name}!</h2>
    <p style="margin:0 0 20px; font-size:14px; color:#4b5563; line-height:1.5;">
      Hemos recibido tu solicitud de reserva. Te notificaremos cuando el negocio responda.
    </p>
    <table style="width:100%; border-collapse:collapse; background:#f9fafb; border-radius:8px; overflow:hidden; margin:16px 0;">
      {_detail_row("Servicio", service)}
      {_detail_row("Fecha", date_str)}
      {employee_row}
      {location_row}
      {address_row}
    </table>
    <p style="margin:16px 0 0; font-size:13px; color:#9ca3af; text-align:center;">
      Recibirás otro email cuando tu solicitud sea aceptada o rechazada.
    </p>
    """
    return subject, _wrap(body, business_name, business_phone, business_email)


def email_request_received_employee(
    client_name: str,
    client_phone: str,
    service: str,
    date_str: str,
    business_name: str,
) -> tuple[str, str]:
    """Notification to employee/owner about a new booking request."""
    subject = f"Nueva solicitud de reserva — {client_name}"
    dashboard_url = f"{APP_URL}/notifications"

    body = f"""
    <h2 style="margin:0 0 8px; font-size:20px; color:#111827;">Nueva solicitud de reserva</h2>
    <p style="margin:0 0 20px; font-size:14px; color:#4b5563; line-height:1.5;">
      Un cliente ha solicitado una cita en tu negocio.
    </p>
    {_details_table(
        ("Cliente", client_name),
        ("Teléfono", client_phone),
        ("Servicio", service),
        ("Fecha", date_str),
    )}
    {_button("Acceder a Businext", dashboard_url)}
    <p style="margin:0; font-size:13px; color:#9ca3af; text-align:center;">
      Acepta o rechaza esta solicitud desde tu panel.
    </p>
    """
    return subject, _wrap(body, business_name)


def email_request_accepted(
    client_name: str,
    service: str,
    date_str: str,
    business_name: str,
    employee_name: str | None = None,
    business_phone: str | None = None,
    business_email: str | None = None,
    location_name: str | None = None,
    location_address: str | None = None,
) -> tuple[str, str]:
    """Email to client when their request is accepted."""
    subject = f"¡Reserva confirmada! — {business_name}"

    employee_row = _detail_row("Profesional", employee_name) if employee_name else ""
    location_row = _detail_row("Local", location_name) if location_name else ""
    address_row = _detail_row("Dirección", location_address) if location_address else ""

    body = f"""
    <div style="text-align:center; margin-bottom:20px;">
      <div style="display:inline-block; background:#dcfce7; border-radius:50%; padding:12px;">
        <span style="font-size:28px;">&#10003;</span>
      </div>
    </div>
    <h2 style="margin:0 0 8px; font-size:20px; color:#111827; text-align:center;">¡Reserva confirmada!</h2>
    <p style="margin:0 0 20px; font-size:14px; color:#4b5563; line-height:1.5; text-align:center;">
      Hola {client_name}, tu cita ha sido aceptada.
    </p>
    <table style="width:100%; border-collapse:collapse; background:#f0fdf4; border-radius:8px; overflow:hidden; margin:16px 0; border:1px solid #bbf7d0;">
      {_detail_row("Servicio", service)}
      {_detail_row("Fecha", date_str)}
      {employee_row}
      {location_row}
      {address_row}
    </table>
    <p style="margin:16px 0 0; font-size:15px; color:#111827; text-align:center; font-weight:600;">
      ¡Te esperamos!
    </p>
    """
    return subject, _wrap(body, business_name, business_phone, business_email)


def email_request_rejected(
    client_name: str, business_name: str, reason: str | None = None,
    business_phone: str | None = None, business_email: str | None = None,
) -> tuple[str, str]:
    """Email to client when their request is rejected."""
    subject = f"Solicitud no disponible — {business_name}"
    reason_html = f"""
    <div style="background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:12px 16px; margin:16px 0;">
      <p style="margin:0; font-size:13px; color:#991b1b;"><strong>Motivo:</strong> {reason}</p>
    </div>
    """ if reason else ""

    body = f"""
    <h2 style="margin:0 0 8px; font-size:20px; color:#111827;">Hola {client_name}</h2>
    <p style="margin:0 0 20px; font-size:14px; color:#4b5563; line-height:1.5;">
      Lamentamos informarte que tu solicitud de reserva no ha podido ser aceptada en esta ocasión.
    </p>
    {reason_html}
    <p style="margin:16px 0 0; font-size:14px; color:#4b5563; line-height:1.5;">
      Puedes intentar reservar en otra fecha o contactar directamente con el negocio.
    </p>
    """
    return subject, _wrap(body, business_name, business_phone, business_email)


def email_request_expired(
    client_name: str, service: str, date_str: str, business_name: str,
    business_phone: str | None = None, business_email: str | None = None,
) -> tuple[str, str]:
    """Email to client when their request expires without response."""
    subject = f"Solicitud expirada — {business_name}"

    body = f"""
    <h2 style="margin:0 0 8px; font-size:20px; color:#111827;">Hola {client_name}</h2>
    <p style="margin:0 0 20px; font-size:14px; color:#4b5563; line-height:1.5;">
      Tu solicitud de reserva ha expirado sin respuesta.
    </p>
    {_details_table(
        ("Servicio", service),
        ("Fecha", date_str),
    )}
    <p style="margin:16px 0 0; font-size:14px; color:#4b5563; line-height:1.5;">
      Puedes enviar una nueva solicitud cuando lo desees.
    </p>
    """
    return subject, _wrap(body, business_name, business_phone, business_email)
