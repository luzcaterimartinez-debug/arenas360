"""Helpers for event API responses from the production schema."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.media import build_athlete_image_url, build_media_url
from backend.models import Escenario, Evento, EventoPrueba, Inscripcion
from backend.result_ids import make_result_id

MESES = (
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
)

MESES_LARGO = (
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
)

DIAS_SEMANA = (
    "LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO",
)

# UI filter values
ESTADO_UI_PROXIMO = "PRÓXIMO"
ESTADO_UI_EN_CURSO = "EN CURSO"
ESTADO_UI_FINALIZADO = "FINALIZADO"


def format_pruebas(count: int) -> str:
    if count == 1:
        return "1 PRUEBA"
    return f"{count} PRUEBAS"


def format_inscritos(count: int) -> str:
    if count == 1:
        return "1 INSCRITO"
    return f"{count} INSCRITOS"


def _format_fecha(d: date) -> str:
    return f"{d.day} DE {MESES[d.month - 1]}"


def format_fecha_larga(d: date | None) -> str:
    if not d:
        return "POR CONFIRMAR"
    return f"{d.day} DE {MESES_LARGO[d.month - 1]} {d.year}"


def format_fecha_corta_dia(d: date) -> str:
    nombre_dia = DIAS_SEMANA[d.weekday()]
    return f"{nombre_dia} {d.day} {MESES[d.month - 1]}"


def format_hora(t: time | None) -> str:
    if not t:
        return "—"
    return t.strftime("%H:%M")


def format_fecha_hora(val: datetime | date | None) -> str:
    if not val:
        return "POR CONFIRMAR"
    if isinstance(val, datetime):
        return f"{format_fecha_larga(val.date())}"
    return format_fecha_larga(val)


def resolve_city(escenario: Escenario | None) -> str:
    if not escenario:
        return "Por confirmar"
    parts = [p for p in (escenario.municipio, escenario.departamento) if p]
    return ", ".join(parts) if parts else "Por confirmar"


def format_fecha_display(evento: Evento, ui_status: str) -> str:
    if ui_status == ESTADO_UI_EN_CURSO:
        return "EN CURSO"

    inicio = evento.fecha_inicio
    fin = evento.fecha_fin or inicio

    if not inicio:
        return "FECHA POR CONFIRMAR"
    if fin and fin != inicio:
        return f"DEL {_format_fecha(inicio)} AL {_format_fecha(fin)}"
    return f"DEL {_format_fecha(inicio)}"


def resolve_ui_status(evento: Evento) -> tuple[str, str]:
    """Map DB status + dates to UI status and badge color."""
    today = date.today()
    db_estado = (evento.estado or "").upper()

    if db_estado == "FINALIZADO":
        return ESTADO_UI_FINALIZADO, "#64748B"

    if db_estado in ("INICIADO", "EN_CURSO", "EN CURSO"):
        return ESTADO_UI_EN_CURSO, "#10B981"

    if evento.fecha_inicio and evento.fecha_inicio > today:
        return ESTADO_UI_PROXIMO, "#208AEF"

    if evento.fecha_fin and evento.fecha_fin < today:
        return ESTADO_UI_FINALIZADO, "#64748B"

    if db_estado in ("PUBLICADO", "ABIERTO", "PROGRAMADO", "BORRADOR"):
        if evento.fecha_inicio and evento.fecha_inicio <= today <= (evento.fecha_fin or evento.fecha_inicio):
            return ESTADO_UI_EN_CURSO, "#10B981"
        return ESTADO_UI_PROXIMO, "#208AEF"

    return ESTADO_UI_PROXIMO, "#208AEF"


def resolve_location(evento: Evento, escenario: Escenario | None) -> str:
    if escenario and escenario.nombre:
        return escenario.nombre
    if escenario and escenario.municipio:
        return escenario.municipio
    return "Ubicación por confirmar"


def ui_status_matches_filter(ui_status: str, filtro: str | None) -> bool:
    if not filtro:
        return True
    return ui_status == filtro


def fetch_eventos(
    db: Session,
    tenant_id: int | None = None,
    estado_ui: str | None = None,
    api_base: str | None = None,
) -> list[dict]:
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
        .where(Evento.activo.is_(True))
        .order_by(Evento.fecha_inicio.desc().nullslast(), Evento.id.desc())
    )

    if tenant_id is not None:
        stmt = stmt.where(Evento.tenant_id == tenant_id)

    rows = db.execute(stmt).all()
    results: list[dict] = []

    for evento, escenario, num_pruebas, num_inscritos in rows:
        ui_status, _ = resolve_ui_status(evento)
        if not ui_status_matches_filter(ui_status, estado_ui):
            continue

        results.append(
            evento_row_to_response(
                evento,
                escenario,
                int(num_pruebas or 0),
                int(num_inscritos or 0),
                api_base=api_base,
            )
        )

    return results


def fetch_eventos_by_deporte(
    db: Session,
    deporte_id: int,
    api_base: str | None = None,
) -> list[dict]:
    query = text("""
        SELECT
            e.id,
            e.nombre,
            e.slug,
            e.descripcion,
            e.fecha_inicio,
            e.fecha_fin,
            e.fecha_inicio_inscripciones,
            e.fecha_fin_inscripciones,
            e.categoria_evento,
            e.estado,
            e.escenario_id,
            e.foto,
            e.activo,
            e.created_at,
            e.updated_at,
            e.tenant_id,
            s.nombre AS escenario_nombre,
            s.direccion AS escenario_direccion,
            s.municipio AS escenario_municipio,
            s.departamento AS escenario_departamento,
            s.activo AS escenario_activo,
            COUNT(DISTINCT ep.id) AS num_pruebas,
            COUNT(DISTINCT i.id) AS num_inscritos
        FROM eventos e
        JOIN evento_pruebas ep ON ep.evento_id = e.id AND ep.activo = true
        JOIN pruebas p ON p.id = ep.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        LEFT JOIN escenarios s ON s.id = e.escenario_id
        LEFT JOIN inscripciones i ON i.evento_id = e.id AND i.prueba_id = p.id
        WHERE e.activo = true
          AND (disc.deporte_id = :deporte_id OR p.deporte_id = :deporte_id)
        GROUP BY e.id, e.nombre, e.slug, e.descripcion, e.fecha_inicio, e.fecha_fin,
                 e.fecha_inicio_inscripciones, e.fecha_fin_inscripciones, e.categoria_evento,
                 e.estado, e.escenario_id, e.foto, e.activo, e.created_at, e.updated_at,
                 e.tenant_id, s.nombre, s.direccion, s.municipio, s.departamento, s.activo
        ORDER BY e.fecha_inicio DESC NULLS LAST, e.id DESC
    """)
    rows = db.execute(query, {"deporte_id": deporte_id}).mappings().all()
    results: list[dict] = []
    for row in rows:
        escenario = None
        if row["escenario_id"]:
            escenario = Escenario(
                id=row["escenario_id"],
                nombre=row["escenario_nombre"],
                direccion=row["escenario_direccion"],
                municipio=row["escenario_municipio"],
                departamento=row["escenario_departamento"],
                activo=bool(row["escenario_activo"]),
            )
        evento = Evento(
            id=row["id"],
            tenant_id=row["tenant_id"],
            nombre=row["nombre"],
            slug=row["slug"],
            descripcion=row["descripcion"],
            fecha_inicio=row["fecha_inicio"],
            fecha_fin=row["fecha_fin"],
            fecha_inicio_inscripciones=row["fecha_inicio_inscripciones"],
            fecha_fin_inscripciones=row["fecha_fin_inscripciones"],
            categoria_evento=row["categoria_evento"],
            estado=row["estado"],
            escenario_id=row["escenario_id"],
            foto=row["foto"],
            activo=row["activo"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        results.append(
            evento_row_to_response(
                evento,
                escenario,
                int(row["num_pruebas"] or 0),
                int(row["num_inscritos"] or 0),
                api_base=api_base,
            )
        )
    return results


def evento_row_to_response(
    evento: Evento,
    escenario: Escenario | None,
    num_pruebas: int,
    num_inscritos: int,
    api_base: str | None = None,
) -> dict:
    ui_status, status_color = resolve_ui_status(evento)
    return {
        "id": str(evento.id),
        "title": evento.nombre,
        "date": format_fecha_display(evento, ui_status),
        "status": ui_status,
        "statusColor": status_color,
        "image": build_media_url(evento.foto, api_base=api_base),
        "tests": format_pruebas(num_pruebas),
        "inscribed": format_inscritos(num_inscritos),
        "location": resolve_location(evento, escenario),
    }


def _format_categoria(genero: str | None, disciplina: str | None) -> str:
    genero_fmt = (genero or "").replace("_", " ").strip().title()
    disciplina_fmt = (disciplina or "").strip()
    if genero_fmt and disciplina_fmt:
        return f"{genero_fmt} · {disciplina_fmt}"
    return genero_fmt or disciplina_fmt or "General"


def _iter_event_days(inicio: date | None, fin: date | None) -> list[date]:
    if not inicio:
        return []
    end = fin or inicio
    if end < inicio:
        end = inicio
    days: list[date] = []
    current = inicio
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _day_label_for_key(day_key: str, evento: Evento) -> str:
    if day_key == "general":
        if evento.fecha_inicio and evento.fecha_fin and evento.fecha_fin != evento.fecha_inicio:
            return f"DEL {_format_fecha(evento.fecha_inicio)} AL {_format_fecha(evento.fecha_fin)}"
        if evento.fecha_inicio:
            return format_fecha_corta_dia(evento.fecha_inicio)
        return "CRONOGRAMA GENERAL"
    try:
        return format_fecha_corta_dia(date.fromisoformat(day_key))
    except ValueError:
        return day_key


def _sort_day_keys(keys: list[str]) -> list[str]:
    def sort_key(key: str):
        if key == "general":
            return (1, "")
        try:
            return (0, date.fromisoformat(key).isoformat())
        except ValueError:
            return (2, key)

    return sorted(keys, key=sort_key)


def fetch_cronograma(
    db: Session,
    evento_id: int,
    evento: Evento,
    escenario: Escenario | None,
) -> list[dict]:
    query = text("""
        SELECT
            ep.id AS evento_prueba_id,
            ep.fecha_inicio AS ep_fecha,
            ep.hora_inicio AS ep_hora,
            (
                SELECT MIN(fep.fecha_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_fecha,
            (
                SELECT MIN(fep.hora_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_hora,
            (
                SELECT COUNT(1)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS num_series,
            p.nombre AS prueba_nombre,
            p.genero_aplicable AS genero,
            disc.nombre AS disciplina,
            s.nombre AS escenario
        FROM evento_pruebas ep
        JOIN pruebas p ON p.id = ep.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        LEFT JOIN escenarios s ON s.id = ep.escenario_id
        WHERE ep.evento_id = :evento_id AND ep.activo = true
        ORDER BY
            COALESCE(
                (SELECT MIN(fep.fecha_programada) FROM fase_evento_pruebas fep
                 WHERE fep.evento_prueba_id = ep.id AND fep.activo = true),
                ep.fecha_inicio,
                :fecha_evento
            ) NULLS LAST,
            COALESCE(
                (SELECT MIN(fep.hora_programada) FROM fase_evento_pruebas fep
                 WHERE fep.evento_prueba_id = ep.id AND fep.activo = true),
                ep.hora_inicio
            ) NULLS LAST,
            p.nombre
    """)
    rows = db.execute(
        query,
        {"evento_id": evento_id, "fecha_evento": evento.fecha_inicio},
    ).mappings().all()

    if not rows:
        return []

    ubicacion_default = resolve_location(evento, escenario)
    grouped: dict[str, list[dict]] = defaultdict(list)
    unassigned: list[dict] = []

    for row in rows:
        fecha = row["fase_fecha"] or row["ep_fecha"]
        hora = row["fase_hora"] or row["ep_hora"]
        item = {
            "time": format_hora(hora),
            "event": row["prueba_nombre"] or "Prueba",
            "category": _format_categoria(row["genero"], row["disciplina"]),
            "pool": row["escenario"] or ubicacion_default,
            "heats": int(row["num_series"] or 0),
            "eventoPruebaId": str(row["evento_prueba_id"]),
            "_sort_hora": hora,
            "_sort_nombre": row["prueba_nombre"] or "",
        }

        if fecha:
            grouped[fecha.isoformat()].append(item)
        else:
            unassigned.append(item)

    if unassigned:
        event_days = _iter_event_days(evento.fecha_inicio, evento.fecha_fin)
        if len(event_days) <= 1:
            key = event_days[0].isoformat() if event_days else "general"
            grouped[key].extend(unassigned)
        else:
            for index, item in enumerate(unassigned):
                grouped[event_days[index % len(event_days)].isoformat()].append(item)

    schedule = []
    for idx, day_key in enumerate(_sort_day_keys(list(grouped.keys()))):
        events = grouped[day_key]
        events.sort(
            key=lambda e: (
                e["_sort_hora"] is None,
                e["_sort_hora"] or time(23, 59),
                e["_sort_nombre"],
            )
        )
        for event in events:
            event.pop("_sort_hora", None)
            event.pop("_sort_nombre", None)

        schedule.append({
            "id": str(idx + 1),
            "day": _day_label_for_key(day_key, evento),
            "events": events,
        })
    return schedule


def fetch_evento_cronograma(db: Session, evento_id: int, api_base: str | None = None) -> dict | None:
    stmt = (
        select(Evento, Escenario)
        .outerjoin(Escenario, Evento.escenario_id == Escenario.id)
        .where(Evento.id == evento_id, Evento.activo.is_(True))
    )
    row = db.execute(stmt).first()
    if not row:
        return None

    evento, escenario = row
    schedule = fetch_cronograma(db, evento_id, evento, escenario)
    total_pruebas = sum(len(day["events"]) for day in schedule)

    return {
        "id": str(evento.id),
        "eventName": evento.nombre,
        "startDate": format_fecha_larga(evento.fecha_inicio),
        "endDate": format_fecha_larga(evento.fecha_fin or evento.fecha_inicio),
        "location": resolve_location(evento, escenario),
        "status": resolve_ui_status(evento)[0],
        "image": build_media_url(evento.foto, api_base=api_base),
        "totalPruebas": total_pruebas,
        "totalDays": len(schedule),
        "schedule": schedule,
    }


def fetch_competencia_detalle(db: Session, evento_prueba_id: int) -> dict | None:
    query = text("""
        SELECT
            ep.id AS evento_prueba_id,
            ep.evento_id,
            ep.prueba_id,
            ep.fecha_inicio AS ep_fecha,
            ep.hora_inicio AS ep_hora,
            (
                SELECT MIN(fep.fecha_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_fecha,
            (
                SELECT MIN(fep.hora_programada)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS fase_hora,
            (
                SELECT COUNT(1)
                FROM fase_evento_pruebas fep
                WHERE fep.evento_prueba_id = ep.id AND fep.activo = true
            ) AS num_series,
            e.nombre AS evento_nombre,
            e.escenario_id AS evento_escenario_id,
            p.nombre AS prueba_nombre,
            p.genero_aplicable AS genero,
            disc.nombre AS disciplina,
            s.nombre AS escenario_nombre
        FROM evento_pruebas ep
        JOIN eventos e ON e.id = ep.evento_id
        JOIN pruebas p ON p.id = ep.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        LEFT JOIN escenarios s ON s.id = ep.escenario_id
        WHERE ep.id = :evento_prueba_id
          AND ep.activo = true
          AND e.activo = true
        LIMIT 1
    """)

    row = db.execute(query, {"evento_prueba_id": evento_prueba_id}).mappings().first()
    if not row:
        return None

    phases_query = text("""
        SELECT
            fep.id,
            fep.fecha_programada AS fecha,
            fep.hora_programada AS hora
        FROM fase_evento_pruebas fep
        WHERE fep.evento_prueba_id = :evento_prueba_id
          AND fep.activo = true
        ORDER BY fep.fecha_programada NULLS LAST, fep.hora_programada NULLS LAST, fep.id
    """)
    phase_rows = db.execute(phases_query, {"evento_prueba_id": evento_prueba_id}).mappings().all()
    phases = [
        {
            "id": str(p["id"]),
            "date": format_fecha_larga(p["fecha"]),
            "time": format_hora(p["hora"]),
        }
        for p in phase_rows
    ]

    fecha = row["fase_fecha"] or row["ep_fecha"]
    hora = row["fase_hora"] or row["ep_hora"]
    num_series = int(row["num_series"] or 0)

    category = _format_categoria(row.get("genero"), row.get("disciplina"))
    evento_id = int(row["evento_id"])
    prueba_id = int(row["prueba_id"])

    has_results = db.execute(
        text("""
            SELECT 1
            FROM resultados r
            JOIN inscripciones i ON i.id = r.inscripcion_id
            WHERE i.evento_id = :evento_id
              AND i.prueba_id = :prueba_id
              AND r.estado IN ('VALIDADO', 'OFICIAL')
              AND r.descalificado = false
            LIMIT 1
        """),
        {"evento_id": evento_id, "prueba_id": prueba_id},
    ).first() is not None
    result_id = make_result_id(evento_id, prueba_id) if has_results else None

    return {
        "id": str(row["evento_prueba_id"]),
        "eventoId": str(row["evento_id"]),
        "eventoNombre": row.get("evento_nombre") or "Evento",
        "prueba": row.get("prueba_nombre") or "Prueba",
        "category": category,
        "date": format_fecha_larga(fecha),
        "time": format_hora(hora),
        "location": row.get("escenario_nombre") or "Por confirmar",
        "heats": num_series,
        "phases": phases,
        "resultId": result_id,
    }


def fetch_atletas_inscritos(db: Session, evento_id: int, api_base: str | None = None) -> list[dict]:
    query = text("""
        SELECT
            d.id,
            per.foto,
            TRIM(CONCAT(
                COALESCE(per.primer_nombre, ''),
                ' ',
                COALESCE(per.segundo_nombre, ''),
                ' ',
                COALESCE(per.primer_apellido, ''),
                ' ',
                COALESCE(per.segundo_apellido, '')
            )) AS nombre,
            COUNT(DISTINCT i.prueba_id) AS num_pruebas,
            MAX(ce.nombre) AS categoria,
            MAX(disc.nombre) AS disciplina,
            MAX(eq.nombre) AS club
        FROM inscripciones i
        JOIN deportistas d ON d.id = i.deportista_id
        JOIN personas per ON per.id = d.persona_id
        LEFT JOIN categorias_edad ce ON ce.id = i.categoria_edad_id
        LEFT JOIN pruebas pr ON pr.id = i.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = pr.disciplina_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE i.evento_id = :evento_id
        GROUP BY d.id, per.foto, per.primer_nombre, per.segundo_nombre,
                 per.primer_apellido, per.segundo_apellido
        ORDER BY nombre
        LIMIT 50
    """)
    rows = db.execute(query, {"evento_id": evento_id}).mappings().all()
    athletes = []
    for row in rows:
        nombre = " ".join((row["nombre"] or "").split()).upper()
        athletes.append({
            "id": str(row["id"]),
            "name": nombre or "ATLETA",
            "club": row["club"] or "Sin club",
            "category": (row["categoria"] or "General").upper(),
            "image": build_athlete_image_url(row.get("foto"), api_base=api_base),
            "events": int(row["num_pruebas"] or 0),
            "specialty": row["disciplina"] or "—",
        })
    return athletes


def fetch_evento_detalle(db: Session, evento_id: int, api_base: str | None = None) -> dict | None:
    pruebas_sq = (
        select(func.count(EventoPrueba.id))
        .where(EventoPrueba.evento_id == Evento.id)
        .correlate(Evento)
        .scalar_subquery()
    )
    inscritos_sq = (
        select(func.count(func.distinct(Inscripcion.deportista_id)))
        .where(Inscripcion.evento_id == Evento.id)
        .correlate(Evento)
        .scalar_subquery()
    )

    stmt = (
        select(Evento, Escenario, pruebas_sq.label("num_pruebas"), inscritos_sq.label("num_inscritos"))
        .outerjoin(Escenario, Evento.escenario_id == Escenario.id)
        .where(Evento.id == evento_id, Evento.activo.is_(True))
    )

    row = db.execute(stmt).first()
    if not row:
        return None

    evento, escenario, num_pruebas, num_inscritos = row
    base = evento_row_to_response(
        evento,
        escenario,
        int(num_pruebas or 0),
        int(num_inscritos or 0),
        api_base=api_base,
    )
    descripcion = (evento.descripcion or "").strip()
    if not descripcion:
        descripcion = (
            f"{evento.nombre} es un evento deportivo organizado en la plataforma Arenas 360. "
            "Consulta el cronograma y los atletas inscritos para más información."
        )

    return {
        **base,
        "startDate": format_fecha_larga(evento.fecha_inicio),
        "endDate": format_fecha_larga(evento.fecha_fin or evento.fecha_inicio),
        "city": resolve_city(escenario),
        "description": descripcion,
        "totalEvents": int(num_pruebas or 0),
        "totalAthletes": int(num_inscritos or 0),
        "totalCountries": 1,
        "organizer": evento.categoria_evento or "Arenas 360",
        "contact": "Por confirmar",
        "email": "Por confirmar",
        "website": "Por confirmar",
        "registrationDeadline": format_fecha_hora(evento.fecha_fin_inscripciones),
        "registrationFee": "Por confirmar",
        "inscribed": int(num_inscritos or 0),
        "schedule": fetch_cronograma(db, evento_id, evento, escenario),
        "athletes": fetch_atletas_inscritos(db, evento_id, api_base=api_base),
    }
