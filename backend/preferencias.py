"""User notification preferences and push device tokens."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import DispositivoPush, UsuarioPreferencia


def get_or_create_preferencias(db: Session, usuario_id: int) -> UsuarioPreferencia:
    stmt = select(UsuarioPreferencia).where(UsuarioPreferencia.usuario_id == usuario_id)
    preferencias = db.execute(stmt).scalars().first()

    if preferencias:
        return preferencias

    preferencias = UsuarioPreferencia(
        usuario_id=usuario_id,
        notificaciones_push=True,
    )
    db.add(preferencias)
    db.commit()
    db.refresh(preferencias)
    return preferencias


def push_habilitado_para_usuario(db: Session, usuario_id: int) -> bool:
    preferencias = get_or_create_preferencias(db, usuario_id)
    return preferencias.notificaciones_push


def actualizar_preferencias(db: Session, usuario_id: int, notificaciones_push: bool) -> UsuarioPreferencia:
    preferencias = get_or_create_preferencias(db, usuario_id)
    preferencias.notificaciones_push = notificaciones_push
    db.commit()
    db.refresh(preferencias)
    return preferencias


def registrar_dispositivo_push(
    db: Session,
    usuario_id: int,
    push_token: str,
    platform: str | None = None,
) -> DispositivoPush:
    token = push_token.strip()
    stmt = select(DispositivoPush).where(DispositivoPush.push_token == token)
    dispositivo = db.execute(stmt).scalars().first()

    if dispositivo:
        dispositivo.usuario_id = usuario_id
        dispositivo.platform = platform
        dispositivo.activo = True
    else:
        dispositivo = DispositivoPush(
            usuario_id=usuario_id,
            push_token=token,
            platform=platform,
            activo=True,
        )
        db.add(dispositivo)

    db.commit()
    db.refresh(dispositivo)
    return dispositivo


def desactivar_dispositivo_push(db: Session, usuario_id: int, push_token: str) -> bool:
    stmt = select(DispositivoPush).where(
        DispositivoPush.usuario_id == usuario_id,
        DispositivoPush.push_token == push_token.strip(),
    )
    dispositivo = db.execute(stmt).scalars().first()
    if not dispositivo:
        return False

    dispositivo.activo = False
    db.commit()
    return True


def obtener_tokens_push_activos(db: Session, usuario_id: int) -> list[str]:
    stmt = select(DispositivoPush.push_token).where(
        DispositivoPush.usuario_id == usuario_id,
        DispositivoPush.activo.is_(True),
    )
    return list(db.execute(stmt).scalars().all())
