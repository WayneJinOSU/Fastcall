import re


def format_person_name_title_case(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""

    def _format_part(part: str) -> str:
        if not part:
            return part
        return part[:1].upper() + part[1:].lower()

    return re.sub(r"\S+", lambda match: _format_part(match.group(0)), name)


def replace_person_name_in_text(text: str, user_name: str) -> str:
    raw_name = (user_name or "").strip()
    if not text or not raw_name:
        return text

    formatted_name = format_person_name_title_case(raw_name)
    if not formatted_name or formatted_name == raw_name:
        return text

    if raw_name in text:
        return text.replace(raw_name, formatted_name)

    return re.sub(
        re.escape(raw_name),
        formatted_name,
        text,
        flags=re.IGNORECASE,
    )
