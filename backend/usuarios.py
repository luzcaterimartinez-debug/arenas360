"""User profile helpers."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.media import build_user_image_url, save_user_photo_file
from backend.models import Usuario
from backend.schemas import UsuarioPerfilUpdate, UsuarioResponse


def serialize_usuario(usuario: Usuario, api_base: str | None = None) -> UsuarioResponse:
    data = UsuarioResponse.model_validate(usuario)
    return data.model_copy(update={"image": build_user_image_url(usuario.foto, api_base)})


def actualizar_perfil(db: Session, usuario: Usuario, payload: UsuarioPerfilUpdate) -> Usuario:
    usuario.primer_nombre = payload.primer_nombre.strip()
    usuario.segundo_nombre = payload.segundo_nombre.strip() if payload.segundo_nombre else None
    usuario.primer_apellido = payload.primer_apellido.strip()
    usuario.segundo_apellido = payload.segundo_apellido.strip() if payload.segundo_apellido else None
    usuario.telefono = payload.telefono.strip() if payload.telefono else None
    db.commit()
    db.refresh(usuario)
    return usuario


def actualizar_foto_perfil(
    db: Session,
    usuario: Usuario,
    filename: str | None,
    content: bytes,
) -> Usuario:
    try:
        relative_path = save_user_photo_file(usuario.id, filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    usuario.foto = relative_path
    db.commit()
    db.refresh(usuario)
    return usuario
