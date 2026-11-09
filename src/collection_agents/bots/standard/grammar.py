import re
import unicodedata

CONFIRMATION_GRAMMAR = [
    "si", "no", "si soy yo", "no no soy yo",
    "es mi esposo", "es mi esposa", "es mi hijo", "es mi hija",
    "un momento", "quien es", "quien habla", "diga", "digame", "bueno", "hola", "mande",
    "me equivoque de numero", "no vive aqui", "estoy ocupado", "llame mas tarde",
    "ya pague", "puedo hablar", "correcto", "exacto", "claro", "como no",
]
CONFIRMATION_GRAMMAR_RELEASE_SEC = 20.0
_GRAMMAR_PHRASES = set(CONFIRMATION_GRAMMAR)


def _normalize_for_grammar(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _is_confirmation_phrase(text: str) -> bool:
    normalized = _normalize_for_grammar(text)
    if not normalized:
        return False
    words = normalized.split()
    max_phrase_words = max(len(p.split()) for p in _GRAMMAR_PHRASES)
    reachable = [False] * (len(words) + 1)
    reachable[0] = True
    for i in range(len(words)):
        if not reachable[i]:
            continue
        for n in range(1, min(max_phrase_words, len(words) - i) + 1):
            if " ".join(words[i:i + n]) in _GRAMMAR_PHRASES:
                reachable[i + n] = True
    return reachable[len(words)]
