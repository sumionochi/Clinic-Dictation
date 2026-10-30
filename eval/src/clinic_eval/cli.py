"""clinic-eval: measure speech recognition accuracy on evaluation cases.

    uv run clinic-eval synth                          render synthetic smoke-test cases
    uv run clinic-eval add NAME --audio FILE          add a role-play recording from a file
    uv run clinic-eval add NAME --latest              add the latest recording made in the app
    uv run clinic-eval add NAME --session SESSION_ID  add a specific one recorded in the app
    uv run clinic-eval run [--model REPO] [CASE ...]  transcribe, score, save results
    uv run clinic-eval extract [--llm MODEL]...       agents vs one-prompt baseline on gold facts
    uv run clinic-eval fhir RESULTS.json              agents' facts → FHIR bundles, checked by the HL7 validator
"""

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from clinic_eval.fixtures import EVAL_DIR, add_case, list_cases, make_synthetic, pcm_from_app, to_pcm16
from clinic_eval.metrics import CaseScore, Summary, score_case, score_speakers, summarize
from clinic_eval.normalize import load_terms

RESULTS_DIR = EVAL_DIR / "results"
APP_MODELS_DIR = Path.home() / "Library" / "Application Support" / "clinic-dictation" / "models"


def _pct(x: float | None) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}%"


def _recall(found: int, expected: int) -> str:
    return f"{found}/{expected}" if expected else "-"


def cmd_synth(_args: argparse.Namespace) -> None:
    for case in make_synthetic():
        print(f"wrote {case.dir.relative_to(EVAL_DIR)}")


def cmd_add(args: argparse.Namespace) -> None:
    pcm = to_pcm16(Path(args.audio)) if args.audio else pcm_from_app(args.session)  # None = latest
    case = add_case(args.name, pcm)
    print(f"wrote {case.audio_path.relative_to(EVAL_DIR)} ({len(pcm) / 32000:.1f} s)")
    print(f"now write what was said into {case.reference_path.relative_to(EVAL_DIR)}")


def cmd_run(args: argparse.Namespace) -> None:
    # Use the models the app already downloaded, and never fetch anything new silently.
    os.environ.setdefault("HF_HOME", str(APP_MODELS_DIR))
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    import numpy as np
    from clinic_sidecar.diarizer import SortformerDiarizer
    from clinic_sidecar.live import live_transcript
    from clinic_sidecar.speaker_merge import split_by_speaker
    from clinic_sidecar.transcriber import FINAL_MODEL, TranscriptionWorker, WhisperTranscriber

    cases = list_cases(args.cases)
    if not cases:
        raise SystemExit("No cases in eval/fixtures. Run `clinic-eval synth` or `clinic-eval add`.")
    terms = load_terms(EVAL_DIR / "terms" / "medical_de.txt")
    negations = load_terms(EVAL_DIR / "terms" / "negation_de.txt")
    model = args.model or FINAL_MODEL
    transcriber = WhisperTranscriber(model)
    diarizer = None if args.no_speakers else SortformerDiarizer()
    worker = TranscriptionWorker()
    language = None if args.language == "auto" else args.language

    # Load the model first so the first case's timing measures transcription, not loading.
    load_start = time.monotonic()
    silence = np.zeros(16_000, dtype=np.float32)
    worker.submit(lambda: transcriber.transcribe(silence, language)).result()
    if diarizer:
        worker.submit(lambda: diarizer.diarize(silence)).result()
    load_s = time.monotonic() - load_start

    scores: list[CaseScore] = []
    rows: list[dict] = []
    refs: list[str] = []
    hyps: list[str] = []
    for case in cases:
        audio = case.audio()
        start = time.monotonic()
        segments = worker.submit(lambda a=audio: transcriber.transcribe(a, language)).result()
        elapsed = time.monotonic() - start
        hypothesis = " ".join(s.text for s in segments)
        reference = case.reference_text()
        score = score_case(case.name, reference, hypothesis, terms, negations)
        live = None
        if not args.no_live:
            # The live pass: the same audio in ~20 s windows, as during recording.
            live_segments = worker.submit(lambda a=audio: live_transcript(transcriber, a, language)).result()
            live_text = " ".join(s.text for s in live_segments)
            live_score = score_case(case.name, reference, live_text, terms, negations)
            live = {
                "wer": live_score.wer,
                "term_recall": live_score.terms.recall,
                "negation_recall": live_score.negations.recall,
                "hypothesis": live_text,
            }
        speakers = diarize_s = None
        if diarizer:
            start = time.monotonic()
            turns = worker.submit(lambda a=audio: diarizer.diarize(a)).result()
            diarize_s = round(time.monotonic() - start, 2)
            labelled = split_by_speaker(segments, turns)
            speakers = score_speakers(
                [(t.speaker, t.text) for t in case.turns()], [(spk, s.text) for s, spk in labelled]
            )
        scores.append(score)
        refs.append(reference)
        hyps.append(hypothesis)
        rows.append(
            {
                "score": asdict(score),
                "audio_s": round(audio.size / 16_000, 2),
                "transcribe_s": round(elapsed, 2),
                "diarize_s": diarize_s,
                "live": live,
                "speakers": asdict(speakers) if speakers else None,
                "reference": reference,
                "hypothesis": hypothesis,
            }
        )
    worker.shutdown()
    summary = summarize(scores, refs, hyps)
    audio_total = sum(r["audio_s"] for r in rows)
    transcribe_total = sum(r["transcribe_s"] for r in rows)

    _print_report(model, rows, scores, summary, audio_total, transcribe_total, load_s)
    path = _save(model, language, rows, summary, audio_total, transcribe_total, load_s)
    print(f"\nsaved {path.relative_to(EVAL_DIR)}")


