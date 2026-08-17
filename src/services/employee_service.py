import os
import re
import smtplib
from email.message import EmailMessage

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from src.database.models.member_model import BusinessMember
from src.database.models.profile_model import Profile
from src.services.supabase_utils import get_app_url, get_supabase_settings

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_employee_role(role: str) -> str:
    if role == "manager":
        return "manager"
    if role == "owner":
        return "owner"
    return "employee"


def validate_invite_payload(display_name: str, email: str, phone: str) -> None:
    if len(display_name) < 2:
        raise HTTPException(status_code=400, detail="El nombre del empleado es obligatorio")

    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Email inválido")

    if not phone:
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")


def validate_onboarding_password(password: str) -> None:
    if not password or len(password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Contraseña inválida (mínimo 6 caracteres)",
        )

    if (
        not re.search(r"[A-Z]", password)
        or not re.search(r"[a-z]", password)
        or not re.search(r"[0-9]", password)
    ):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe contener mayúsculas, minúsculas y números",
        )


def _serialize_employee(member: BusinessMember, profile: Profile | None) -> dict:
    return {
        "id": member.id,
        "businessId": member.business_id,
        "memberUserId": member.member_user_id,
        "displayName": profile.display_name if profile else None,
        "email": profile.email if profile else None,
        "phone": profile.phone if profile else None,
        "role": normalize_employee_role(member.role or "employee"),
        "status": member.status or "pending",
        "locationId": member.location_id,
        "createdAt": member.created_at,
    }


def list_employees_for_business(session: Session, business_id: str) -> list[dict]:
    members = session.exec(
        select(BusinessMember)
        .where(BusinessMember.business_id == business_id)
        .order_by(BusinessMember.created_at.desc())
    ).all()

    if not members:
        return []

    member_ids = [member.member_user_id for member in members]
    profiles = session.exec(
        select(Profile).where(Profile.id.in_(member_ids))
    ).all()
    profile_by_id = {profile.id: profile for profile in profiles}

    return [
        _serialize_employee(member, profile_by_id.get(member.member_user_id))
        for member in members
    ]


async def _generate_invite_link(
    email: str,
    display_name: str,
    phone: str,
    role: str,
    business_id: str,
    invited_by: str,
) -> tuple[str, str]:
    supabase_url, service_role_key = get_supabase_settings()
    app_url = get_app_url()

    payload = {
        "type": "invite",
        "email": email,
        "redirect_to": f"{app_url}/auth/confirm",
        "data": {
            "display_name": display_name,
            "phone": phone,
            "role": role,
            "business_id": business_id,
            "invited_by": invited_by,
        },
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{supabase_url}/auth/v1/admin/generate_link",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    data = response.json() if response.content else {}
    print("Supabase generate_link response:", data)

    if not response.is_success:
        message = (
            data.get("msg")
            or data.get("error_description")
            or data.get("error")
            or "No se pudo generar la invitación"
        )
        raise HTTPException(status_code=500, detail=message)

    hashed_token = data.get("hashed_token")
    user_id = data.get("id")

    if not hashed_token or not user_id:
        raise HTTPException(status_code=500, detail="No se pudo generar la invitación")

    invite_url = (
        f"{app_url}/auth/confirm?token_hash={hashed_token}"
        "&type=invite&next=%2Femployee%2Fonboarding"
    )

    return user_id, invite_url


async def _delete_supabase_user(user_id: str) -> None:
    supabase_url, service_role_key = get_supabase_settings()

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.delete(
            f"{supabase_url}/auth/v1/admin/users/{user_id}",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
            },
        )

    if not response.is_success:
        data = response.json() if response.content else {}
        message = (
            data.get("msg")
            or data.get("error_description")
            or data.get("error")
            or "No se pudo eliminar el usuario de Supabase"
        )
        raise HTTPException(status_code=500, detail=message)


async def _update_supabase_user_password(user_id: str, password: str) -> None:
    supabase_url, service_role_key = get_supabase_settings()

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.put(
            f"{supabase_url}/auth/v1/admin/users/{user_id}",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            json={"password": password},
        )

    data = response.json() if response.content else {}

    if not response.is_success:
        message = (
            data.get("msg")
            or data.get("error_description")
            or data.get("error")
            or "No se pudo actualizar la contraseña"
        )
        raise HTTPException(status_code=500, detail=message)


