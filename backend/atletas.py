"""Athletes API helpers — list and profile from PostgreSQL."""

from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.eventos import (
    MESES,
    format_fecha_larga,
    resolve_location,
    resolve_ui_status,
)
from backend.media import build_athlete_image_url, build_media_url
from backend.models import Escenario, Evento
from backend.resultados import format_mark

CATEGORY_COLORS = {
    "ÉLITE": "#FF9F1C",
    "ELITE": "#FF9F1C",
    "MAYORES": "#FF9F1C",
    "SUB-23": "#208AEF",
    "SUB 23": "#208AEF",
    "JÚNIOR": "#8B5CF6",
    "JUNIOR": "#8B5CF6",
    "JUNIOR": "#8B5CF6",
    "INFANTIL": "#10B981",
    "GENERAL": "#64748B",
}

VALID_FILTERS = {"TODOS", "ACTIVOS", "INACTIVOS", "NUEVOS"}


def _athlete_name(row: dict) -> str:
    name = " ".join((row.get("nombre") or row.get("atleta") or "").split())
    return name.upper() if name else "ATLETA"


def _category_color(category: str | None) -> str:
    key = (category or "GENERAL").upper().strip()
    return CATEGORY_COLORS.get(key, "#64748B")


def _format_birth_date(value: date | None) -> str:
    if not value:
        return "—"
    return f"{value.day} {MESES[value.month - 1]} {value.year}"


def _format_specialty(deporte: str | None, disciplina: str | None) -> str:
    dep = (deporte or "").strip()
    disc = (disciplina or "").strip()
    if dep and disc:
        return f"{dep} - {disc}"
    return disc or dep or "—"


def _compute_rating(podiums: int, participations: int, gold: int) -> str:
    if participations <= 0:
        return "—"
    score = min(10.0, (podiums / participations) * 8 + gold * 0.4)
    return f"{score:.1f}"


def _pick_best_mark(best_tiempo, best_distancia, unidad_hint: str | None = None) -> str:
    if best_tiempo is not None:
        return format_mark(best_tiempo, None, "TIEMPO")
    if best_distancia is not None:
        return format_mark(None, best_distancia, "DISTANCIA")
    return "—"


def _filter_clause(filtro: str | None) -> str:
    filtro_norm = (filtro or "TODOS").upper()
    if filtro_norm == "ACTIVOS":
        return "AND d.activo = true"
    if filtro_norm == "INACTIVOS":
        return "AND d.activo = false"
    if filtro_norm == "NUEVOS":
        return "AND d.created_at >= NOW() - INTERVAL '90 days'"
    return ""


def _tenant_clause(tenant_id: int | None, alias: str = "d") -> tuple[str, dict]:
    if tenant_id is None:
        return "", {}
    return f"AND {alias}.tenant_id = :tenant_id", {"tenant_id": tenant_id}


