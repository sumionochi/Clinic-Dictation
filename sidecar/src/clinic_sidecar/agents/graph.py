"""The extraction pipeline as a LangGraph graph.

    extract ─► check ─► verify ─┬─► finalize
                ▲               │
                └──── retry ◄───┘  (rejected facts, with the reason, at most MAX_RETRIES times)

- extract: the LLM proposes facts, each with quoted evidence.
- check:   deterministic, the quote must really be on the cited line (a miscounted
           line number is corrected when the exact words are on a nearby line).
- verify:  a second LLM call per fact judges it against the cited lines and their
           neighbours. A separate call, so the model is not grading its own answer
           in the same breath.
- retry:   facts that failed go back to the extractor with the reason.
- finalize: merges duplicates. Facts that still fail are returned as rejected,
           with the reason, never silently dropped.

Contradictions (the same thing stated as present and as past/absent, both with
real evidence) are collected at the check step, before the verifier can reject
one side. The AI never resolves a contradiction; the doctor sees both.
"""

import time
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, StateGraph
from pydantic import Field, create_model

from clinic_sidecar.agents import prompts
from clinic_sidecar.agents.evidence import cited_context, evidence_problems, relocate_evidence
from clinic_sidecar.agents.llm import LLM, LLMError
from clinic_sidecar.agents.schema import Fact, Facts, TranscriptLine, Verification

MAX_RETRIES = 1


@dataclass
class Rejected:
    fact: Fact
    reason: str


@dataclass
class Contradiction:
    category: str
    name: str
    assertions: list[str]
    lines: list[int]


@dataclass
class ExtractionResult:
    facts: list[Fact]
    rejected: list[Rejected]
    contradictions: list[Contradiction]
    rounds: int
    seconds: float
    llm_calls: int
    model: str
    log: list[str] = field(default_factory=list)


class State(TypedDict):
    lines: list[TranscriptLine]
    candidates: list[Fact]
    accepted: list[Fact]
    failed: list[tuple[Fact, str]]
    evidenced: list[Fact]  # every fact that passed the evidence check, any round
    retries: int
    log: list[str]


def build_graph(llm: LLM):
    def by_number(state: State) -> dict[int, TranscriptLine]:
        return {ln.line: ln for ln in state["lines"]}

    def extract(state: State) -> dict:
        out = llm.structured(
            "extract", prompts.EXTRACT_SYSTEM, prompts.extract_user(state["lines"]), Facts
        )
        return {"candidates": out.facts, "log": state["log"] + [f"extract: {len(out.facts)} facts"]}

    def retry(state: State) -> dict:
        # At most one corrected fact per rejected one: a small model asked for "the
        # corrected facts" will otherwise pad the list up to the schema maximum.
        limited = create_model(
            "CorrectedFacts", facts=(list[Fact], Field(max_length=len(state["failed"])))
        )
        try:
            out = llm.structured(
                "retry", prompts.EXTRACT_SYSTEM, prompts.retry_user(state["lines"], state["failed"]), limited
            )
        except LLMError as exc:
            # Keep everything already verified; the failed facts stay rejected.
            return {
                "retries": state["retries"] + 1,
                "log": state["log"] + [f"retry {state['retries'] + 1} failed: {exc}"],
            }
        return {
            "candidates": out.facts,
            "failed": [],
            "retries": state["retries"] + 1,
            "log": state["log"] + [f"retry {state['retries'] + 1}: {len(state['failed'])} sent back, {len(out.facts)} returned"],
        }

    def check(state: State) -> dict:
        lines = by_number(state)
        passed: list[Fact] = []
        failed = list(state["failed"])
        moved = 0
        for fact in state["candidates"]:
            fact, notes = relocate_evidence(fact, lines)
            moved += len(notes)
            problems = evidence_problems(fact, lines)
            if problems:
                failed.append((fact, "Evidence check: " + "; ".join(problems)))
            else:
                passed.append(fact)
        return {
            "candidates": passed,
            "failed": failed,
            "evidenced": state["evidenced"] + passed,
            "log": state["log"]
            + [f"check: {len(passed)} passed, {len(failed) - len(state['failed'])} bad quotes, {moved} line numbers corrected"],
        }

    def verify(state: State) -> dict:
        lines = by_number(state)
        accepted = list(state["accepted"])
        failed = list(state["failed"])
        for fact in state["candidates"]:
            context = cited_context(fact.evidence, lines)
            try:
                v = llm.structured(
                    "verify", prompts.VERIFY_SYSTEM, prompts.verify_user(context, fact), Verification, max_tokens=400
                )
            except LLMError as exc:
                failed.append((fact, f"Verifier failed: {exc}"))
                continue
            if v.verdict == "supported":
                accepted.append(fact)
            else:
                failed.append((fact, f"Verifier: {v.verdict}: {v.reason}"))
        new_failures = failed[len(state["failed"]) :]
        return {
            "candidates": [],
            "accepted": accepted,
            "failed": failed,
            "log": state["log"]
            + [f"verify: {len(accepted) - len(state['accepted'])} supported, {len(new_failures)} rejected"]
            + [f"  rejected {f.category} '{f.name}' {f.assertion} (lines {[e.line for e in f.evidence]}): {why}" for f, why in new_failures],
        }

    def after_verify(state: State) -> str:
        return "retry" if state["failed"] and state["retries"] < MAX_RETRIES else END

    graph = StateGraph(State)
    graph.add_node("extract", extract)
    graph.add_node("check", check)
    graph.add_node("verify", verify)
    graph.add_node("retry", retry)
    graph.set_entry_point("extract")
    graph.add_edge("extract", "check")
    graph.add_edge("check", "verify")
    graph.add_conditional_edges("verify", after_verify, {"retry": "retry", END: END})
    graph.add_edge("retry", "check")
    return graph.compile()