def _print_report(model, rows, scores, summary: Summary, audio_total, transcribe_total, load_s) -> None:
    print(f"\nModel: {model}   (load {load_s:.1f} s)\n")
    print(
        f"{'case':28s} {'audio':>6s} {'time':>6s} {'WER':>6s} {'CER':>6s} {'terms':>7s} {'negations':>9s}"
        f" {'speakers':>9s} {'voices':>6s} {'liveWER':>7s}"
    )
    for row, s in zip(rows, scores):
        spk = row["speakers"]
        print(
            f"{s.case:28s} {row['audio_s']:5.1f}s {row['transcribe_s']:5.1f}s {_pct(s.wer)} {_pct(s.cer)}"
            f" {_recall(s.terms.found, s.terms.expected):>7s} {_recall(s.negations.found, s.negations.expected):>9s}"
            f" {_pct(spk['accuracy']) if spk else '   -':>9s}"
            f" {str(spk['voices_detected']) + '/' + str(spk['speakers_in_reference']) if spk else '-':>6s}"
            f" {_pct(row['live']['wer']) if row['live'] else '    -':>7s}"
        )
    compared = [r["speakers"] for r in rows if r["speakers"] and r["speakers"]["accuracy"] is not None]
    pooled_speakers = (
        sum(c["accuracy"] * c["words"] for c in compared) / sum(c["words"] for c in compared) if compared else None
    )
    print(
        f"{'ALL (' + str(summary.cases) + ' cases, ' + str(summary.words) + ' words)':28s}"
        f" {audio_total:5.1f}s {transcribe_total:5.1f}s {_pct(summary.wer)} {_pct(summary.cer)}"
        f" {_pct(summary.term_recall):>7s} {_pct(summary.negation_recall):>9s} {_pct(pooled_speakers):>9s}"
    )
    live_rows = [r for r in rows if r["live"]]
    if live_rows:
        import jiwer

        from clinic_eval.normalize import normalize

        live_wer = jiwer.wer(
            [normalize(r["reference"]) for r in live_rows], [normalize(r["live"]["hypothesis"]) for r in live_rows]
        )
        final_wer = jiwer.wer(
            [normalize(r["reference"]) for r in live_rows], [normalize(r["hypothesis"]) for r in live_rows]
        )
        print(f"\nLive pass (20 s windows): WER {_pct(live_wer)} vs final {_pct(final_wer)}")
    diarize_total = sum(r["diarize_s"] or 0 for r in rows)
    if diarize_total:
        print(f"\nTelling speakers apart took {diarize_total:.1f} s in total.")
    if transcribe_total:
        print(f"\nSpeed: {audio_total / transcribe_total:.1f}x real time")
    misses = [(s.case, "term", m) for s in scores for m in s.terms.misses] + [
        (s.case, "negation", m) for s in scores for m in s.negations.misses
    ]
    if misses:
        print("\nMissed:")
        for case, kind, m in misses:
            print(f"  {case}: {kind} '{m.term}' heard as '{m.heard or '(nothing)'}'")