def fetch_atletas_list(
    db: Session,
    q: str | None = None,
    filtro: str | None = None,
    api_base: str | None = None,
    deporte_id: int | None = None,
    limit: int = 100,
    tenant_id: int | None = None,
) -> list[dict]:
    search_filter = ""
    params: dict = {"limit": limit}
    if q and q.strip():
        search_filter = """
            AND (
                per.primer_nombre ILIKE :search OR
                per.segundo_nombre ILIKE :search OR
                per.primer_apellido ILIKE :search OR
                per.segundo_apellido ILIKE :search OR
                eq.nombre ILIKE :search
            )
        """
        params["search"] = f"%{q.strip()}%"

    deporte_filter = ""
    if deporte_id is not None:
        deporte_filter = "AND (disc.deporte_id = :deporte_id OR d.deporte_id = :deporte_id)"
        params["deporte_id"] = deporte_id

    tenant_filter, tenant_params = _tenant_clause(tenant_id, "d")
    params.update(tenant_params)

    query = text(f"""
        SELECT
            d.id,
            d.activo,
            d.created_at,
            per.foto,
            per.fecha_nacimiento,
            dep.nombre AS deporte,
            disc.nombre AS disciplina,
            TRIM(CONCAT(
                COALESCE(per.primer_nombre, ''),
                ' ',
                COALESCE(per.segundo_nombre, ''),
                ' ',
                COALESCE(per.primer_apellido, ''),
                ' ',
                COALESCE(per.segundo_apellido, '')
            )) AS nombre,
            MAX(ce.nombre) AS categoria,
            MAX(eq.nombre) AS club,
            COUNT(DISTINCT i.evento_id) AS participaciones,
            COUNT(DISTINCT CASE WHEN r.posicion = 1 THEN r.id END) AS oros,
            COUNT(DISTINCT CASE WHEN r.posicion = 2 THEN r.id END) AS platas,
            COUNT(DISTINCT CASE WHEN r.posicion = 3 THEN r.id END) AS bronces,
            COUNT(DISTINCT CASE WHEN r.posicion IN (1,2,3) THEN r.id END) AS podios,
            COUNT(DISTINCT CASE WHEN r.codigo_irm IS NOT NULL AND r.codigo_irm <> '' THEN r.id END) AS records,
            MIN(r.valor_tiempo) FILTER (WHERE r.valor_tiempo IS NOT NULL) AS best_tiempo,
            MAX(r.valor_numerico) FILTER (WHERE r.valor_numerico IS NOT NULL) AS best_distancia
        FROM deportistas d
        JOIN personas per ON per.id = d.persona_id
        LEFT JOIN deportes dep ON dep.id = d.deporte_id
        LEFT JOIN disciplinas disc ON disc.id = d.disciplina_id
        LEFT JOIN inscripciones i ON i.deportista_id = d.id
        LEFT JOIN resultados r ON r.inscripcion_id = i.id
            AND r.estado IN ('VALIDADO', 'OFICIAL') AND r.descalificado = false
        LEFT JOIN categorias_edad ce ON ce.id = i.categoria_edad_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE 1=1
          {_filter_clause(filtro)}
          {tenant_filter}
          {deporte_filter}
          {search_filter}
        GROUP BY d.id, d.activo, d.created_at, per.foto, per.fecha_nacimiento,
                 dep.nombre, disc.nombre, per.primer_nombre, per.segundo_nombre,
                 per.primer_apellido, per.segundo_apellido
        ORDER BY podios DESC NULLS LAST, oros DESC NULLS LAST, nombre
        LIMIT :limit
    """)
    rows = db.execute(query, params).mappings().all()
    athletes = []
    now = datetime.now().astimezone() if rows and rows[0].get("created_at") else datetime.now()

    for row in rows:
        oros = int(row.get("oros") or 0)
        platas = int(row.get("platas") or 0)
        bronces = int(row.get("bronces") or 0)
        podios = int(row.get("podios") or 0)
        participaciones = int(row.get("participaciones") or 0)
        total_medals = oros + platas + bronces
        categoria = (row.get("categoria") or "General").upper()
        activo = bool(row.get("activo"))
        created_at = row.get("created_at")
        is_new = False
        if created_at is not None:
            ref = now if now.tzinfo else datetime.now()
            if created_at.tzinfo:
                is_new = created_at >= ref - timedelta(days=90)
            else:
                is_new = created_at >= datetime.now() - timedelta(days=90)

        athletes.append({
            "id": str(row["id"]),
            "name": _athlete_name(row),
            "specialty": _format_specialty(row.get("deporte"), row.get("disciplina")),
            "category": categoria,
            "categoryColor": _category_color(categoria),
            "image": build_athlete_image_url(row.get("foto"), api_base=api_base),
            "medals": str(total_medals),
            "records": str(int(row.get("records") or 0)),
            "status": "ACTIVO" if activo else "INACTIVO",
            "statusColor": "#10B981" if activo else "#EF4444",
            "bestTime": _pick_best_mark(row.get("best_tiempo"), row.get("best_distancia")),
            "club": row.get("club") or "Sin club",
            "rating": _compute_rating(podios, participaciones, oros),
            "isNew": is_new,
        })
    return athletes


