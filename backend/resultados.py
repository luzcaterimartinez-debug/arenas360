"""Results API helpers — podiums and marks from PostgreSQL."""

from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.eventos import (
    MESES,
    format_fecha_larga,
    resolve_location,
)
from backend.media import build_athlete_image_url
from backend.models import Escenario, Evento

from backend.result_ids import make_result_id, parse_result_id

VALID_ESTADOS = ("VALIDADO", "OFICIAL")



def _format_fecha_corta(d: date | None) -> str:
    if not d:
        return "—"
    return f"{d.day} DE {MESES[d.month - 1]} {d.year}"


def _format_categoria(genero: str | None, categoria: str | None, disciplina: str | None) -> str:
    genero_fmt = (genero or "").replace("_", " ").strip().title()
    parts = [p for p in (genero_fmt, categoria, disciplina) if p]
    return " · ".join(parts) if parts else "General"


def format_mark(
    valor_tiempo: timedelta | None,
    valor_numerico,
    unidad_resultado: str | None,
) -> str:
    if valor_tiempo is not None:
        total = valor_tiempo.total_seconds()
        if total >= 60:
            minutes = int(total // 60)
            seconds = total % 60
            return f"{minutes}:{seconds:05.2f}"
        return f"{total:.2f}s"
    if valor_numerico is not None:
        value = float(valor_numerico)
        unit = (unidad_resultado or "").upper()
        if unit == "DISTANCIA":
            return f"{value:.2f}m"
        if unit == "PUNTOS":
            return f"{value:.0f} pts"
        return f"{value:.2f}"
    return "—"


def _athlete_name(row: dict) -> str:
    name = " ".join((row.get("atleta") or "").split())
    return name.upper() if name else "ATLETA"


def _row_to_podium_member(row: dict, api_base: str | None = None) -> dict:
    is_record = bool(row.get("codigo_irm"))
    return {
        "position": int(row["posicion"]),
        "athlete": _athlete_name(row),
        "club": row.get("club") or "Sin club",
        "time": format_mark(
            row.get("valor_tiempo"),
            row.get("valor_numerico"),
            row.get("unidad_resultado"),
        ),
        "image": build_athlete_image_url(row.get("foto"), api_base=api_base),
        "isRecord": is_record,
        "recordType": row.get("codigo_irm") if is_record else None,
        "deportistaId": str(row["deportista_id"]) if row.get("deportista_id") else None,
    }


def _event_tenant_clause(tenant_id: int | None) -> tuple[str, dict]:
    if tenant_id is None:
        return "", {}
    return "AND e.tenant_id = :tenant_id", {"tenant_id": tenant_id}


def fetch_competiciones_con_resultados(db: Session, tenant_id: int | None = None) -> list[dict]:
    tenant_filter, tenant_params = _event_tenant_clause(tenant_id)
    query = text(f"""
        SELECT
            e.id,
            e.nombre,
            e.fecha_fin,
            e.fecha_inicio,
            e.tenant_id,
            s.nombre AS escenario_nombre,
            s.municipio,
            COUNT(DISTINCT r.id) AS num_resultados,
            MAX(r.created_at) AS ultimo_resultado
        FROM eventos e
        JOIN inscripciones i ON i.evento_id = e.id
        JOIN resultados r ON r.inscripcion_id = i.id
        LEFT JOIN escenarios s ON s.id = e.escenario_id
        WHERE e.activo = true
          AND r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
          {tenant_filter}
        GROUP BY e.id, e.nombre, e.fecha_fin, e.fecha_inicio, e.tenant_id,
                 s.nombre, s.municipio
        ORDER BY MAX(r.created_at) DESC NULLS LAST, e.fecha_fin DESC NULLS LAST
    """)
    rows = db.execute(query, tenant_params).mappings().all()
    competitions = []
    for row in rows:
        escenario = None
        if row["escenario_nombre"] or row["municipio"]:
            escenario = Escenario(nombre=row["escenario_nombre"], municipio=row["municipio"])
        evento = Evento(
            id=row["id"],
            nombre=row["nombre"],
            fecha_fin=row["fecha_fin"],
            fecha_inicio=row["fecha_inicio"],
            tenant_id=int(row.get("tenant_id") or 1),
            estado="FINALIZADO",
            activo=True,
        )
        competitions.append({
            "id": str(row["id"]),
            "name": row["nombre"],
            "date": _format_fecha_corta(row["fecha_fin"] or row["fecha_inicio"]),
            "location": resolve_location(evento, escenario),
            "totalResults": int(row["num_resultados"] or 0),
            "lastResultAt": row["ultimo_resultado"].isoformat() if row["ultimo_resultado"] else None,
        })
    return competitions


def fetch_resultados_resumen(
    db: Session,
    evento_id: int | None = None,
    tenant_id: int | None = None,
) -> dict:
    params: dict = {}
    evento_filter = ""
    if evento_id is not None:
        evento_filter = "AND i.evento_id = :evento_id"
        params["evento_id"] = evento_id

    tenant_filter = ""
    if tenant_id is not None:
        tenant_filter = "AND e.tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    query = text(f"""
        SELECT
            COUNT(DISTINCT i.evento_id) AS eventos,
            COUNT(DISTINCT i.deportista_id) AS participantes,
            COUNT(DISTINCT CASE WHEN r.codigo_irm IS NOT NULL AND r.codigo_irm <> '' THEN r.id END) AS records,
            COUNT(DISTINCT CONCAT(i.evento_id, '-', i.prueba_id)) AS pruebas
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN eventos e ON e.id = i.evento_id
        WHERE r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
          {evento_filter}
          {tenant_filter}
    """)
    row = db.execute(query, params).mappings().first()
    return {
        "eventos": int(row["eventos"] or 0),
        "records": int(row["records"] or 0),
        "participantes": int(row["participantes"] or 0),
        "pruebas": int(row["pruebas"] or 0),
    }


def _fetch_result_rows(db: Session, evento_id: int, prueba_id: int | None = None) -> list[dict]:
    prueba_filter = "AND i.prueba_id = :prueba_id" if prueba_id is not None else ""
    params: dict = {"evento_id": evento_id}
    if prueba_id is not None:
        params["prueba_id"] = prueba_id

    query = text(f"""
        SELECT
            r.id AS resultado_id,
            r.posicion,
            r.valor_numerico,
            r.valor_tiempo,
            r.codigo_irm,
            r.viento_ms,
            r.estado,
            r.created_at,
            r.descalificado,
            i.prueba_id,
            i.deportista_id,
            p.nombre AS prueba_nombre,
            p.genero_aplicable,
            p.unidad_resultado,
            disc.nombre AS disciplina,
            ce.nombre AS categoria,
            TRIM(CONCAT(
                COALESCE(per.primer_nombre, ''),
                ' ',
                COALESCE(per.segundo_nombre, ''),
                ' ',
                COALESCE(per.primer_apellido, ''),
                ' ',
                COALESCE(per.segundo_apellido, '')
            )) AS atleta,
            per.foto,
            MAX(eq.nombre) AS club
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN deportistas d ON d.id = i.deportista_id
        JOIN personas per ON per.id = d.persona_id
        JOIN pruebas p ON p.id = i.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        LEFT JOIN categorias_edad ce ON ce.id = i.categoria_edad_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE i.evento_id = :evento_id
          AND r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
          {prueba_filter}
        GROUP BY r.id, r.posicion, r.valor_numerico, r.valor_tiempo, r.codigo_irm,
                 r.viento_ms, r.estado, r.created_at, r.descalificado,
                 i.prueba_id, i.deportista_id, p.nombre, p.genero_aplicable,
                 p.unidad_resultado, disc.nombre, ce.nombre,
                 per.primer_nombre, per.segundo_nombre, per.primer_apellido, per.segundo_apellido,
                 per.foto
        ORDER BY i.prueba_id, r.posicion NULLS LAST, r.created_at
    """)
    return [dict(row) for row in db.execute(query, params).mappings().all()]


def _build_result_card(
    evento_id: int,
    prueba_id: int,
    rows: list[dict],
    api_base: str | None = None,
) -> dict | None:
    if not rows:
        return None

    first = rows[0]
    podium_rows = [r for r in rows if r.get("posicion") in (1, 2, 3)]
    podium_rows.sort(key=lambda r: r["posicion"])
    podium = [_row_to_podium_member(r, api_base=api_base) for r in podium_rows]

    positions = {m["position"] for m in podium}
    has_full_podium = {1, 2, 3}.issubset(positions)
    has_records = any(m["isRecord"] for m in podium)
    latest = max((r["created_at"] for r in rows if r.get("created_at")), default=None)
    is_recent = bool(latest and latest >= datetime.now(latest.tzinfo) - timedelta(days=30))

    return {
        "id": make_result_id(evento_id, prueba_id),
        "competitionId": str(evento_id),
        "event": first["prueba_nombre"] or "Prueba",
        "category": _format_categoria(
            first.get("genero_aplicable"),
            first.get("categoria"),
            first.get("disciplina"),
        ),
        "podium": podium,
        "participants": len(rows),
        "records": sum(1 for r in rows if r.get("codigo_irm")),
        "hasFullPodium": has_full_podium,
        "hasRecords": has_records,
        "isRecent": is_recent,
        "updatedAt": latest.isoformat() if latest else None,
    }


def fetch_resultados_por_evento(
    db: Session,
    evento_id: int,
    filtro: str | None = None,
    api_base: str | None = None,
    tenant_id: int | None = None,
) -> list[dict]:
    rows = _fetch_result_rows(db, evento_id)
    if not rows:
        return []

    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["prueba_id"], []).append(row)

    cards = []
    for prueba_id, prueba_rows in grouped.items():
        card = _build_result_card(evento_id, prueba_id, prueba_rows, api_base=api_base)
        if card:
            cards.append(card)

    filtro_norm = (filtro or "TODOS").upper()
    if filtro_norm == "RECIENTES":
        cards = [c for c in cards if c["isRecent"]]
    elif filtro_norm == "DESTACADOS":
        cards = [c for c in cards if c["hasFullPodium"]]
    elif filtro_norm == "RÉCORDS" or filtro_norm == "RECORDS":
        cards = [c for c in cards if c["hasRecords"]]

    cards.sort(key=lambda c: c.get("updatedAt") or "", reverse=True)
    return cards


def fetch_resultados_by_deporte(db: Session, deporte_id: int, api_base: str | None = None) -> list[dict]:
    pairs_query = text("""
        SELECT DISTINCT i.evento_id, i.prueba_id
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN pruebas p ON p.id = i.prueba_id
        LEFT JOIN disciplinas disc ON disc.id = p.disciplina_id
        WHERE r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
          AND (disc.deporte_id = :deporte_id OR p.deporte_id = :deporte_id)
    """)
    pairs = db.execute(pairs_query, {"deporte_id": deporte_id}).all()

    cards: list[dict] = []
    for evento_id, prueba_id in pairs:
        rows = _fetch_result_rows(db, evento_id, prueba_id)
        card = _build_result_card(evento_id, prueba_id, rows, api_base=api_base)
        if card:
            cards.append(card)

    cards.sort(key=lambda c: c.get("updatedAt") or "", reverse=True)
    return cards


def has_resultado_publicado(db: Session, evento_id: int, prueba_id: int) -> bool:
    query = text("""
        SELECT 1
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        WHERE i.evento_id = :evento_id
          AND i.prueba_id = :prueba_id
          AND r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
        LIMIT 1
    """)
    return db.execute(query, {"evento_id": evento_id, "prueba_id": prueba_id}).first() is not None


def fetch_resultado_detalle(db: Session, result_id: str, api_base: str | None = None) -> dict | None:
    parsed = parse_result_id(result_id)
    if not parsed:
        return None
    evento_id, prueba_id = parsed

    stmt = (
        select(Evento, Escenario)
        .outerjoin(Escenario, Evento.escenario_id == Escenario.id)
        .where(Evento.id == evento_id)
    )

    row = db.execute(stmt).first()
    if not row:
        return None

    evento, escenario = row
    result_rows = _fetch_result_rows(db, evento_id, prueba_id)
    if not result_rows:
        return None

    card = _build_result_card(evento_id, prueba_id, result_rows, api_base=api_base)
    if not card:
        return None

    ranking = []
    for r in sorted(result_rows, key=lambda x: (x["posicion"] is None, x["posicion"] or 999)):
        ranking.append(_row_to_podium_member(r, api_base=api_base))

    best = ranking[0] if ranking else None
    wind = next((r.get("viento_ms") for r in result_rows if r.get("viento_ms") is not None), None)

    return {
        **card,
        "competitionName": evento.nombre,
        "date": _format_fecha_corta(evento.fecha_fin or evento.fecha_inicio),
        "venue": resolve_location(evento, escenario),
        "description": (
            f"Resultados oficiales de {card['event']} en {evento.nombre}. "
            f"Participaron {len(result_rows)} atletas."
        ),
        "ranking": ranking,
        "bestMark": best["time"] if best else "—",
        "wind": f"{float(wind):+.1f} m/s" if wind is not None else None,
        "startDate": format_fecha_larga(evento.fecha_inicio),
        "endDate": format_fecha_larga(evento.fecha_fin or evento.fecha_inicio),
    }


def fetch_atletas_con_podios(
    db: Session,
    evento_id: int | None = None,
    tenant_id: int | None = None,
) -> list[dict]:
    params: dict = {}
    evento_filter = ""
    if evento_id is not None:
        evento_filter = "AND i.evento_id = :evento_id"
        params["evento_id"] = evento_id

    tenant_filter = ""
    if tenant_id is not None:
        tenant_filter = "AND d.tenant_id = :tenant_id"
        params["tenant_id"] = tenant_id

    query = text(f"""
        SELECT
            i.deportista_id,
            TRIM(CONCAT(
                COALESCE(per.primer_nombre, ''),
                ' ',
                COALESCE(per.segundo_nombre, ''),
                ' ',
                COALESCE(per.primer_apellido, ''),
                ' ',
                COALESCE(per.segundo_apellido, '')
            )) AS atleta,
            MAX(eq.nombre) AS club,
            COUNT(*) FILTER (WHERE r.posicion IN (1,2,3)) AS podios,
            COUNT(*) FILTER (WHERE r.posicion = 1) AS oros,
            COUNT(*) FILTER (WHERE r.posicion = 2) AS platas,
            COUNT(*) FILTER (WHERE r.posicion = 3) AS bronces
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN deportistas d ON d.id = i.deportista_id
        JOIN personas per ON per.id = d.persona_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
          AND r.posicion IN (1,2,3)
          {evento_filter}
          {tenant_filter}
        GROUP BY i.deportista_id, per.primer_nombre, per.segundo_nombre,
                 per.primer_apellido, per.segundo_apellido
        ORDER BY podios DESC, oros DESC, platas DESC, bronces DESC, atleta
    """)
    rows = db.execute(query, params).mappings().all()
    athletes = []
    for row in rows:
        athletes.append({
            "deportistaId": str(row["deportista_id"]),
            "athlete": _athlete_name(row),
            "club": row.get("club") or "Sin club",
            "podios": int(row.get("podios") or 0),
            "oros": int(row.get("oros") or 0),
            "platas": int(row.get("platas") or 0),
            "bronces": int(row.get("bronces") or 0),
        })
    return athletes