def _save(model, language, rows, summary: Summary, audio_total, transcribe_total, load_s) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = RESULTS_DIR / f"{stamp}-{model.split('/')[-1]}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "language": language,
                "run_at": datetime.now().isoformat(timespec="seconds"),
                "model_load_s": round(load_s, 2),
                "audio_s": round(audio_total, 2),
                "transcribe_s": round(transcribe_total, 2),
                "summary": asdict(summary),
                "cases": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def cmd_extract(args: argparse.Namespace) -> None:
    from clinic_eval.extraction import SPEAKER_NAMES, FactScore, load_cases, score_contradictions, score_facts
    from clinic_sidecar.agents.baseline import baseline_facts
    from clinic_sidecar.agents.graph import extract_facts
    from clinic_sidecar.agents.llm import OllamaLLM
    from clinic_sidecar.agents.schema import TranscriptLine

    cases = load_cases(args.cases or None)
    report: dict = {"run_at": datetime.now().isoformat(timespec="seconds"), "models": {}}
    for model in args.llm or ["qwen3:8b"]:
        print(f"\n=== {model} ===")
        print(f"{'case':28s} {'system':9s} {'prec':>6s} {'recall':>6s} {'flips':>5s} {'contra':>6s} {'time':>6s} {'calls':>5s}")
        totals = {"baseline": FactScore(), "agents": FactScore()}
        contra = {"baseline": [0, 0], "agents": [0, 0]}
        seconds = {"baseline": 0.0, "agents": 0.0}
        rows = []
        for case in cases:
            lines = [
                TranscriptLine(line=i + 1, speaker=SPEAKER_NAMES.get(spk, spk.title()), text=text)
                for i, (spk, text) in enumerate(case.lines)
            ]
            llm = OllamaLLM(model)
            plain, base_s = baseline_facts(lines, llm)
            result = extract_facts(lines, llm)
            outputs = {
                "baseline": ([p.model_dump() for p in plain], [], base_s, 1),
                "agents": (
                    [x.model_dump() for x in result.facts],
                    [c.name for c in result.contradictions],
                    result.seconds,
                    result.llm_calls,
                ),
            }
            row = {"case": case.name}
            for system, (facts, flagged, secs, calls) in outputs.items():
                sc = score_facts(facts, case.gold)
                found, expected = score_contradictions(flagged, case.contradictions)
                totals[system].add(sc)
                contra[system][0] += found
                contra[system][1] += expected
                seconds[system] += secs
                print(
                    f"{case.name:28s} {system:9s} {_pct(sc.precision)} {_pct(sc.recall)} {sc.assertion_errors:5d}"
                    f" {f'{found}/{expected}' if expected else '-':>6s} {secs:5.1f}s {calls:5d}"
                )
                row[system] = {
                    "facts": facts,
                    "contradictions": flagged,
                    "seconds": secs,
                    "llm_calls": calls,
                    "precision": sc.precision,
                    "recall": sc.recall,
                    "assertion_errors": sc.assertion_errors,
                    "duplicates": sc.duplicates,
                    "wrong": sc.wrong,
                    "missed": sc.missed,
                }
            row["agents"]["rejected"] = [{"fact": r.fact.model_dump(), "reason": r.reason} for r in result.rejected]
            row["agents"]["log"] = result.log
            rows.append(row)
        for system in ("baseline", "agents"):
            t = totals[system]
            c = contra[system]
            print(
                f"{'ALL':28s} {system:9s} {_pct(t.precision)} {_pct(t.recall)} {t.assertion_errors:5d}"
                f" {f'{c[0]}/{c[1]}' if c[1] else '-':>6s} {seconds[system]:5.1f}s"
            )
        for system in ("baseline", "agents"):
            if totals[system].wrong or totals[system].missed:
                print(f"\n{system}:")
                for w in totals[system].wrong:
                    print(f"  extra   {w}")
                for m in totals[system].missed:
                    print(f"  missed  {m}")
        report["models"][model] = {
            "cases": rows,
            "totals": {
                s: {
                    "precision": totals[s].precision,
                    "recall": totals[s].recall,
                    "assertion_errors": totals[s].assertion_errors,
                    "contradictions_found": contra[s][0],
                    "contradictions_expected": contra[s][1],
                    "seconds": round(seconds[s], 1),
                }
                for s in totals
            },
        }
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M')}-extraction.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved {path.relative_to(EVAL_DIR)}")