def fetch_atletas_resumen(db: Session, filtro: str | None = None, tenant_id: int | None = None) -> dict:
    tenant_filter, tenant_params = _tenant_clause(tenant_id, "d")
    query = text(f"""
        SELECT
            COUNT(DISTINCT d.id) AS total,
            COUNT(DISTINCT CASE WHEN r.posicion IN (1,2,3) THEN r.id END) AS total_medals,
            COUNT(DISTINCT CASE WHEN r.codigo_irm IS NOT NULL AND r.codigo_irm <> '' THEN r.id END) AS total_records
        FROM deportistas d
        LEFT JOIN inscripciones i ON i.deportista_id = d.id
        LEFT JOIN resultados r ON r.inscripcion_id = i.id
            AND r.estado IN ('VALIDADO', 'OFICIAL') AND r.descalificado = false
        WHERE 1=1
          {_filter_clause(filtro)}
          {tenant_filter}
    """)
    row = db.execute(query, tenant_params).mappings().first()
    return {
        "total": int(row["total"] or 0),
        "totalMedals": int(row["total_medals"] or 0),
        "totalRecords": int(row["total_records"] or 0),
    }


def _fetch_best_times(db: Session, deportista_id: int) -> list[dict]:
    query = text("""
        SELECT
            p.nombre AS prueba,
            p.unidad_resultado,
            r.valor_tiempo,
            r.valor_numerico,
            e.nombre AS evento,
            COALESCE(e.fecha_fin, e.fecha_inicio) AS fecha
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN pruebas p ON p.id = i.prueba_id
        JOIN eventos e ON e.id = i.evento_id
        WHERE i.deportista_id = :deportista_id
          AND r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
        ORDER BY r.created_at DESC
        LIMIT 50
    """)
    rows = db.execute(query, {"deportista_id": deportista_id}).mappings().all()
    best_by_prueba: dict[str, dict] = {}
    for row in rows:
        key = row["prueba"] or "Prueba"
        mark = format_mark(row.get("valor_tiempo"), row.get("valor_numerico"), row.get("unidad_resultado"))
        current = best_by_prueba.get(key)
        if not current:
            best_by_prueba[key] = {
                "event": key,
                "time": mark,
                "date": format_fecha_larga(row.get("fecha")),
                "competition": row.get("evento") or "—",
                "_sort": row.get("valor_tiempo") or row.get("valor_numerico"),
            }
            continue
        # Keep first seen (already ordered by created_at desc) — good enough for display
    return [
        {k: v for k, v in item.items() if k != "_sort"}
        for item in list(best_by_prueba.values())[:5]
    ]


def _fetch_recent_results(db: Session, deportista_id: int) -> list[dict]:
    query = text("""
        SELECT
            r.id,
            r.posicion,
            r.valor_tiempo,
            r.valor_numerico,
            p.nombre AS prueba,
            p.unidad_resultado,
            e.nombre AS evento,
            COALESCE(e.fecha_fin, e.fecha_inicio) AS fecha
        FROM resultados r
        JOIN inscripciones i ON i.id = r.inscripcion_id
        JOIN pruebas p ON p.id = i.prueba_id
        JOIN eventos e ON e.id = i.evento_id
        WHERE i.deportista_id = :deportista_id
          AND r.estado IN ('VALIDADO', 'OFICIAL')
          AND r.descalificado = false
        ORDER BY r.created_at DESC
        LIMIT 8
    """)
    rows = db.execute(query, {"deportista_id": deportista_id}).mappings().all()
    results = []
    for row in rows:
        pos = row.get("posicion")
        results.append({
            "id": str(row["id"]),
            "event": row["prueba"] or "Prueba",
            "position": f"{pos}°" if pos else "—",
            "time": format_mark(row.get("valor_tiempo"), row.get("valor_numerico"), row.get("unidad_resultado")),
            "date": format_fecha_larga(row.get("fecha")),
            "competition": row.get("evento") or "—",
        })
    return results


