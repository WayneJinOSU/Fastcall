from pathlib import Path


def execute_sql_file(connection, path):
    statements = []
    delimiter = ";"
    current = []
    for line in Path(path).read_text().splitlines(keepends=True):
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            if "".join(current).strip():
                statements.append("".join(current))
                current = []
            delimiter = stripped.split(maxsplit=1)[1]
            continue
        current.append(line)
        if stripped.endswith(delimiter):
            statement = "".join(current).strip()
            statement = statement[:-len(delimiter)].rstrip()
            if statement:
                statements.append(statement)
            current = []
    if "".join(current).strip():
        statements.append("".join(current))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
