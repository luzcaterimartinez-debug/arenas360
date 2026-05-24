import json
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.atletas import (
    _athlete_name,
    _category_color,
    _compute_rating,
    _format_specialty,
    _pick_best_mark,
)
from backend.eventos import (
    _format_categoria,
    evento_row_to_response,
    format_fecha_larga,
    format_hora,
)
from backend.media import build_athlete_image_url, build_media_url
from backend.models import (
    Escenario,
    Evento,
    EventoPrueba,
    Inscripcion,
    Notificacion,
    TipoNotificacion,
    TipoSeguimiento,
    Usuario,
    UsuarioSeguimiento,
)

TIPO_ICON = {
    TipoNotificacion.event: ("calendar-outline", "#FF9F1C"),
    TipoNotificacion.result: ("stats-chart-outline", "#208AEF"),
    TipoNotificacion.system: ("person-outline", "#8B5CF6"),
}


def _format_relative_date(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now.date() - dt.date()
    if delta.days == 0:
        return "HOY"
    if delta.days == 1:
        return "AYER"
    return dt.strftime("%d DE %B").upper()


def _format_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%H:%M")


def _parse_payload(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _serialize_notificacion(item: Notificacion) -> dict:
    icon, color = TIPO_ICON.get(item.tipo, ("notifications-outline", "#94A3B8"))
    return {
        "id": str(item.id),
        "title": item.titulo,
        "description": item.descripcion,
        "type": item.tipo.value,
        "date": _format_relative_date(item.created_at),
        "time": _format_time(item.created_at),
        "read": item.leida,
        "icon": icon,
        "color": color,
        "payload": _parse_payload(item.payload),
    }


def crear_notificacion(
    db: Session,
    usuario_id: int,
    tipo: TipoNotificacion,
    titulo: str,
    descripcion: str,
    payload: dict | None = None,
) -> Notificacion:
    item = Notificacion(
        usuario_id=usuario_id,
        tipo=tipo,
        titulo=titulo,
        descripcion=descripcion,
        payload=json.dumps(payload) if payload else None,
        leida=False,
    )
    db.add(item)
    return item


def _enviar_notificacion_y_push(
    db: Session,
    usuario_id: int,
    tipo: TipoNotificacion,
    titulo: str,
    descripcion: str,
    payload: dict | None = None,
) -> int:
    crear_notificacion(db, usuario_id, tipo, titulo, descripcion, payload)
    db.commit()

    from backend.push import enviar_push_a_usuario

    return enviar_push_a_usuario(db, usuario_id, titulo, descripcion, payload)


def list_notificaciones(db: Session, usuario_id: int, tipo: str | None = None) -> dict:
    stmt = (
        select(Notificacion)
        .where(Notificacion.usuario_id == usuario_id)
        .order_by(Notificacion.created_at.desc())
    )
    if tipo and tipo in {"event", "result", "system"}:
        stmt = stmt.where(Notificacion.tipo == TipoNotificacion(tipo))

    items = db.execute(stmt).scalars().all()
    serialized = [_serialize_notificacion(item) for item in items]
    no_leidas = sum(1 for item in items if not item.leida)
    return {
        "items": serialized,
        "resumen": {"no_leidas": no_leidas},
    }


def marcar_leida(db: Session, usuario_id: int, notificacion_id: int) -> bool:
    stmt = select(Notificacion).where(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    )
    item = db.execute(stmt).scalars().first()
    if not item:
        return False
    item.leida = True
    db.commit()
    return True


def marcar_todas_leidas(db: Session, usuario_id: int) -> int:
    stmt = select(Notificacion).where(
        Notificacion.usuario_id == usuario_id,
        Notificacion.leida.is_(False),
    )
    items = db.execute(stmt).scalars().all()
    for item in items:
        item.leida = True
    db.commit()
    return len(items)


def resumen_notificaciones(db: Session, usuario_id: int) -> dict:
    stmt = select(Notificacion).where(
        Notificacion.usuario_id == usuario_id,
        Notificacion.leida.is_(False),
    )
    count = len(db.execute(stmt).scalars().all())
    return {"no_leidas": count}


def _resolve_entity_label(db: Session, tipo: TipoSeguimiento, entidad_id: int) -> str:
    if tipo == TipoSeguimiento.EVENTO:
        evento = db.get(Evento, entidad_id)
        return evento.nombre if evento else "este evento"

    if tipo == TipoSeguimiento.ATLETA:
        row = db.execute(
            text(
                """
                SELECT TRIM(CONCAT_WS(' ', per.primer_nombre, per.segundo_nombre, per.primer_apellido, per.segundo_apellido)) AS nombre
                FROM deportistas d
                JOIN personas per ON per.id = d.persona_id
                WHERE d.id = :deportista_id
                LIMIT 1
                """
            ),
            {"deportista_id": entidad_id},
        ).mappings().first()
        return row["nombre"] if row and row.get("nombre") else "este atleta"

    row = db.execute(
        text(
            """
            SELECT COALESCE(p.nombre, CONCAT('Prueba #', ep.id)) AS nombre
            FROM evento_pruebas ep
            LEFT JOIN pruebas p ON p.id = ep.prueba_id
            WHERE ep.id = :evento_prueba_id
            LIMIT 1
            """
        ),
        {"evento_prueba_id": entidad_id},
    ).mappings().first()
    return row["nombre"] if row and row.get("nombre") else "esta prueba"


def _payload_for_tipo(tipo: TipoSeguimiento, entidad_id: int, db: Session) -> dict:
    if tipo == TipoSeguimiento.ATLETA:
        return {"athleteId": str(entidad_id)}
    if tipo == TipoSeguimiento.EVENTO:
        return {"eventId": str(entidad_id)}
    row = db.execute(
        text("SELECT evento_id FROM evento_pruebas WHERE id = :id LIMIT 1"),
        {"id": entidad_id},
    ).mappings().first()
    payload = {"eventoPruebaId": str(entidad_id)}
    if row and row.get("evento_id"):
        payload["eventId"] = str(row["evento_id"])
    return payload


def get_follow_state(db: Session, usuario_id: int, tipo: TipoSeguimiento, entidad_id: int) -> bool:
    stmt = select(UsuarioSeguimiento).where(
        UsuarioSeguimiento.usuario_id == usuario_id,
        UsuarioSeguimiento.tipo == tipo,
        UsuarioSeguimiento.entidad_id == entidad_id,
    )
    return db.execute(stmt).scalars().first() is not None


def get_follow_states(
    db: Session,
    usuario_id: int,
    tipo: TipoSeguimiento,
    entidad_ids: list[int],
) -> dict[str, bool]:
    if not entidad_ids:
        return {}
    stmt = select(UsuarioSeguimiento.entidad_id).where(
        UsuarioSeguimiento.usuario_id == usuario_id,
        UsuarioSeguimiento.tipo == tipo,
        UsuarioSeguimiento.entidad_id.in_(entidad_ids),
    )
    followed_ids = {row for row in db.execute(stmt).scalars().all()}
    return {str(entity_id): entity_id in followed_ids for entity_id in entidad_ids}


def toggle_seguimiento(
    db: Session,
    usuario: Usuario,
    tipo: TipoSeguimiento,
    entidad_id: int,
) -> bool:
    stmt = select(UsuarioSeguimiento).where(
        UsuarioSeguimiento.usuario_id == usuario.id,
        UsuarioSeguimiento.tipo == tipo,
        UsuarioSeguimiento.entidad_id == entidad_id,
    )
    existing = db.execute(stmt).scalars().first()

    if existing:
        db.delete(existing)
        db.commit()
        return False

    db.add(
        UsuarioSeguimiento(
            usuario_id=usuario.id,
            tipo=tipo,
            entidad_id=entidad_id,
        )
    )

    label = _resolve_entity_label(db, tipo, entidad_id)
    payload = _payload_for_tipo(tipo, entidad_id, db)
    notif_tipo = TipoNotificacion.event if tipo in {TipoSeguimiento.EVENTO, TipoSeguimiento.PRUEBA} else TipoNotificacion.system

    if tipo == TipoSeguimiento.ATLETA:
        titulo = f"Ahora sigues a {label}"
        descripcion = "Recibirás avisos cuando haya nuevos resultados de este atleta."
    elif tipo == TipoSeguimiento.EVENTO:
        titulo = f"Ahora sigues {label}"
        descripcion = "Te avisaremos sobre cambios y resultados de este evento."
    else:
        titulo = f"Ahora sigues {label}"
        descripcion = "Te avisaremos cuando se publiquen resultados de esta prueba."

    crear_notificacion(
        db,
        usuario.id,
        notif_tipo,
        titulo,
        descripcion,
        payload,
    )
    db.commit()

    from backend.push import enviar_push_a_usuario

    enviar_push_a_usuario(db, usuario.id, titulo, descripcion, payload)
    return True


def _obtener_seguidores_resultado(db: Session, evento_id: int, prueba_id: int) -> set[int]:
    """Users following the event, the scheduled test, or athletes with official results."""
    evento_prueba_row = db.execute(
        text(
            """
            SELECT ep.id AS evento_prueba_id
            FROM evento_pruebas ep
            WHERE ep.evento_id = :evento_id
              AND ep.prueba_id = :prueba_id
              AND ep.activo = true
            LIMIT 1
            """
        ),
        {"evento_id": evento_id, "prueba_id": prueba_id},
    ).mappings().first()

    evento_prueba_id = evento_prueba_row["evento_prueba_id"] if evento_prueba_row else None

    params: dict = {"evento_id": evento_id, "prueba_id": prueba_id}
    prueba_filter = ""
    if evento_prueba_id is not None:
        prueba_filter = "UNION SELECT DISTINCT usuario_id FROM usuario_seguimientos WHERE tipo = 'PRUEBA' AND entidad_id = :evento_prueba_id"
        params["evento_prueba_id"] = evento_prueba_id

    rows = db.execute(
        text(
            f"""
            SELECT DISTINCT usuario_id FROM (
                SELECT DISTINCT usuario_id
                FROM usuario_seguimientos
                WHERE tipo = 'EVENTO' AND entidad_id = :evento_id
                {prueba_filter}
                UNION
                SELECT DISTINCT us.usuario_id
                FROM usuario_seguimientos us
                JOIN inscripciones i
                  ON i.deportista_id = us.entidad_id
                 AND us.tipo = 'ATLETA'
                JOIN resultados r ON r.inscripcion_id = i.id
                WHERE i.evento_id = :evento_id
                  AND i.prueba_id = :prueba_id
                  AND r.estado IN ('VALIDADO', 'OFICIAL')
                  AND r.descalificado = false
            ) seguidores
            """
        ),
        params,
    ).scalars().all()

    return set(rows)


def notificar_resultados_a_seguidores(db: Session, evento_id: int, prueba_id: int) -> dict:
    """Create in-app notifications and push alerts for new official results."""
    from backend.result_ids import make_result_id

    meta = db.execute(
        text(
            """
            SELECT
                e.nombre AS evento_nombre,
                p.nombre AS prueba_nombre,
                ep.id AS evento_prueba_id
            FROM eventos e
            JOIN pruebas p ON p.id = :prueba_id
            LEFT JOIN evento_pruebas ep
              ON ep.evento_id = e.id
             AND ep.prueba_id = :prueba_id
             AND ep.activo = true
            WHERE e.id = :evento_id
            LIMIT 1
            """
        ),
        {"evento_id": evento_id, "prueba_id": prueba_id},
    ).mappings().first()

    if not meta:
        return {"usuarios_notificados": 0, "push_enviados": 0}

    evento_nombre = meta.get("evento_nombre") or "Evento"
    prueba_nombre = meta.get("prueba_nombre") or "Prueba"
    result_id = make_result_id(evento_id, prueba_id)

    payload: dict = {
        "resultId": result_id,
        "eventId": str(evento_id),
    }
    if meta.get("evento_prueba_id"):
        payload["eventoPruebaId"] = str(meta["evento_prueba_id"])

    titulo = f"Nuevos resultados: {prueba_nombre}"
    descripcion = f"Se publicaron resultados oficiales de {prueba_nombre} en {evento_nombre}."

    seguidores = _obtener_seguidores_resultado(db, evento_id, prueba_id)
    push_enviados = 0

    for usuario_id in seguidores:
        push_enviados += _enviar_notificacion_y_push(
            db,
            usuario_id,
            TipoNotificacion.result,
            titulo,
            descripcion,
            payload,
        )

    return {
        "usuarios_notificados": len(seguidores),
        "push_enviados": push_enviados,
    }


def _followed_at_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _fetch_eventos_seguidos(
    db: Session,
    evento_ids: list[int],
    follow_dates: dict[tuple[str, int], datetime],
    api_base: str | None,
) -> list[dict]:
    if not evento_ids:
        return []

    pruebas_sq = (
        select(func.count(EventoPrueba.id))
        .where(EventoPrueba.evento_id == Evento.id)
        .correlate(Evento)
        .scalar_subquery()
    )
    inscritos_sq = (
        select(func.count(Inscripcion.id))
        .where(Inscripcion.evento_id == Evento.id)
        .correlate(Evento)
        .scalar_subquery()
    )

    stmt = (
        select(Evento, Escenario, pruebas_sq.label("num_pruebas"), inscritos_sq.label("num_inscritos"))
        .outerjoin(Escenario, Evento.escenario_id == Escenario.id)
        .where(Evento.id.in_(evento_ids), Evento.activo.is_(True))
    )
    rows = db.execute(stmt).all()
    items: list[dict] = []

    for evento, escenario, num_pruebas, num_inscritos in rows:
        item = evento_row_to_response(
            evento,
            escenario,
            int(num_pruebas or 0),
            int(num_inscritos or 0),
            api_base=api_base,
        )
        item["followedAt"] = _followed_at_iso(follow_dates[(TipoSeguimiento.EVENTO.value, evento.id)])
        items.append(item)

    items.sort(key=lambda row: row["followedAt"], reverse=True)
    return items


def _fetch_atletas_seguidos(
    db: Session,
    atleta_ids: list[int],
    follow_dates: dict[tuple[str, int], datetime],
    api_base: str | None,
) -> list[dict]:
    if not atleta_ids:
        return []

    query = text("""
        SELECT
            d.id,
            d.activo,
            d.created_at,
            per.foto,
            dep.nombre AS deporte,
            disc.nombre AS disciplina,
            TRIM(CONCAT_WS(' ', per.primer_nombre, per.segundo_nombre, per.primer_apellido, per.segundo_apellido)) AS nombre,
            MAX(ce.nombre) AS categoria,
            MAX(eq.nombre) AS club,
            COUNT(DISTINCT CASE WHEN r.posicion IN (1,2,3) THEN r.id END) AS podios,
            COUNT(DISTINCT CASE WHEN r.posicion = 1 THEN r.id END) AS oros,
            COUNT(DISTINCT CASE WHEN r.posicion = 2 THEN r.id END) AS platas,
            COUNT(DISTINCT CASE WHEN r.posicion = 3 THEN r.id END) AS bronces,
            COUNT(DISTINCT CASE WHEN r.codigo_irm IS NOT NULL AND r.codigo_irm <> '' THEN r.id END) AS records,
            COUNT(DISTINCT i.id) AS participaciones,
            MIN(r.valor_tiempo) AS best_tiempo,
            MIN(r.valor_numerico) AS best_distancia
        FROM deportistas d
        JOIN personas per ON per.id = d.persona_id
        LEFT JOIN inscripciones i ON i.deportista_id = d.id
        LEFT JOIN resultados r ON r.inscripcion_id = i.id
            AND r.estado IN ('VALIDADO', 'OFICIAL') AND r.descalificado = false
        LEFT JOIN categorias_edad ce ON ce.id = i.categoria_edad_id
        LEFT JOIN pruebas p ON p.id = i.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        LEFT JOIN deportes dep ON dep.id = disc.deporte_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE d.id = ANY(:ids)
        GROUP BY d.id, d.activo, d.created_at, per.foto, dep.nombre, disc.nombre,
                 per.primer_nombre, per.segundo_nombre, per.primer_apellido, per.segundo_apellido
    """)
    rows = db.execute(query, {"ids": atleta_ids}).mappings().all()
    items: list[dict] = []

    for row in rows:
        athlete_id = int(row["id"])
        oros = int(row.get("oros") or 0)
        platas = int(row.get("platas") or 0)
        bronces = int(row.get("bronces") or 0)
        podios = int(row.get("podios") or 0)
        participaciones = int(row.get("participaciones") or 0)
        categoria = (row.get("categoria") or "General").upper()
        activo = bool(row.get("activo"))
        items.append(
            {
                "id": str(athlete_id),
                "name": _athlete_name(row),
                "specialty": _format_specialty(row.get("deporte"), row.get("disciplina")),
                "category": categoria,
                "categoryColor": _category_color(categoria),
                "image": build_athlete_image_url(row.get("foto"), api_base=api_base),
                "medals": str(oros + platas + bronces),
                "records": str(int(row.get("records") or 0)),
                "status": "ACTIVO" if activo else "INACTIVO",
                "statusColor": "#10B981" if activo else "#EF4444",
                "bestTime": _pick_best_mark(row.get("best_tiempo"), row.get("best_distancia")),
                "club": row.get("club") or "Sin club",
                "rating": _compute_rating(podios, participaciones, oros),
                "isNew": False,
                "followedAt": _followed_at_iso(follow_dates[(TipoSeguimiento.ATLETA.value, athlete_id)]),
            }
        )

    items.sort(key=lambda row: row["followedAt"], reverse=True)
    return items


def _fetch_pruebas_seguidas(
    db: Session,
    prueba_ids: list[int],
    follow_dates: dict[tuple[str, int], datetime],
    api_base: str | None,
) -> list[dict]:
    if not prueba_ids:
        return []

    query = text("""
        SELECT
            ep.id AS evento_prueba_id,
            ep.evento_id,
            ep.fecha_inicio AS ep_fecha,
            ep.hora_inicio AS ep_hora,
            e.nombre AS evento_nombre,
            p.nombre AS prueba_nombre,
            p.genero_aplicable AS genero,
            disc.nombre AS disciplina,
            (
                SELECT MIN(fep.fecha_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_fecha,
            (
                SELECT MIN(fep.hora_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_hora
        FROM evento_pruebas ep
        JOIN eventos e ON e.id = ep.evento_id
        JOIN pruebas p ON p.id = ep.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        WHERE ep.id = ANY(:ids)
          AND ep.activo = true
          AND e.activo = true
    """)
    rows = db.execute(query, {"ids": prueba_ids}).mappings().all()
    items: list[dict] = []

    for row in rows:
        evento_prueba_id = int(row["evento_prueba_id"])
        fecha = row.get("fase_fecha") or row.get("ep_fecha")
        hora = row.get("fase_hora") or row.get("ep_hora")
        items.append(
            {
                "id": str(evento_prueba_id),
                "eventoId": str(row["evento_id"]),
                "title": row.get("prueba_nombre") or "Prueba",
                "subtitle": row.get("evento_nombre") or "Evento",
                "category": _format_categoria(row.get("genero"), row.get("disciplina")),
                "date": format_fecha_larga(fecha),
                "time": format_hora(hora),
                "followedAt": _followed_at_iso(follow_dates[(TipoSeguimiento.PRUEBA.value, evento_prueba_id)]),
            }
        )

    items.sort(key=lambda row: row["followedAt"], reverse=True)
    return items


def list_mis_seguidos(db: Session, usuario_id: int, api_base: str | None = None) -> dict:
    """Return all events, athletes and competitions followed by the user."""
    stmt = (
        select(UsuarioSeguimiento)
        .where(UsuarioSeguimiento.usuario_id == usuario_id)
        .order_by(UsuarioSeguimiento.created_at.desc())
    )
    seguimientos = db.execute(stmt).scalars().all()

    if not seguimientos:
        return {
            "eventos": [],
            "atletas": [],
            "pruebas": [],
            "resumen": {"total": 0, "eventos": 0, "atletas": 0, "pruebas": 0},
        }

    follow_dates: dict[tuple[str, int], datetime] = {}
    evento_ids: list[int] = []
    atleta_ids: list[int] = []
    prueba_ids: list[int] = []

    for item in seguimientos:
        follow_dates[(item.tipo.value, item.entidad_id)] = item.created_at
        if item.tipo == TipoSeguimiento.EVENTO:
            evento_ids.append(item.entidad_id)
        elif item.tipo == TipoSeguimiento.ATLETA:
            atleta_ids.append(item.entidad_id)
        else:
            prueba_ids.append(item.entidad_id)

    eventos = _fetch_eventos_seguidos(db, evento_ids, follow_dates, api_base)
    atletas = _fetch_atletas_seguidos(db, atleta_ids, follow_dates, api_base)
    pruebas = _fetch_pruebas_seguidas(db, prueba_ids, follow_dates, api_base)

    return {
        "eventos": eventos,
        "atletas": atletas,
        "pruebas": pruebas,
        "resumen": {
            "total": len(eventos) + len(atletas) + len(pruebas),
            "eventos": len(eventos),
            "atletas": len(atletas),
            "pruebas": len(pruebas),
        },
    }