def cmd_fhir(args: argparse.Namespace) -> None:
    from clinic_eval.extraction import SPEAKER_NAMES, load_cases
    from clinic_sidecar.agents.graph import Contradiction
    from clinic_sidecar.agents.schema import Fact, TranscriptLine
    from clinic_sidecar.fhir.mapper import DocumentInput, build_document
    from clinic_sidecar.fhir.validator import validate

    source = Path(args.results)
    report = json.loads(source.read_text(encoding="utf-8"))
    model = args.llm or next(iter(report["models"]))
    cases = {c.name: c for c in load_cases()}
    out_dir = RESULTS_DIR / "fhir"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Facts from {source.name} ({model})\n")
    print(f"{'case':28s} {'facts':>5s} {'resources':>9s} {'errors':>6s} {'warnings':>8s}")
    failed = False
    for row in report["models"][model]["cases"]:
        case = cases[row["case"]]
        lines = [
            TranscriptLine(line=i + 1, speaker=SPEAKER_NAMES.get(spk, spk.title()), text=text)
            for i, (spk, text) in enumerate(case.lines)
        ]
        facts = [Fact.model_validate(f) for f in row["agents"]["facts"]]
        flagged = set(row["agents"]["contradictions"])
        contradictions = [
            Contradiction(
                category=group[0].category,
                name=name,
                assertions=sorted({f.assertion for f in group}),
                lines=sorted({e.line for f in group for e in f.evidence}),
            )
            for name in flagged
            if (group := [f for f in facts if f.name == name])
        ]
        bundle = build_document(
            DocumentInput(f"eval-{case.name}", facts, lines, contradictions, extraction_model=model)
        )
        result = validate(bundle)
        failed |= not result.valid
        (out_dir / f"{case.name}.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{case.name:28s} {len(facts):5d} {len(bundle['entry']):9d} {len(result.errors):6d} {len(result.warnings):8d}")
        for issue in result.errors + result.warnings:
            print(f"    {issue.severity}: {issue.location}: {issue.message[:160]}")
    print(f"\nbundles saved in {out_dir.relative_to(EVAL_DIR)}/")
    if failed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="clinic-eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)

    synth = sub.add_parser("synth", help="render eval/synthetic/*.tsv into fixtures")
    synth.set_defaults(func=cmd_synth)

    add = sub.add_parser("add", help="add a recording as an evaluation case")
    add.add_argument("name")
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--audio", help="any audio file ffmpeg can read")
    source.add_argument("--session", help="session id of a consultation recorded in the app")
    source.add_argument("--latest", action="store_true", help="the most recent recording made in the app")
    add.set_defaults(func=cmd_add)

    run = sub.add_parser("run", help="transcribe all cases and score them")
    run.add_argument("cases", nargs="*", help="case names (default: all)")
    run.add_argument("--model", help="Hugging Face repo of an MLX Whisper model (default: the app's final model)")
    run.add_argument("--language", default="de", choices=["de", "en", "auto"])
    run.add_argument("--allow-download", action="store_true", help="let the model be downloaded if missing")
    run.add_argument("--no-speakers", action="store_true", help="skip speaker diarization")
    run.add_argument("--no-live", action="store_true", help="skip the windowed live pass")
    run.set_defaults(func=cmd_run)

    extract = sub.add_parser("extract", help="score the extraction agents against the one-prompt baseline")
    extract.add_argument("cases", nargs="*", help="case names under eval/extraction (default: all)")
    extract.add_argument("--llm", action="append", help="Ollama model; repeat to compare several (default: qwen3:8b)")
    extract.set_defaults(func=cmd_extract)

    fhir = sub.add_parser("fhir", help="turn the agents' facts from an extract run into validated FHIR bundles")
    fhir.add_argument("results", help="results/<date>-extraction.json from `clinic-eval extract`")
    fhir.add_argument("--llm", help="which model's facts to use (default: the first in the file)")
    fhir.set_defaults(func=cmd_fhir)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