def _fetch_competitions(db: Session, deportista_id: int) -> list[dict]:
    query = text("""
        SELECT
            e.id,
            e.nombre,
            e.fecha_inicio,
            e.fecha_fin,
            e.estado,
            e.tenant_id,
            s.nombre AS escenario_nombre,
            s.municipio,
            COUNT(DISTINCT i.prueba_id) AS pruebas,
            MIN(r.posicion) AS mejor_posicion
        FROM inscripciones i
        JOIN eventos e ON e.id = i.evento_id
        LEFT JOIN escenarios s ON s.id = e.escenario_id
        LEFT JOIN resultados r ON r.inscripcion_id = i.id
            AND r.estado IN ('VALIDADO', 'OFICIAL') AND r.descalificado = false
        WHERE i.deportista_id = :deportista_id AND e.activo = true
        GROUP BY e.id, e.nombre, e.fecha_inicio, e.fecha_fin, e.estado, e.tenant_id,
                 s.nombre, s.municipio
        ORDER BY e.fecha_inicio DESC NULLS LAST
        LIMIT 10
    """)
    rows = db.execute(query, {"deportista_id": deportista_id}).mappings().all()
    competitions = []
    for row in rows:
        evento = Evento(
            id=row["id"],
            nombre=row["nombre"],
            fecha_inicio=row["fecha_inicio"],
            fecha_fin=row["fecha_fin"],
            estado=row["estado"] or "",
            tenant_id=int(row.get("tenant_id") or 1),
            activo=True,
        )
        escenario = Escenario(nombre=row["escenario_nombre"], municipio=row["municipio"]) if row["escenario_nombre"] or row["municipio"] else None
        ui_status, status_color = resolve_ui_status(evento)
        best_pos = row.get("mejor_posicion")
        inicio = format_fecha_larga(row.get("fecha_inicio"))
        fin = format_fecha_larga(row.get("fecha_fin") or row.get("fecha_inicio"))
        date_range = inicio if inicio == fin else f"{inicio} — {fin}"
        competitions.append({
            "id": str(row["id"]),
            "name": row["nombre"],
            "location": resolve_location(evento, escenario),
            "date": date_range,
            "status": ui_status,
            "statusColor": status_color,
            "position": f"{best_pos}°" if best_pos else "—",
            "events": int(row.get("pruebas") or 0),
        })
    return competitions


