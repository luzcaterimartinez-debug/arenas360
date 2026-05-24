"""Password reset token management."""

import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth import hash_password
from backend.models import PasswordResetToken, Usuario

RESET_TOKEN_EXPIRE_HOURS = 24
INSECURE_DEBUG = os.getenv("DEBUG", "False").lower() == "true"


def request_password_reset(db: Session, email: str) -> dict:
    """Create a reset token for the user. Always returns a generic message."""
    stmt = select(Usuario).where(Usuario.email == email.lower().strip())
    usuario = db.execute(stmt).scalars().first()

    response = {
        "message": "Si el correo está registrado, recibirás instrucciones para restablecer tu contraseña.",
        "debug_token": None,
    }

    if not usuario or not usuario.activo:
        return response

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_EXPIRE_HOURS)

    db.add(
        PasswordResetToken(
            usuario_id=usuario.id,
            token=token,
            expira_en=expires,
            usado=False,
        )
    )
    db.commit()

    if INSECURE_DEBUG:
        response["debug_token"] = token

    return response


def confirm_password_reset(db: Session, token: str, new_password: str) -> None:
    """Validate token and update password."""
    stmt = (
        select(PasswordResetToken)
        .where(PasswordResetToken.token == token.strip())
        .where(PasswordResetToken.usado.is_(False))
    )
    reset_row = db.execute(stmt).scalars().first()

    if not reset_row:
        raise ValueError("Token inválido o expirado")

    now = datetime.now(timezone.utc)
    expires = reset_row.expira_en
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if expires < now:
        raise ValueError("Token inválido o expirado")

    usuario = db.get(Usuario, reset_row.usuario_id)
    if not usuario or not usuario.activo:
        raise ValueError("Token inválido o expirado")

    usuario.hashed_password = hash_password(new_password)
    reset_row.usado = True
    db.commit()
