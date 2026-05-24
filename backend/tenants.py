"""Tenant listing and invitation validation for registration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Invitacion, RolUsuario, Tenant, UsuarioTenantRol


def list_active_tenants(db: Session) -> list[dict]:
    stmt = (
        select(Tenant.id, Tenant.nombre)
        .where(Tenant.activo.is_(True))
        .order_by(Tenant.nombre.asc())
    )
    rows = db.execute(stmt).all()
    return [{"id": row.id, "nombre": row.nombre} for row in rows]


def _parse_invitation_token(raw_token: str) -> uuid.UUID:
    cleaned = raw_token.strip()
    try:
        return uuid.UUID(cleaned)
    except ValueError as exc:
        raise ValueError("Código de invitación inválido") from exc


def _resolve_rol(raw_rol: str | None) -> RolUsuario:
    if not raw_rol:
        return RolUsuario.READONLY
    try:
        return RolUsuario(raw_rol.strip().upper())
    except ValueError:
        return RolUsuario.READONLY


def get_invitation_preview(db: Session, token: str, email: str) -> dict:
    invitation = _get_valid_invitation(db, token, email)
    tenant = db.get(Tenant, invitation.tenant_id)
    return {
        "valid": True,
        "tenant_id": invitation.tenant_id,
        "tenant_nombre": tenant.nombre if tenant else "Organización",
        "rol": invitation.rol,
        "email": invitation.email,
    }


def resolve_registration_context(
    db: Session,
    *,
    email: str,
    tenant_id: int | None,
    invitation_token: str | None,
) -> tuple[int, RolUsuario, Invitacion | None]:
    normalized_email = email.lower().strip()

    if invitation_token:
        invitation = _get_valid_invitation(db, invitation_token, normalized_email)
        return invitation.tenant_id, _resolve_rol(invitation.rol), invitation

    if tenant_id is None:
        raise ValueError("Debes seleccionar una organización o usar un código de invitación")

    tenant = db.get(Tenant, tenant_id)
    if not tenant or not tenant.activo:
        raise ValueError("La organización seleccionada no está disponible")

    return tenant_id, RolUsuario.READONLY, None


def _get_valid_invitation(db: Session, token: str, email: str) -> Invitacion:
    parsed_token = _parse_invitation_token(token)
    stmt = select(Invitacion).where(Invitacion.token == parsed_token)
    invitation = db.execute(stmt).scalars().first()

    if not invitation:
        raise ValueError("Código de invitación no encontrado")

    if invitation.estado != "pendiente":
        raise ValueError("Esta invitación ya no está disponible")

    now = datetime.now(timezone.utc)
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        raise ValueError("Esta invitación ha expirado")

    if invitation.email.lower().strip() != email.lower().strip():
        raise ValueError("El correo no coincide con la invitación")

    tenant = db.get(Tenant, invitation.tenant_id)
    if not tenant or not tenant.activo:
        raise ValueError("La organización de la invitación no está activa")

    return invitation


def ensure_usuario_tenant_rol(
    db: Session,
    usuario_id: int,
    tenant_id: int,
    rol: RolUsuario,
) -> None:
    stmt = select(UsuarioTenantRol).where(
        UsuarioTenantRol.usuario_id == usuario_id,
        UsuarioTenantRol.tenant_id == tenant_id,
    )
    existing = db.execute(stmt).scalars().first()
    if existing:
        existing.rol = rol
        existing.activo = True
        return

    db.add(
        UsuarioTenantRol(
            usuario_id=usuario_id,
            tenant_id=tenant_id,
            rol=rol,
            activo=True,
        )
    )


def mark_invitation_accepted(db: Session, invitation: Invitacion) -> None:
    invitation.estado = "aceptada"
    invitation.accepted_at = datetime.now(timezone.utc)