def fetch_atleta_perfil(
    db: Session,
    deportista_id: int,
    api_base: str | None = None,
    tenant_id: int | None = None,
) -> dict | None:
    tenant_filter, tenant_params = _tenant_clause(tenant_id, "d")
    params = {"deportista_id": deportista_id, **tenant_params}
    query = text(f"""
        SELECT
            d.id,
            d.activo,
            d.created_at,
            per.foto,
            per.fecha_nacimiento,
            dep.nombre AS deporte,
            disc.nombre AS disciplina,
            mun.nombre AS municipio,
            TRIM(CONCAT(
                COALESCE(per.primer_nombre, ''),
                ' ',
                COALESCE(per.segundo_nombre, ''),
                ' ',
                COALESCE(per.primer_apellido, ''),
                ' ',
                COALESCE(per.segundo_apellido, '')
            )) AS nombre,
            MAX(ce.nombre) AS categoria,
            MAX(eq.nombre) AS club,
            COUNT(DISTINCT i.evento_id) AS participaciones,
            COUNT(DISTINCT CASE WHEN r.posicion = 1 THEN r.id END) AS oros,
            COUNT(DISTINCT CASE WHEN r.posicion = 2 THEN r.id END) AS platas,
            COUNT(DISTINCT CASE WHEN r.posicion = 3 THEN r.id END) AS bronces,
            COUNT(DISTINCT CASE WHEN r.posicion IN (1,2,3) THEN r.id END) AS podios,
            COUNT(DISTINCT CASE WHEN r.codigo_irm IS NOT NULL AND r.codigo_irm <> '' THEN r.id END) AS records,
            MIN(r.posicion) AS best_rank,
            MIN(r.valor_tiempo) FILTER (WHERE r.valor_tiempo IS NOT NULL) AS best_tiempo,
            MAX(r.valor_numerico) FILTER (WHERE r.valor_numerico IS NOT NULL) AS best_distancia,
            COUNT(DISTINCT EXTRACT(YEAR FROM COALESCE(e.fecha_inicio, e.created_at))) AS seasons
        FROM deportistas d
        JOIN personas per ON per.id = d.persona_id
        LEFT JOIN municipios mun ON mun.id = per.municipio_residencia_id
        LEFT JOIN deportes dep ON dep.id = d.deporte_id
        LEFT JOIN disciplinas disc ON disc.id = d.disciplina_id
        LEFT JOIN inscripciones i ON i.deportista_id = d.id
        LEFT JOIN eventos e ON e.id = i.evento_id
        LEFT JOIN resultados r ON r.inscripcion_id = i.id
            AND r.estado IN ('VALIDADO', 'OFICIAL') AND r.descalificado = false
        LEFT JOIN categorias_edad ce ON ce.id = i.categoria_edad_id
        LEFT JOIN equipo_miembros em ON em.deportista_id = d.id
        LEFT JOIN equipos eq ON eq.id = em.equipo_id
        WHERE d.id = :deportista_id
          {tenant_filter}
        GROUP BY d.id, d.activo, d.created_at, per.foto, per.fecha_nacimiento,
                 dep.nombre, disc.nombre, mun.nombre,
                 per.primer_nombre, per.segundo_nombre, per.primer_apellido, per.segundo_apellido
        LIMIT 1
    """)
    row = db.execute(query, params).mappings().first()
    if not row:
        return None

    participaciones = int(row.get("participaciones") or 0)
    podios = int(row.get("podios") or 0)
    oros = int(row.get("oros") or 0)
    win_rate = f"{(oros / participaciones * 100):.1f}%" if participaciones > 0 else "0%"
    categoria = (row.get("categoria") or "General").upper()

    return {
        "id": str(row["id"]),
        "name": _athlete_name(row),
        "specialty": _format_specialty(row.get("deporte"), row.get("disciplina")),
        "category": categoria,
        "club": row.get("club") or "Sin club",
        "image": build_athlete_image_url(row.get("foto"), api_base=api_base),
        "nationality": (row.get("municipio") or "Colombia").upper(),
        "birthDate": _format_birth_date(row.get("fecha_nacimiento")),
        "medals": {
            "gold": oros,
            "silver": int(row.get("platas") or 0),
            "bronze": int(row.get("bronces") or 0),
        },
        "records": int(row.get("records") or 0),
        "stats": {
            "participations": participaciones,
            "podiums": podios,
            "winRate": win_rate,
            "avgTime": _pick_best_mark(row.get("best_tiempo"), row.get("best_distancia")),
            "bestRank": int(row.get("best_rank") or 0),
            "seasons": int(row.get("seasons") or 0),
        },
        "bestTimes": _fetch_best_times(db, deportista_id),
        "recentResults": _fetch_recent_results(db, deportista_id),
        "competitions": _fetch_competitions(db, deportista_id),
    }


def _mini_athlete(profile: dict) -> dict:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "specialty": profile["specialty"],
        "club": profile["club"],
        "image": profile["image"],
        "category": profile["category"],
    }


def _leader_numeric(
    value_a: int | float,
    value_b: int | float,
    lower_is_better: bool = False,
) -> str | None:
    if value_a == value_b:
        return None
    if lower_is_better:
        return "A" if value_a < value_b else "B"
    return "A" if value_a > value_b else "B"


def _parse_percent(value: str) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return 0.0


