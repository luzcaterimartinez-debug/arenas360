"""Disciplines API helpers — sports and sub-disciplines from PostgreSQL."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.atletas import fetch_atletas_list
from backend.eventos import fetch_eventos_by_deporte
from backend.resultados import fetch_resultados_by_deporte

VALID_FILTERS = {"TODOS", "OLIMPICOS", "CON_ATLETAS", "CON_PRUEBAS"}


def _filter_clause(filtro: str | None) -> str:
    key = (filtro or "TODOS").upper()
    if key == "OLIMPICOS":
        return "AND dep.es_olimpico = true"
    if key == "CON_ATLETAS":
        return "AND EXISTS (SELECT 1 FROM disciplinas disc2 JOIN deportistas d2 ON d2.disciplina_id = disc2.id WHERE disc2.deporte_id = dep.id)"
    if key == "CON_PRUEBAS":
        return "AND EXISTS (SELECT 1 FROM disciplinas disc2 JOIN pruebas p2 ON p2.disciplina_id = disc2.id WHERE disc2.deporte_id = dep.id)"
    return ""


def fetch_disciplinas_list(
    db: Session,
    q: str | None = None,
    filtro: str | None = None,
) -> list[dict]:
    params: dict = {}
    search_clause = ""
    if q and q.strip():
        params["q"] = f"%{q.strip().lower()}%"
        search_clause = """
          AND (
            LOWER(dep.nombre) LIKE :q
            OR EXISTS (
                SELECT 1 FROM disciplinas disc_q
                WHERE disc_q.deporte_id = dep.id
                  AND LOWER(disc_q.nombre) LIKE :q
            )
          )
        """

    query = text(
        f"""
        SELECT
            dep.id,
            dep.nombre,
            dep.descripcion,
            dep.es_olimpico,
            COUNT(DISTINCT disc.id) AS subdiscipline_count,
            COUNT(DISTINCT d.id) AS athlete_count,
            COUNT(DISTINCT p.id) AS test_count
        FROM deportes dep
        LEFT JOIN disciplinas disc
            ON disc.deporte_id = dep.id AND disc.activo_global = true
        LEFT JOIN deportistas d ON d.disciplina_id = disc.id
        LEFT JOIN pruebas p ON p.disciplina_id = disc.id
        WHERE dep.activo_global = true
          {_filter_clause(filtro)}
          {search_clause}
        GROUP BY dep.id, dep.nombre, dep.descripcion, dep.es_olimpico
        ORDER BY dep.nombre
        """
    )
    rows = db.execute(query, params).mappings().all()

    sport_ids = [row["id"] for row in rows]
    subdisciplines_map: dict[int, list[dict]] = {sid: [] for sid in sport_ids}
    if sport_ids:
        sub_rows = db.execute(
            text(
                """
                SELECT id, deporte_id, nombre
                FROM disciplinas
                WHERE activo_global = true AND deporte_id = ANY(:ids)
                ORDER BY nombre
                """
            ),
            {"ids": sport_ids},
        ).mappings().all()
        for sub in sub_rows:
            subdisciplines_map.setdefault(sub["deporte_id"], []).append(
                {"id": str(sub["id"]), "name": sub["nombre"]}
            )

    items: list[dict] = []
    for row in rows:
        sport_id = row["id"]
        subdisciplines = subdisciplines_map.get(sport_id, [])
        display_name = row["nombre"]
        if subdisciplines:
            specialty_label = ", ".join(s["name"] for s in subdisciplines[:3])
            if len(subdisciplines) > 3:
                specialty_label += f" +{len(subdisciplines) - 3}"
        else:
            specialty_label = row["descripcion"] or "Disciplina general"

        items.append(
            {
                "id": str(sport_id),
                "name": display_name,
                "description": specialty_label,
                "isOlympic": bool(row["es_olimpico"]),
                "subdisciplineCount": int(row["subdiscipline_count"] or 0),
                "athleteCount": int(row["athlete_count"] or 0),
                "testCount": int(row["test_count"] or 0),
                "subdisciplines": subdisciplines,
            }
        )
    return items


def fetch_disciplinas_resumen(db: Session, filtro: str | None = None) -> dict:
    filter_clause = _filter_clause(filtro).replace("dep.", "dep2.")
    summary_query = text(
        f"""
        SELECT
            (SELECT COUNT(*) FROM disciplinas WHERE activo_global = true) AS total_disciplinas,
            (
                SELECT COUNT(*)
                FROM deportes dep2
                WHERE dep2.activo_global = true
                  {filter_clause}
            ) AS total_deportes,
            (
                SELECT COUNT(DISTINCT d.id)
                FROM deportistas d
                JOIN disciplinas disc ON disc.id = d.disciplina_id
                JOIN deportes dep2 ON dep2.id = disc.deporte_id
                WHERE dep2.activo_global = true
                  {filter_clause.replace('dep2.', 'dep2.')}
            ) AS total_atletas,
            (
                SELECT COUNT(DISTINCT p.id)
                FROM pruebas p
                JOIN disciplinas disc ON disc.id = p.disciplina_id
                JOIN deportes dep2 ON dep2.id = disc.deporte_id
                WHERE dep2.activo_global = true
                  {filter_clause}
            ) AS total_pruebas
        """
    )
    row = db.execute(summary_query).mappings().first()
    return {
        "totalDisciplines": int(row["total_disciplinas"] or 0),
        "totalSports": int(row["total_deportes"] or 0),
        "totalAthletes": int(row["total_atletas"] or 0),
        "totalTests": int(row["total_pruebas"] or 0),
    }


def _fetch_deporte_header(db: Session, deporte_id: int) -> dict | None:
    row = db.execute(
        text(
            """
            SELECT id, nombre, descripcion, es_olimpico
            FROM deportes
            WHERE id = :deporte_id AND activo_global = true
            """
        ),
        {"deporte_id": deporte_id},
    ).mappings().first()
    if not row:
        return None

    sub_rows = db.execute(
        text(
            """
            SELECT id, nombre
            FROM disciplinas
            WHERE deporte_id = :deporte_id AND activo_global = true
            ORDER BY nombre
            """
        ),
        {"deporte_id": deporte_id},
    ).mappings().all()
    subdisciplines = [{"id": str(s["id"]), "name": s["nombre"]} for s in sub_rows]

    if subdisciplines:
        specialty_label = ", ".join(s["name"] for s in subdisciplines[:3])
        if len(subdisciplines) > 3:
            specialty_label += f" +{len(subdisciplines) - 3}"
    else:
        specialty_label = row["descripcion"] or "Disciplina general"

    return {
        "id": str(row["id"]),
        "name": row["nombre"],
        "description": specialty_label,
        "isOlympic": bool(row["es_olimpico"]),
        "subdisciplines": subdisciplines,
    }


def fetch_disciplina_detalle(
    db: Session,
    deporte_id: int,
    api_base: str | None = None,
) -> dict | None:
    header = _fetch_deporte_header(db, deporte_id)
    if not header:
        return None

    athletes = fetch_atletas_list(
        db,
        filtro="TODOS",
        api_base=api_base,
        deporte_id=deporte_id,
        limit=200,
    )
    events = fetch_eventos_by_deporte(db, deporte_id, api_base=api_base)
    results = fetch_resultados_by_deporte(db, deporte_id, api_base=api_base)

    return {
        **header,
        "summary": {
            "athleteCount": len(athletes),
            "eventCount": len(events),
            "resultCount": len(results),
            "testCount": len(header["subdisciplines"]),
        },
        "athletes": athletes,
        "events": events,
        "results": results,
    }
