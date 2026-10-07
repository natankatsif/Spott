"""Questions shown to the admin or kept as quick questions: personal data masked, long ones cut."""

import re

MAX_QUESTION_CHARS = 200
MASK = "•••"
PERSONAL = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # e-mail
    r"|\+?\d[\d\s().-]{5,}\d"  # phones, IDNP, other long digit runs
)


def mask(text: str) -> str:
    masked = PERSONAL.sub(MASK, text)
    return masked if len(masked) <= MAX_QUESTION_CHARS else masked[: MAX_QUESTION_CHARS - 1] + "…"
