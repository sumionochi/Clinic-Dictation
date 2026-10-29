"""Deterministic evidence check: does the quoted text really appear on the cited line?

This runs before any model is asked to judge anything. A quote the model made up
is caught here for free.

Models often copy the words right but miscount the line ("Fieber habe ich nicht"
cited as line 7 when it is line 8). Since the quote is still verbatim, the citation
is moved to the nearest line that really contains it. That keeps the guarantee
that every fact points at words actually said.
"""

import re

from clinic_sidecar.agents.schema import Evidence, Fact, TranscriptLine

RELOCATE_WITHIN = 3  # how far a miscounted line number may be off

_NOT_WORD = re.compile(r"[^\wäöüß]+")


def _norm(text: str) -> str:
    return " ".join(_NOT_WORD.sub(" ", text.lower()).split())


def evidence_problems(fact: Fact, lines: dict[int, TranscriptLine]) -> list[str]:
    """Empty list if every quote is found on its line (ignoring case and punctuation)."""
    problems: list[str] = []
    for ev in fact.evidence:
        line = lines.get(ev.line)
        if line is None:
            problems.append(f"line {ev.line} does not exist")
        elif not _norm(ev.quote):
            problems.append(f"empty quote for line {ev.line}")
        elif _norm(ev.quote) not in _norm(line.text):
            problems.append(f"quote '{ev.quote}' is not on line {ev.line}")
    return problems


def relocate_evidence(fact: Fact, lines: dict[int, TranscriptLine]) -> tuple[Fact, list[str]]:
    """Move each quote that is not on its cited line to the nearest line (within
    RELOCATE_WITHIN) where it appears verbatim. Returns the fixed fact and notes."""
    notes: list[str] = []
    fixed: list[Evidence] = []
    for ev in fact.evidence:
        quote = _norm(ev.quote)
        here = lines.get(ev.line)
        if not quote or (here is not None and quote in _norm(here.text)):
            fixed.append(ev)
            continue
        candidates = [
            n for n in sorted(lines, key=lambda n: abs(n - ev.line))
            if abs(n - ev.line) <= RELOCATE_WITHIN and quote in _norm(lines[n].text)
        ]
        if candidates:
            fixed.append(Evidence(line=candidates[0], quote=ev.quote))
            notes.append(f"'{ev.quote}' moved from line {ev.line} to {candidates[0]}")
        else:
            fixed.append(ev)
    return fact.model_copy(update={"evidence": fixed}), notes


def cited_context(evidence: list[Evidence], lines: dict[int, TranscriptLine], around: int = 1) -> list[TranscriptLine]:
    """The cited lines plus their neighbours: a "Nein." only means something next to its question."""
    wanted: set[int] = set()
    for ev in evidence:
        wanted.update(range(ev.line - around, ev.line + around + 1))
    return [lines[n] for n in sorted(wanted) if n in lines]