def fetch_atletas_comparacion(
    db: Session,
    atleta_a_id: int,
    atleta_b_id: int,
    api_base: str | None = None,
) -> dict | None:
    if atleta_a_id == atleta_b_id:
        return None

    profile_a = fetch_atleta_perfil(db, atleta_a_id, api_base=api_base)
    profile_b = fetch_atleta_perfil(db, atleta_b_id, api_base=api_base)
    if not profile_a or not profile_b:
        return None

    stats_a = profile_a["stats"]
    stats_b = profile_b["stats"]
    medals_a = profile_a["medals"]
    medals_b = profile_b["medals"]
    total_medals_a = medals_a["gold"] + medals_a["silver"] + medals_a["bronze"]
    total_medals_b = medals_b["gold"] + medals_b["silver"] + medals_b["bronze"]

    rows = [
        {
            "label": "Medallas totales",
            "valueA": str(total_medals_a),
            "valueB": str(total_medals_b),
            "leader": _leader_numeric(total_medals_a, total_medals_b),
        },
        {
            "label": "Oro",
            "valueA": str(medals_a["gold"]),
            "valueB": str(medals_b["gold"]),
            "leader": _leader_numeric(medals_a["gold"], medals_b["gold"]),
        },
        {
            "label": "Plata",
            "valueA": str(medals_a["silver"]),
            "valueB": str(medals_b["silver"]),
            "leader": _leader_numeric(medals_a["silver"], medals_b["silver"]),
        },
        {
            "label": "Bronce",
            "valueA": str(medals_a["bronze"]),
            "valueB": str(medals_b["bronze"]),
            "leader": _leader_numeric(medals_a["bronze"], medals_b["bronze"]),
        },
        {
            "label": "Participaciones",
            "valueA": str(stats_a["participations"]),
            "valueB": str(stats_b["participations"]),
            "leader": _leader_numeric(stats_a["participations"], stats_b["participations"]),
        },
        {
            "label": "Podios",
            "valueA": str(stats_a["podiums"]),
            "valueB": str(stats_b["podiums"]),
            "leader": _leader_numeric(stats_a["podiums"], stats_b["podiums"]),
        },
        {
            "label": "Tasa de victoria",
            "valueA": stats_a["winRate"],
            "valueB": stats_b["winRate"],
            "leader": _leader_numeric(
                _parse_percent(stats_a["winRate"]),
                _parse_percent(stats_b["winRate"]),
            ),
        },
        {
            "label": "Récords",
            "valueA": str(profile_a["records"]),
            "valueB": str(profile_b["records"]),
            "leader": _leader_numeric(profile_a["records"], profile_b["records"]),
        },
        {
            "label": "Mejor puesto",
            "valueA": f"#{stats_a['bestRank']}" if stats_a["bestRank"] else "—",
            "valueB": f"#{stats_b['bestRank']}" if stats_b["bestRank"] else "—",
            "leader": _leader_numeric(
                stats_a["bestRank"] or 999,
                stats_b["bestRank"] or 999,
                lower_is_better=True,
            )
            if stats_a["bestRank"] and stats_b["bestRank"]
            else None,
        },
        {
            "label": "Temporadas",
            "valueA": str(stats_a["seasons"]),
            "valueB": str(stats_b["seasons"]),
            "leader": _leader_numeric(stats_a["seasons"], stats_b["seasons"]),
        },
    ]

    marks_a = {item["event"]: item["time"] for item in profile_a["bestTimes"]}
    marks_b = {item["event"]: item["time"] for item in profile_b["bestTimes"]}
    common_marks = [
        {
            "event": event,
            "timeA": marks_a[event],
            "timeB": marks_b[event],
        }
        for event in sorted(set(marks_a.keys()) & set(marks_b.keys()))
    ]

    return {
        "athleteA": _mini_athlete(profile_a),
        "athleteB": _mini_athlete(profile_b),
        "rows": rows,
        "commonMarks": common_marks,
    }
