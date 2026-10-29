"""Text normalisation applied to both the reference and Whisper's output before
scoring, so that formatting differences do not count as recognition errors.

"5 Milligramm" and "fünf Milligramm" are the same words; so are "Neu-Delhi" and
"Neu Delhi". Case, punctuation and digits are normalised. Umlauts and ß are kept:
"Ödeme" and "Odeme" are genuinely different words.
"""

import re
from pathlib import Path

from num2words import num2words

_NUMBER = re.compile(r"\d+(?:,\d+)?")
_NOT_WORD = re.compile(r"[^\wäöüß]+")
# Whisper sometimes writes units the way a doctor would; that is formatting, not mishearing.
_ABBREVIATIONS = {"mg": "milligramm", "µg": "mikrogramm", "mcg": "mikrogramm", "ml": "milliliter", "dr": "doktor"}
_WORD_FORMS = {"einhundert": "hundert", "eintausend": "tausend"}


def _spell_number(match: re.Match[str]) -> str:
    whole, _, frac = match.group(0).partition(",")
    words = num2words(int(whole), lang="de")
    # People say "hundert", num2words writes "einhundert"; pick one form.
    words = re.sub(r"^ein(hundert|tausend)", r"\1", words)
    if frac:
        words += " komma " + " ".join(num2words(int(d), lang="de") for d in frac)
    return f" {words} "


def normalize(text: str) -> str:
    text = text.lower()
    text = _NUMBER.sub(_spell_number, text)
    text = _NOT_WORD.sub(" ", text.replace("_", " "))
    words = (_ABBREVIATIONS.get(w, w) for w in text.split())
    return " ".join(_WORD_FORMS.get(w, w) for w in words)


def load_terms(path: Path) -> list[str]:
    """One term per line, # comments ignored, returned normalised and de-duplicated."""
    terms: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term = normalize(line)
        if term and term not in terms:
            terms.append(term)
    return terms
