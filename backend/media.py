"""Media URL resolution and file serving for event photos."""

import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

load_dotenv()

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "media")).resolve()
MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", "").rstrip("/")
GCS_BUCKET = os.getenv("GCS_BUCKET", "").strip()
API_PUBLIC_URL = os.getenv(
    "API_PUBLIC_URL",
    os.getenv("EXPO_PUBLIC_API_URL", "http://localhost:8000"),
).rstrip("/")

DEFAULT_EVENT_IMAGE = (
    "https://images.unsplash.com/photo-1519315901367-f34ff9154487"
    "?q=80&w=1000&auto=format&fit=crop"
)

DEFAULT_ATHLETE_IMAGE = (
    "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d"
    "?q=80&w=400&auto=format&fit=crop"
)

DEFAULT_USER_IMAGE = (
    "https://images.unsplash.com/photo-1535713875002-d1d0cf377fde"
    "?q=80&w=400&auto=format&fit=crop"
)


def _resolve_media_url(
    foto: str | None,
    *,
    api_base: str | None,
    default: str,
) -> str:
    """Build a loadable URL from a DB photo path without requiring local file checks."""
    if not foto or not str(foto).strip():
        return default

    foto = str(foto).strip()
    if foto.startswith(("http://", "https://")):
        return foto

    if MEDIA_BASE_URL:
        return f"{MEDIA_BASE_URL}/{foto.lstrip('/')}"

    if GCS_BUCKET:
        return f"https://storage.googleapis.com/{GCS_BUCKET}/{foto.lstrip('/')}"

    safe_path = _safe_relative_path(foto)
    if not safe_path:
        return default

    base = (api_base or API_PUBLIC_URL).rstrip("/")
    encoded_path = quote(foto.lstrip("/"), safe="/")
    return f"{base}/api/media/{encoded_path}"


def build_media_url(foto: str | None, api_base: str | None = None) -> str:
    """Turn DB event `foto` path into a URL the mobile app can load."""
    return _resolve_media_url(foto, api_base=api_base, default=DEFAULT_EVENT_IMAGE)


def build_athlete_image_url(foto: str | None, api_base: str | None = None) -> str:
    """Turn DB persona `foto` path into a URL for athlete avatars."""
    return _resolve_media_url(foto, api_base=api_base, default=DEFAULT_ATHLETE_IMAGE)


def build_user_image_url(foto: str | None, api_base: str | None = None) -> str:
    """Turn DB user photo path into a URL for profile avatars."""
    return _resolve_media_url(foto, api_base=api_base, default=DEFAULT_USER_IMAGE)


def save_user_photo_file(usuario_id: int, filename: str | None, content: bytes) -> str:
    """Persist uploaded profile photo and return relative storage path."""
    ext = Path(filename or "avatar.jpg").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Formato de imagen no permitido")

    if len(content) > 5 * 1024 * 1024:
        raise ValueError("La imagen no puede superar 5 MB")

    relative_path = f"usuarios/{usuario_id}/avatar{ext}"
    destination = MEDIA_ROOT / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return relative_path


def _safe_relative_path(file_path: str) -> str:
    normalized = file_path.lstrip("/").replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != ".."]
    return "/".join(parts)


def serve_media_file(file_path: str):
    """Serve a media file from local disk or Google Cloud Storage."""
    safe_path = _safe_relative_path(file_path)
    if not safe_path:
        raise HTTPException(status_code=404, detail="Ruta de archivo inválida")

    local_file = MEDIA_ROOT / safe_path
    if local_file.is_file():
        media_type = mimetypes.guess_type(safe_path)[0] or "application/octet-stream"
        return FileResponse(local_file, media_type=media_type)

    if GCS_BUCKET:
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="google-cloud-storage no instalado. Ejecuta: pip install google-cloud-storage",
            ) from exc

        try:
            client = storage.Client()
            blob = client.bucket(GCS_BUCKET).blob(safe_path)
            if blob.exists():
                content = blob.download_as_bytes()
                media_type = (
                    blob.content_type
                    or mimetypes.guess_type(safe_path)[0]
                    or "application/octet-stream"
                )
                return Response(content=content, media_type=media_type)
        except HTTPException:
            raise
        except Exception:
            pass

        return RedirectResponse(
            f"https://storage.googleapis.com/{GCS_BUCKET}/{safe_path}",
            status_code=302,
        )

    if MEDIA_BASE_URL:
        return RedirectResponse(f"{MEDIA_BASE_URL}/{safe_path}", status_code=302)

    return RedirectResponse(DEFAULT_EVENT_IMAGE, status_code=302)
