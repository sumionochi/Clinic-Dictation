"""End-to-end evaluation: from audio to checked facts, every stage scored.

For each case in eval/fixtures/ that has audio, reference.tsv and gold facts:

    audio ──Whisper──► transcript ──Sortformer──► speakers ──agents──► facts
      │                    │                          │                  │
      time              WER, terms,               speaker            precision, recall,
                        negations                 accuracy           flips, contradictions,
                                                                     verifier precision/recall

Voices are matched to people the way a doctor would in the app: each detected voice
is named after the reference speaker it overlaps most.

Two comparisons come with every case:
- baseline: one prompt instead of the agents, on the same transcript;
- reference: the agents on the perfect reference transcript, so the gap shows how
  many facts were lost to speech and speaker errors rather than to extraction.

Gold facts: fixtures/<case>/gold.json, or for synthetic cases the matching
extraction/<name>/gold.json.
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from clinic_eval.extraction import (
    EXTRACTION_DIR,
    SPEAKER_NAMES,
    FactScore,
    GoldFact,
    VerifierScore,
    score_contradictions,
    score_facts,
    score_verifier,
)
from clinic_eval.fixtures import EVAL_DIR, Case, list_cases
from clinic_eval.metrics import score_case, score_speakers
from clinic_eval.normalize import load_terms

RESULTS_DIR = EVAL_DIR / "results"
APP_MODELS_DIR = Path.home() / "Library" / "Application Support" / "clinic-dictation" / "models"


def gold_path(case: Case) -> Path | None:
    own = case.dir / "gold.json"
    if own.exists():
        return own
    if case.name.startswith("synthetic-"):
        shared = EXTRACTION_DIR / case.name.removeprefix("synthetic-") / "gold.json"
        if shared.exists():
            return shared
    return None


def load_gold(path: Path) -> tuple[list[GoldFact], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    gold = [
        GoldFact(
            item["category"],
            [tuple(p) for p in item["match"]]
            if "match" in item
            else [(n, a) for n in item["names"] for a in item["assertions"]],
        )
        for item in data["facts"]
    ]
    return gold, data.get("contradictions", [])


@dataclass
class SystemResult:
    precision: float | None
    recall: float | None
    omission_rate: float | None
    unsupported_rate: float | None
    assertion_errors: int
    contradictions_found: int
    contradictions_expected: int
    seconds: float
    wrong: list[str]
    missed: list[str]


def _system(score: FactScore, found: int, expected: int, seconds: float) -> SystemResult:
    return SystemResult(
        precision=score.precision,
        recall=score.recall,
        omission_rate=None if score.recall is None else 1 - score.recall,
        unsupported_rate=None if score.precision is None else 1 - score.precision,
        assertion_errors=score.assertion_errors,
        contradictions_found=found,
        contradictions_expected=expected,
        seconds=round(seconds, 1),
        wrong=score.wrong,
        missed=score.missed,
    )


def run(case_names: list[str] | None, model: str, language: str | None, with_reference: bool) -> dict:
    os.environ.setdefault("HF_HOME", str(APP_MODELS_DIR))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import numpy as np
    from clinic_sidecar.agents.baseline import baseline_facts
    from clinic_sidecar.agents.graph import extract_facts
    from clinic_sidecar.agents.llm import OllamaLLM
    from clinic_sidecar.agents.schema import TranscriptLine
    from clinic_sidecar.diarizer import SortformerDiarizer
    from clinic_sidecar.speaker_merge import split_by_speaker
    from clinic_sidecar.transcriber import TranscriptionWorker, WhisperTranscriber

    cases = [c for c in list_cases(case_names) if gold_path(c) and c.audio_path.exists()]
    if not cases:
        raise SystemExit("No case has audio, reference.tsv and gold facts. See eval/README.md.")
    terms = load_terms(EVAL_DIR / "terms" / "medical_de.txt")
    negations = load_terms(EVAL_DIR / "terms" / "negation_de.txt")
    transcriber, diarizer, worker = WhisperTranscriber(), SortformerDiarizer(), TranscriptionWorker()
    silence = np.zeros(16_000, dtype=np.float32)
    worker.submit(lambda: (transcriber.transcribe(silence, language), diarizer.diarize(silence))).result()

    rows = []
    for case in cases:
        print(f"  {case.name} …", flush=True)
        gold, expected_contradictions = load_gold(gold_path(case))
        audio = case.audio()
        ref_turns = case.turns()

        t0 = time.monotonic()
        segments = worker.submit(lambda a=audio: transcriber.transcribe(a, language)).result()
        transcribe_s = time.monotonic() - t0
        t0 = time.monotonic()
        turns = worker.submit(lambda a=audio: diarizer.diarize(a)).result()
        diarize_s = time.monotonic() - t0
        labelled = split_by_speaker(segments, turns)

        asr = score_case(case.name, case.reference_text(), " ".join(s.text for s in segments), terms, negations)
        speakers = score_speakers([(t.speaker, t.text) for t in ref_turns], [(spk, s.text) for s, spk in labelled])
        lines = [
            TranscriptLine(
                line=i + 1,
                speaker=SPEAKER_NAMES.get(speakers.mapping.get(spk, ""), None) if spk else None,
                text=s.text,
            )
            for i, (s, spk) in enumerate(labelled)
        ]

        llm = OllamaLLM(model)
        result = extract_facts(lines, llm)
        kept = [f.model_dump() for f in result.facts]
        agents = _system(
            score_facts(kept, gold),
            *score_contradictions([c.name for c in result.contradictions], expected_contradictions),
            result.seconds,
        )
        verifier = score_verifier(kept, [r.fact.model_dump() for r in result.rejected], gold)
        plain, base_s = baseline_facts(lines, llm)
        baseline = _system(score_facts([p.model_dump() for p in plain], gold), 0, len(expected_contradictions), base_s)

        reference = None
        if with_reference:
            ref_lines = [
                TranscriptLine(line=i + 1, speaker=SPEAKER_NAMES.get(t.speaker, t.speaker.title()), text=t.text)
                for i, t in enumerate(ref_turns)
            ]
            ref_result = extract_facts(ref_lines, OllamaLLM(model))
            reference = _system(
                score_facts([f.model_dump() for f in ref_result.facts], gold),
                *score_contradictions([c.name for c in ref_result.contradictions], expected_contradictions),
                ref_result.seconds,
            )

        rows.append(
            {
                "case": case.name,
                "audio_s": round(audio.size / 16_000, 1),
                "transcribe_s": round(transcribe_s, 1),
                "diarize_s": round(diarize_s, 1),
                "wer": asr.wer,
                "term_recall": asr.terms.recall,
                "negation_recall": asr.negations.recall,
                "speaker_accuracy": speakers.accuracy,
                "voices": f"{speakers.voices_detected}/{speakers.speakers_in_reference}",
                "agents": asdict(agents),
                "verifier": {**asdict(verifier), "precision": verifier.precision, "recall": verifier.recall},
                "baseline": asdict(baseline),
                "agents_on_reference": asdict(reference) if reference else None,
                "stop_to_record_s": round(transcribe_s + diarize_s + result.seconds, 1),
                "transcript": [ln.model_dump() for ln in lines],
            }
        )
    worker.shutdown()
    return {"run_at": datetime.now().isoformat(timespec="seconds"), "model": model, "language": language, "cases": rows}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def report_markdown(data: dict) -> str:
    rows = data["cases"]
    out = [
        f"# End-to-end evaluation, {data['run_at'][:16].replace('T', ' ')}",
        "",
        f"Speech: Whisper large-v3 (MLX) · speakers: Sortformer · agents and baseline: {data['model']} (Ollama). "
        "All local on one MacBook Air M5 (16 GB).",
        "",
        "## Per case",
        "",
        "| Case | Audio | WER | Med. terms | Negations | Speakers | Agents P / R | Flips | Baseline P / R | Flips | Agents on perfect transcript P / R | Stop → record |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        a, b, ref = r["agents"], r["baseline"], r["agents_on_reference"]
        out.append(
            f"| {r['case']} | {r['audio_s']} s | {_pct(r['wer'])} | {_pct(r['term_recall'])} | {_pct(r['negation_recall'])} "
            f"| {_pct(r['speaker_accuracy'])} ({r['voices']}) | {_pct(a['precision'])} / {_pct(a['recall'])} | {a['assertion_errors']} "
            f"| {_pct(b['precision'])} / {_pct(b['recall'])} | {b['assertion_errors']} "
            f"| {_pct(ref['precision']) + ' / ' + _pct(ref['recall']) if ref else '–'} | {r['stop_to_record_s']} s |"
        )
    v = VerifierScore()
    for r in rows:
        v.add(VerifierScore(r["verifier"]["rejected"], r["verifier"]["rejected_wrong"], r["verifier"]["proposed_wrong"]))
    contra_found = sum(r["agents"]["contradictions_found"] for r in rows)
    contra_expected = sum(r["agents"]["contradictions_expected"] for r in rows)
    out += [
        "",
        "## Agents",
        "",
        f"- Verifier: {v.rejected} facts rejected, {v.rejected_wrong} of them really wrong "
        f"(precision {_pct(v.precision)}); it caught {v.rejected_wrong} of {v.proposed_wrong} wrong facts "
        f"(recall {_pct(v.recall)}).",
        f"- Contradictions flagged: {contra_found} of {contra_expected} expected.",
        "- Omission rate = 1 − recall; unsupported-claim rate = 1 − precision.",
        "",
        "## Errors, per case",
        "",
    ]
    for r in rows:
        out.append(f"### {r['case']}")
        for label, key in (("Agents", "agents"), ("Baseline", "baseline")):
            items = [f"extra: {w}" for w in r[key]["wrong"]] + [f"missed: {m}" for m in r[key]["missed"]]
            out.append(f"- {label}: " + ("; ".join(items) if items else "none"))
        out.append("")
    out += [
        "## Caveats",
        "",
        "- Cases whose name starts with `synthetic-` are computer voices: they check the pipeline, "
        "and say little about real accuracy, especially for speakers.",
        "- Voices are matched to people by overlap with the reference, as a doctor does in the app.",
    ]
    return "\n".join(out) + "\n"


def save(data: dict) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    js = RESULTS_DIR / f"{stamp}-e2e.json"
    md = RESULTS_DIR / f"{stamp}-e2e.md"
    js.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(report_markdown(data), encoding="utf-8")
    return js, md
