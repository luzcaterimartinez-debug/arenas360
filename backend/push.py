"""Send push notifications via Expo Push API."""

import json
import logging
import urllib.error
import urllib.request

from sqlalchemy.orm import Session

from backend.preferencias import obtener_tokens_push_activos, push_habilitado_para_usuario

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _build_message(token: str, titulo: str, cuerpo: str, payload_data: dict) -> dict:
    return {
        "to": token,
        "title": titulo,
        "body": cuerpo,
        "sound": "default",
        "priority": "high",
        "channelId": "default",
        "data": payload_data,
    }


def _send_expo_messages(messages: list[dict]) -> tuple[int, list[str]]:
    if not messages:
        return 0, []

    payload = json.dumps(messages).encode("utf-8")
    request = urllib.request.Request(
        EXPO_PUSH_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logger.warning("No se pudo enviar push notification: %s", exc)
        return 0, [str(exc)]

    errors: list[str] = []
    for item in body.get("errors") or []:
        errors.append(str(item))

    sent = 0
    for ticket in body.get("data") or []:
        status = ticket.get("status")
        if status == "ok":
            sent += 1
            continue

        detail = ticket.get("message") or ticket.get("details") or ticket
        errors.append(str(detail))
        logger.warning("Expo push ticket error: %s", detail)

    if errors:
        logger.warning("Expo push errors: %s", errors)

    return sent, errors


def enviar_push_a_usuario(
    db: Session,
    usuario_id: int,
    titulo: str,
    cuerpo: str,
    data: dict | None = None,
) -> int:
    """Send push to all active device tokens if user has push enabled."""
    if not push_habilitado_para_usuario(db, usuario_id):
        return 0

    tokens = obtener_tokens_push_activos(db, usuario_id)
    if not tokens:
        return 0

    payload_data = {k: str(v) for k, v in (data or {}).items()}
    messages = [
        _build_message(token, titulo, cuerpo, payload_data)
        for token in tokens
    ]

    sent = 0
    for index in range(0, len(messages), 100):
        batch_sent, _ = _send_expo_messages(messages[index : index + 100])
        sent += batch_sent

    return sent


def enviar_push_prueba_usuario(db: Session, usuario_id: int) -> dict:
    """Send a test push notification to the user's registered devices."""
    if not push_habilitado_para_usuario(db, usuario_id):
        return {
            "push_enviados": 0,
            "mensaje": "Las notificaciones push están desactivadas en tu cuenta.",
        }

    tokens = obtener_tokens_push_activos(db, usuario_id)
    if not tokens:
        return {
            "push_enviados": 0,
            "mensaje": "No hay dispositivo registrado. Abre la app en tu teléfono con las notificaciones activadas.",
        }

    titulo = "Prueba Arenas 360"
    cuerpo = "Las notificaciones push funcionan correctamente."
    messages = [
        _build_message(token, titulo, cuerpo, {"type": "test"})
        for token in tokens
    ]

    sent = 0
    errors: list[str] = []
    for index in range(0, len(messages), 100):
        batch_sent, batch_errors = _send_expo_messages(messages[index : index + 100])
        sent += batch_sent
        errors.extend(batch_errors)

    if sent == 0:
        detail = errors[0] if errors else "Expo no pudo entregar la notificación."
        return {
            "push_enviados": 0,
            "mensaje": f"No se pudo entregar la push: {detail}",
        }

    mensaje = "Notificación de prueba enviada."
    if errors:
        mensaje = f"Notificación enviada con advertencias: {' · '.join(errors[:2])}"

    return {
        "push_enviados": sent,
        "mensaje": mensaje,
    }
