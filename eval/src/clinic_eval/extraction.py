"""Scoring extracted facts against hand-written gold facts.

    eval/extraction/<case>/transcript.tsv   speaker<TAB>text, one turn per line
    eval/extraction/<case>/gold.json        the facts a careful clinician would record

A fact matches a gold item when the category agrees (complaint and symptom count
as one), the name matches one of the gold names, and the assertion is one the gold
item accepts. Each gold item and each extracted fact can be matched once.

A fact that repeats one already matched (the complaint "Herzrasen" also listed as
a symptom) is counted as a duplicate, not as an error, for every system alike.

Besides precision and recall, two error types are counted on their own because
they are the dangerous ones:
- assertion errors: the right thing with the wrong present/absent/past
  ("Brustschmerzen" recorded as present when the patient denied them);
- missed contradictions: a change of story the doctor should have been shown.
"""

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from clinic_eval.fixtures import EVAL_DIR

EXTRACTION_DIR = EVAL_DIR / "extraction"
SPEAKER_NAMES = {"arzt": "Doctor", "patient": "Patient", "begleitung": "Companion", "dolmetscher": "Interpreter"}


@dataclass
class GoldFact:
    category: str
    options: list[tuple[str, str]]  # acceptable (name, assertion) pairs

    @property
    def label(self) -> str:
        return self.options[0][0]


@dataclass
class ExtractionCase:
    name: str
    lines: list[tuple[str, str]]  # (speaker, text)
    gold: list[GoldFact]
    contradictions: list[str]


def _sources() -> list[tuple[str, Path, Path]]:
    """(name, transcript, gold): the extraction cases, plus every role-play script
    with gold facts, since a script is a perfect transcript of its consultation."""
    found = [(d.name, d / "transcript.tsv", d / "gold.json") for d in sorted(EXTRACTION_DIR.iterdir()) if (d / "gold.json").exists()]
    for script in sorted((EVAL_DIR / "scripts").glob("*.tsv")):
        gold = script.with_name(script.stem + ".gold.json")
        if gold.exists():
            found.append((script.stem, script, gold))
    return found


def load_cases(names: list[str] | None = None) -> list[ExtractionCase]:
    cases = []
    for name, transcript, gold_file in _sources():
        if names and name not in names:
            continue
        lines = []
        for raw in transcript.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.startswith("#"):
                speaker, _, text = raw.partition("\t")
                lines.append((speaker.strip(), text.strip()))
        data = json.loads(gold_file.read_text(encoding="utf-8"))
        gold = []
        for item in data["facts"]:
            options = [tuple(p) for p in item["match"]] if "match" in item else [
                (n, a) for n in item["names"] for a in item["assertions"]
            ]
            gold.append(GoldFact(item["category"], options))
        cases.append(ExtractionCase(name, lines, gold, data.get("contradictions", [])))
    return cases


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^\wäöüß]+", " ", name.lower()).split())


def _same_category(a: str, b: str) -> bool:
    group = {"complaint": "symptom"}
    return group.get(a, a) == group.get(b, b)


def names_match(extracted: str, gold: str) -> bool:
    a, b = _norm(extracted), _norm(gold)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)) or SequenceMatcher(None, a, b).ratio() >= 0.85


@dataclass
class FactScore:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    assertion_errors: int = 0
    duplicates: int = 0
    wrong: list[str] = field(default_factory=list)  # extracted facts that matched nothing
    missed: list[str] = field(default_factory=list)  # gold facts nobody found

    @property
    def precision(self) -> float | None:
        n = self.true_positives + self.false_positives
        return self.true_positives / n if n else None

    @property
    def recall(self) -> float | None:
        n = self.true_positives + self.false_negatives
        return self.true_positives / n if n else None

    def add(self, other: "FactScore") -> None:
        self.true_positives += other.true_positives
        self.false_positives += other.false_positives
        self.false_negatives += other.false_negatives
        self.assertion_errors += other.assertion_errors
        self.duplicates += other.duplicates
        self.wrong += other.wrong
        self.missed += other.missed


def score_facts(extracted: list[dict], gold: list[GoldFact]) -> FactScore:
    """extracted: dicts with category, name, assertion."""
    score = FactScore()
    used: set[int] = set()
    def matches(fact: dict, g: GoldFact) -> bool:
        return _same_category(fact["category"], g.category) and any(
            names_match(fact["name"], n) and fact["assertion"] == a for n, a in g.options
        )

    for fact in extracted:
        hit = next((i for i, g in enumerate(gold) if i not in used and matches(fact, g)), None)
        if hit is not None:
            used.add(hit)
            score.true_positives += 1
            continue
        if any(matches(fact, gold[i]) for i in used):
            score.duplicates += 1
            continue
        score.false_positives += 1
        label = f"{fact['category']} '{fact['name']}' {fact['assertion']}"
        # Right thing, wrong present/absent/past: the dangerous kind of error.
        if any(
            _same_category(fact["category"], g.category) and any(names_match(fact["name"], n) for n, _ in g.options)
            for i, g in enumerate(gold)
            if i not in used
        ):
            score.assertion_errors += 1
            label += "  ← wrong assertion"
        score.wrong.append(label)
    for i, g in enumerate(gold):
        if i not in used:
            score.false_negatives += 1
            score.missed.append(f"{g.category} '{g.label}' {g.options[0][1]}")
    return score


def score_contradictions(flagged: list[str], expected: list[str]) -> tuple[int, int]:
    """(found, expected)"""
    found = sum(1 for e in expected if any(names_match(f, e) for f in flagged))
    return found, len(expected)


@dataclass
class VerifierScore:
    """How good the verifier's rejections are. A proposed fact is "wrong" if it
    matches no gold fact (duplicates of a matched fact are not counted either way).

    precision: of the facts it rejected, the share that really were wrong;
    recall:    of the wrong facts the extractor proposed, the share it rejected.
    """

    rejected: int = 0
    rejected_wrong: int = 0
    proposed_wrong: int = 0

    @property
    def precision(self) -> float | None:
        return self.rejected_wrong / self.rejected if self.rejected else None

    @property
    def recall(self) -> float | None:
        return self.rejected_wrong / self.proposed_wrong if self.proposed_wrong else None

    def add(self, other: "VerifierScore") -> None:
        self.rejected += other.rejected
        self.rejected_wrong += other.rejected_wrong
        self.proposed_wrong += other.proposed_wrong


def score_verifier(kept: list[dict], rejected: list[dict], gold: list[GoldFact]) -> VerifierScore:
    def is_right(fact: dict) -> bool:
        return any(
            _same_category(fact["category"], g.category)
            and any(names_match(fact["name"], n) and fact["assertion"] == a for n, a in g.options)
            for g in gold
        )

    wrong_rejected = sum(1 for f in rejected if not is_right(f))
    wrong_kept = sum(1 for f in kept if not is_right(f))
    return VerifierScore(
        rejected=len(rejected), rejected_wrong=wrong_rejected, proposed_wrong=wrong_rejected + wrong_kept
    )
