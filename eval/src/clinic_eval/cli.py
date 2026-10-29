"""clinic-eval: measure speech recognition accuracy on evaluation cases.

    uv run clinic-eval synth                          render synthetic smoke-test cases
    uv run clinic-eval add NAME --audio FILE          add a role-play recording from a file
    uv run clinic-eval add NAME --latest              add the latest recording made in the app
    uv run clinic-eval add NAME --session SESSION_ID  add a specific one recorded in the app
    uv run clinic-eval run [--model REPO] [CASE ...]  transcribe, score, save results
"""

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from clinic_eval.fixtures import EVAL_DIR, add_case, list_cases, make_synthetic, pcm_from_app, to_pcm16
from clinic_eval.metrics import CaseScore, Summary, score_case, summarize
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
    from clinic_sidecar.transcriber import FINAL_MODEL, TranscriptionWorker, WhisperTranscriber

    cases = list_cases(args.cases)
    if not cases:
        raise SystemExit("No cases in eval/fixtures. Run `clinic-eval synth` or `clinic-eval add`.")
    terms = load_terms(EVAL_DIR / "terms" / "medical_de.txt")
    negations = load_terms(EVAL_DIR / "terms" / "negation_de.txt")
    model = args.model or FINAL_MODEL
    transcriber = WhisperTranscriber(model)
    worker = TranscriptionWorker()
    language = None if args.language == "auto" else args.language

    # Load the model first so the first case's timing measures transcription, not loading.
    load_start = time.monotonic()
    worker.submit(lambda: transcriber.transcribe(np.zeros(16_000, dtype=np.float32), language)).result()
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
        scores.append(score)
        refs.append(reference)
        hyps.append(hypothesis)
        rows.append(
            {
                "score": asdict(score),
                "audio_s": round(audio.size / 16_000, 2),
                "transcribe_s": round(elapsed, 2),
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
    print(f"{'case':28s} {'audio':>6s} {'time':>6s} {'WER':>6s} {'CER':>6s} {'terms':>7s} {'negations':>9s}")
    for row, s in zip(rows, scores):
        print(
            f"{s.case:28s} {row['audio_s']:5.1f}s {row['transcribe_s']:5.1f}s {_pct(s.wer)} {_pct(s.cer)}"
            f" {_recall(s.terms.found, s.terms.expected):>7s} {_recall(s.negations.found, s.negations.expected):>9s}"
        )
    print(
        f"{'ALL (' + str(summary.cases) + ' cases, ' + str(summary.words) + ' words)':28s}"
        f" {audio_total:5.1f}s {transcribe_total:5.1f}s {_pct(summary.wer)} {_pct(summary.cer)}"
        f" {_pct(summary.term_recall):>7s} {_pct(summary.negation_recall):>9s}"
    )
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
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