def send_invite_email(email: str, display_name: str, role: str, invite_url: str) -> None:
    gmail_user = os.getenv("GMAIL_BUSINEXT_USER")
    gmail_password = os.getenv("GMAIL_BUSINEXT_PASSWORD")

    if not gmail_user or not gmail_password:
        raise HTTPException(
            status_code=500,
            detail="Gmail credentials are not configured",
        )

    message = EmailMessage()
    message["From"] = gmail_user
    message["To"] = email
    message["Subject"] = "Has sido invitado a Businext"
    message.set_content(
        "Has sido invitado a Businext. "
        f"Acepta la invitación aquí: {invite_url}"
    )
    message.add_alternative(
        f"""
        <div style=\"font-family: Arial, sans-serif; line-height: 1.5;\">
          <h2>Hola {display_name},</h2>
          <p>Has sido invitado a unirte al equipo en Businext con el rol de <strong>{role}</strong>.</p>
          <p>Haz clic en el siguiente botón para aceptar la invitación y completar tu acceso:</p>
          <p>
            <a href=\"{invite_url}\" style=\"display:inline-block;padding:12px 20px;background:#2563eb;color:#ffffff;text-decoration:none;border-radius:8px;\">
              Aceptar invitación
            </a>
          </p>
          <p>Si el botón no funciona, copia y pega este enlace en tu navegador:</p>
          <p>{invite_url}</p>
        </div>
        """,
        subtype="html",
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(message)
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def invite_employee(
    session: Session,
    business_id: str,
    invited_by: str,
    display_name: str,
    email: str,
    phone: str,
    role: str,
) -> list[dict]:
    normalized_email = email.strip().lower()
    normalized_display_name = display_name.strip()
    normalized_phone = phone.strip()
    normalized_role = normalize_employee_role(role.strip().lower())

    validate_invite_payload(
        normalized_display_name,
        normalized_email,
        normalized_phone,
    )

    existing_profile = session.exec(
        select(Profile).where(Profile.email == normalized_email)
    ).first()
    if existing_profile:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ya existe una cuenta con ese email. Usa un email nuevo para invitar al empleado."
            ),
        )

    user_id, invite_url = await _generate_invite_link(
        email=normalized_email,
        display_name=normalized_display_name,
        phone=normalized_phone,
        role=normalized_role,
        business_id=business_id,
        invited_by=invited_by,
    )

    member = BusinessMember(
        business_id=business_id,
        member_user_id=user_id,
        role=normalized_role,
        status="pending",
    )

    try:
        session.add(member)
        send_invite_email(
            email=normalized_email,
            display_name=normalized_display_name,
            role=normalized_role,
            invite_url=invite_url,
        )
        session.commit()
    except HTTPException:
        session.rollback()
        await _delete_supabase_user(user_id)
        raise
    except Exception:
        session.rollback()
        await _delete_supabase_user(user_id)
        raise

    return list_employees_for_business(session, business_id)


async def delete_employee(
    session: Session,
    business_id: str,
    member_user_id: str,
) -> list[dict]:
    member = session.exec(
        select(BusinessMember).where(
            BusinessMember.member_user_id == member_user_id,
            BusinessMember.business_id == business_id,
        )
    ).first()

    if not member:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    if member.role == "owner":
        raise HTTPException(status_code=400, detail="No puedes eliminar al propietario del negocio")

    await _delete_supabase_user(member_user_id)

    session.delete(member)
    session.commit()


def update_employee(
    session: Session,
    business_id: str,
    member_user_id: str,
    role: str | None,
    status: str | None,
    location_id: int | None = None,
) -> dict:
    member = session.exec(
        select(BusinessMember).where(
            BusinessMember.member_user_id == member_user_id,
            BusinessMember.business_id == business_id,
        )
    ).first()

    if not member:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    # Owner can only change their location_id, not role or status
    if member.role == "owner":
        if role is not None or status is not None:
            raise HTTPException(
                status_code=400,
                detail="No se puede cambiar el rol o estado del propietario.",
            )
        if location_id is not None:
            member.location_id = location_id
        session.add(member)
        session.commit()
        session.refresh(member)
        profile = session.exec(select(Profile).where(Profile.id == member_user_id)).first()
        return _serialize_employee(member, profile)

    if role is not None:
        if member.status == "pending":
            raise HTTPException(
                status_code=400,
                detail="Elimina la invitación pendiente y crea una nueva para cambiar el rol.",
            )
        valid_roles = {"employee", "manager"}
        if role not in valid_roles:
            raise HTTPException(status_code=400, detail=f"Rol inválido. Opciones: {valid_roles}")
        member.role = role

    if status is not None:
        valid_statuses = {"pending", "active", "inactive"}
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Status inválido. Opciones: {valid_statuses}")
        member.status = status

    if location_id is not None:
        member.location_id = location_id

    session.add(member)
    session.commit()
    session.refresh(member)

    profile = session.exec(select(Profile).where(Profile.id == member_user_id)).first()
    return _serialize_employee(member, profile)


async def complete_employee_onboarding(
    session: Session,
    user_id: str,
    password: str,
) -> None:
    validate_onboarding_password(password)

    membership = session.exec(
        select(BusinessMember).where(BusinessMember.member_user_id == user_id)
    ).first()

    if not membership:
        raise HTTPException(
            status_code=404,
            detail="No se encontró una invitación válida para este empleado",
        )

    await _update_supabase_user_password(user_id, password)

    membership.status = "active"
    session.add(membership)

    profile = session.exec(select(Profile).where(Profile.id == user_id)).first()
    if profile:
        profile.status = "onboarded"
        session.add(profile)

    session.commit()
