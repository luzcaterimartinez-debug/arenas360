RESULT_ID_SEP = "-"


def make_result_id(evento_id: int, prueba_id: int) -> str:
    return f"{evento_id}{RESULT_ID_SEP}{prueba_id}"


def parse_result_id(result_id: str) -> tuple[int, int] | None:
    if RESULT_ID_SEP not in result_id:
        return None
    evento_part, prueba_part = result_id.split(RESULT_ID_SEP, 1)
    try:
        return int(evento_part), int(prueba_part)
    except ValueError:
        return None
