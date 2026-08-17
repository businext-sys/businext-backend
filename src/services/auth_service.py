import re

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from src.database.models.profile_model import Profile
from src.services.supabase_utils import get_app_url, get_supabase_anon_key, get_supabase_settings

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(email: str) -> None:
    if not email or not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Email inválido")


def validate_password(password: str) -> None:
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


def validate_full_name(full_name: str) -> None:
    if not full_name or len(full_name) < 3:
        raise HTTPException(
            status_code=400,
            detail="Nombre completo inválido (mínimo 3 caracteres)",
        )

    if re.search(r"[^a-zA-ZáéíóúÁÉÍÓÚüÜñÑ\s]", full_name):
        raise HTTPException(
            status_code=400,
            detail="El nombre completo no debe contener números ni símbolos",
        )



async def _check_user_exists_supabase(email: str) -> bool:
    supabase_url, service_role_key = get_supabase_settings()

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            json={"query": f"email = '{email}'"},
        )

    if not response.is_success:
        if response.status_code == 401 or response.status_code == 403:
            raise HTTPException(status_code=500, detail="Supabase authentication failed")
        return False

    data = response.json() if response.content else {}
    users = data.get("users", []) if isinstance(data, dict) else []
    return len(users) > 0


async def signup_owner(
    session: Session,
    email: str,
    password: str,
    full_name: str,
) -> dict:
    normalized_email = email.strip().lower()
    normalized_password = password.strip()
    normalized_full_name = full_name.strip()

    validate_email(normalized_email)
    validate_password(normalized_password)
    validate_full_name(normalized_full_name)

    existing_profile = session.exec(
        select(Profile).where(Profile.email == normalized_email)
    ).first()
    if existing_profile:
        raise HTTPException(
            status_code=409,
            detail="Este usuario ya está registrado.",
        )

    exists_supabase = await _check_user_exists_supabase(normalized_email)
    if exists_supabase:
        raise HTTPException(
            status_code=409,
            detail="Este usuario ya está registrado.",
        )

    supabase_url, _ = get_supabase_settings()
    anon_key = get_supabase_anon_key()
    app_url = get_app_url()

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{supabase_url}/auth/v1/signup",
            params={"redirect_to": f"{app_url}/auth/confirm"},
            headers={
                "apikey": anon_key,
                "Content-Type": "application/json",
            },
            json={
                "email": normalized_email,
                "password": normalized_password,
                "data": {
                    "full_name": normalized_full_name,
                },
            },
        )

    data = response.json() if response.content else {}

    if not response.is_success:
        message = (
            data.get("msg")
            or data.get("error_description")
            or data.get("error")
            or data.get("message")
            or "Error al crear la cuenta"
        )
        raise HTTPException(status_code=500, detail=message)

    return {"success": True}
