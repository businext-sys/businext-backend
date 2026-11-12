import re

SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def shorten(text: str, max_sentences: int, max_chars: int) -> str:
    """Las primeras `max_sentences` frases de `text`, cortadas por palabra si pasan de `max_chars`."""
    short = " ".join(SENTENCE_END.split(text.strip())[:max_sentences])
    if len(short) <= max_chars:
        return short
    return short[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"