def _key(f: Fact) -> tuple[str, str]:
    return f.category, f.name.strip().lower()


def find_contradictions(facts: list[Fact]) -> list[Contradiction]:
    """The same thing with different assertions, e.g. Metoprolol "abgesetzt" and
    later "nehme ich noch". Shown to the doctor, never resolved here."""
    groups: dict[tuple[str, str], list[Fact]] = {}
    for f in facts:
        groups.setdefault(_key(f), []).append(f)
    return [
        Contradiction(
            category=group[0].category,
            name=group[0].name,
            assertions=sorted({f.assertion for f in group}),
            lines=sorted({e.line for f in group for e in f.evidence}),
        )
        for group in groups.values()
        if len({f.assertion for f in group}) > 1
    ]


def finalize(accepted: list[Fact]) -> list[Fact]:
    """Merge facts stated twice, keeping all their evidence."""
    merged: dict[tuple[str, str, str], Fact] = {}
    for f in accepted:
        k = (*_key(f), f.assertion)
        if k in merged:
            seen = {(e.line, e.quote) for e in merged[k].evidence}
            merged[k].evidence.extend(e for e in f.evidence if (e.line, e.quote) not in seen)
            merged[k].details = merged[k].details or f.details
        else:
            merged[k] = f.model_copy(deep=True)
    return list(merged.values())


def extract_facts(lines: list[TranscriptLine], llm: LLM) -> ExtractionResult:
    start = time.monotonic()
    calls_before = llm.log.calls
    state = build_graph(llm).invoke(
        {"lines": lines, "candidates": [], "accepted": [], "failed": [], "evidenced": [], "retries": 0, "log": []}
    )
    facts = finalize(state["accepted"])
    contradictions = find_contradictions(state["evidenced"])
    return ExtractionResult(
        facts=facts,
        rejected=[Rejected(f, reason) for f, reason in state["failed"]],
        contradictions=contradictions,
        rounds=state["retries"] + 1,
        seconds=round(time.monotonic() - start, 2),
        llm_calls=llm.log.calls - calls_before,
        model=llm.model,
        log=state["log"],
    )
