"""The comparison point: one prompt, same model, same fact format, no evidence
and no verification. If the agents are not clearly better than this, they are
not worth their extra calls."""

import time

from clinic_sidecar.agents import prompts
from clinic_sidecar.agents.llm import LLM
from clinic_sidecar.agents.schema import PlainFact, PlainFacts, TranscriptLine


def baseline_facts(lines: list[TranscriptLine], llm: LLM) -> tuple[list[PlainFact], float]:
    start = time.monotonic()
    out = llm.structured("baseline", prompts.BASELINE_SYSTEM, prompts.extract_user(lines), PlainFacts)
    return out.facts, round(time.monotonic() - start, 2)
