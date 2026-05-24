"""Detect newly published results and notify followers."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models import ResultadoNotificacionTracker
from backend import seguimientos as seguimientos_service

logger = logging.getLogger(__name__)

OFFICIAL_RESULT_STATES = ("VALIDADO", "OFICIAL")


def _fetch_official_result_groups(db: Session) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT
                i.evento_id,
                i.prueba_id,
                MAX(r.created_at) AS ultimo_resultado_at
            FROM resultados r
            JOIN inscripciones i ON i.id = r.inscripcion_id
            WHERE r.estado IN ('VALIDADO', 'OFICIAL')
              AND r.descalificado = false
            GROUP BY i.evento_id, i.prueba_id
            """
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _get_tracker(db: Session, evento_id: int, prueba_id: int) -> ResultadoNotificacionTracker | None:
    stmt = select(ResultadoNotificacionTracker).where(
        ResultadoNotificacionTracker.evento_id == evento_id,
        ResultadoNotificacionTracker.prueba_id == prueba_id,
    )
    return db.execute(stmt).scalars().first()


def _upsert_tracker(db: Session, evento_id: int, prueba_id: int, ultimo_resultado_at: datetime) -> None:
    tracker = _get_tracker(db, evento_id, prueba_id)
    if tracker:
        tracker.ultimo_resultado_at = ultimo_resultado_at
    else:
        db.add(
            ResultadoNotificacionTracker(
                evento_id=evento_id,
                prueba_id=prueba_id,
                ultimo_resultado_at=ultimo_resultado_at,
            )
        )
    db.commit()


def notificar_resultados_publicados(
    db: Session,
    evento_id: int,
    prueba_id: int,
    *,
    update_tracker: bool = True,
) -> dict:
    """Notify followers of event, test or athletes with official results."""
    ultimo_row = db.execute(
        text(
            """
            SELECT MAX(r.created_at) AS ultimo_resultado_at
            FROM resultados r
            JOIN inscripciones i ON i.id = r.inscripcion_id
            WHERE i.evento_id = :evento_id
              AND i.prueba_id = :prueba_id
              AND r.estado IN ('VALIDADO', 'OFICIAL')
              AND r.descalificado = false
            """
        ),
        {"evento_id": evento_id, "prueba_id": prueba_id},
    ).mappings().first()

    if not ultimo_row or not ultimo_row.get("ultimo_resultado_at"):
        return {"usuarios_notificados": 0, "push_enviados": 0}

    stats = seguimientos_service.notificar_resultados_a_seguidores(db, evento_id, prueba_id)

    if update_tracker:
        _upsert_tracker(db, evento_id, prueba_id, ultimo_row["ultimo_resultado_at"])

    return stats


def procesar_notificaciones_resultados_nuevos(db: Session) -> dict:
    """
    Scan official results and notify when new ones appear.
    On first sight of an event+test pair, only records the timestamp (no alert).
    """
    processed = 0
    total_users = 0
    total_push = 0

    for group in _fetch_official_result_groups(db):
        evento_id = int(group["evento_id"])
        prueba_id = int(group["prueba_id"])
        ultimo = group["ultimo_resultado_at"]

        if ultimo.tzinfo is None:
            ultimo = ultimo.replace(tzinfo=timezone.utc)

        tracker = _get_tracker(db, evento_id, prueba_id)
        if tracker is None:
            _upsert_tracker(db, evento_id, prueba_id, ultimo)
            logger.debug(
                "Tracker inicializado evento=%s prueba=%s (sin notificar)",
                evento_id,
                prueba_id,
            )
            continue

        tracker_ts = tracker.ultimo_resultado_at
        if tracker_ts.tzinfo is None:
            tracker_ts = tracker_ts.replace(tzinfo=timezone.utc)

        if ultimo <= tracker_ts:
            continue

        stats = notificar_resultados_publicados(
            db,
            evento_id,
            prueba_id,
            update_tracker=True,
        )
        processed += 1
        total_users += stats["usuarios_notificados"]
        total_push += stats["push_enviados"]
        logger.info(
            "Notificados resultados evento=%s prueba=%s usuarios=%s push=%s",
            evento_id,
            prueba_id,
            stats["usuarios_notificados"],
            stats["push_enviados"],
        )

    return {
        "procesadas": processed,
        "usuarios_notificados": total_users,
        "push_enviados": total_push,
    }
