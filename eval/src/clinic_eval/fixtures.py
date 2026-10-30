"""Evaluation cases on disk.

    eval/fixtures/<case>/
        reference.tsv   what was actually said, one turn per line: speaker<TAB>text
        audio.wav       16 kHz mono 16-bit (never committed: *.wav is gitignored)

`reference.tsv` is written by a person from the role-play script, corrected for
anything the actors said differently. Never copy Whisper's output into it: a
reference built from the model's transcript makes the model look better than it is.
"""

import shutil
import sqlite3
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EVAL_DIR = Path(__file__).resolve().parents[2]
FIXTURES_DIR = EVAL_DIR / "fixtures"
SYNTHETIC_DIR = EVAL_DIR / "synthetic"
SAMPLE_RATE = 16_000
APP_DATA_DIR = Path.home() / "Library" / "Application Support" / "clinic-dictation" / "data"

REFERENCE_TEMPLATE = """\
# Reference transcript: exactly what was said, one turn per line.
# Format: speaker<TAB>text   (speakers: arzt, patient, begleitung, dolmetscher, other)
# Write it from the role-play script and fix anything said differently.
# Do not paste the app's transcript here; it would hide the model's errors.
"""


@dataclass
class Turn:
    speaker: str
    text: str


@dataclass
class Case:
    name: str
    dir: Path

    @property
    def audio_path(self) -> Path:
        return self.dir / "audio.wav"

    @property
    def reference_path(self) -> Path:
        return self.dir / "reference.tsv"

    def turns(self) -> list[Turn]:
        turns: list[Turn] = []
        for n, line in enumerate(self.reference_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            speaker, sep, text = line.partition("\t")
            if not sep or not text.strip():
                raise ValueError(f"{self.reference_path}:{n}: expected 'speaker<TAB>text'")
            turns.append(Turn(speaker.strip().lower(), text.strip()))
        return turns

    def reference_text(self) -> str:
        return " ".join(t.text for t in self.turns())

    def audio(self) -> np.ndarray:
        """Float32 samples in [-1, 1]."""
        with wave.open(str(self.audio_path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise ValueError(f"{self.audio_path}: must be 16 kHz mono 16-bit; re-add it with `clinic-eval add`")
            data = w.readframes(w.getnframes())
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def list_cases(names: list[str] | None = None) -> list[Case]:
    cases = [
        Case(d.name, d)
        for d in sorted(FIXTURES_DIR.iterdir())
        if d.is_dir() and (d / "reference.tsv").exists()
    ]
    if names:
        missing = set(names) - {c.name for c in cases}
        if missing:
            raise SystemExit(f"Unknown case(s): {', '.join(sorted(missing))}")
        cases = [c for c in cases if c.name in names]
    return cases


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def to_pcm16(source: Path) -> bytes:
    """Any audio file ffmpeg can read → 16 kHz mono 16-bit little-endian PCM."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed: brew install ffmpeg")
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(source), "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    )
    return out.stdout


def pcm_from_app(session_id: str | None) -> bytes:
    """Audio of a consultation recorded in the app (the most recent one if no session
    id is given), read read-only from the app's local data folder."""
    db = APP_DATA_DIR / "clinic.sqlite"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        if session_id:
            row = conn.execute("SELECT id FROM recordings WHERE session_id = ?", (session_id,)).fetchone()
        else:
            row = conn.execute("SELECT id FROM recordings ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit(f"No matching recording in {db}")
    return (APP_DATA_DIR / "audio" / f"{row[0]}.pcm").read_bytes()


def add_case(name: str, pcm: bytes, script: Path | None = None) -> Case:
    """With a role-play script (speaker<TAB>text), the script becomes the starting
    reference transcript and its <script>.gold.json the gold facts. The reference
    still has to be corrected by listening for anything said differently."""
    case = Case(name, FIXTURES_DIR / name)
    case.dir.mkdir(parents=True, exist_ok=True)
    write_wav(case.audio_path, pcm)
    if script is not None:
        lines = [ln for ln in script.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        case.reference_path.write_text(
            f"# From {script.name}. Listen to the recording and correct every line that was said differently.\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
        gold = script.with_name(script.stem + ".gold.json")
        if gold.exists():
            (case.dir / "gold.json").write_text(gold.read_text(encoding="utf-8"), encoding="utf-8")
    elif not case.reference_path.exists():
        case.reference_path.write_text(REFERENCE_TEMPLATE, encoding="utf-8")
    return case


def make_synthetic() -> list[Case]:
    """Render every eval/synthetic/<case>.tsv (voice<TAB>speaker<TAB>text) with the
    macOS `say` command into fixtures/synthetic-<case>/. A voice written as
    'Anna:low' is pitched down, to give a second distinguishable speaker."""
    cases: list[Case] = []
    for script in sorted(SYNTHETIC_DIR.glob("*.tsv")):
        rows = [
            line.split("\t")
            for line in script.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        pcm = b""
        gap = b"\x00\x00" * int(SAMPLE_RATE * 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            for i, (voice, _speaker, text) in enumerate(rows):
                name, _, variant = voice.partition(":")
                aiff = Path(tmp) / f"{i}.aiff"
                subprocess.run(["say", "-v", name, "-o", str(aiff), text], check=True)
                if variant == "low":
                    # Lower the pitch by 15% and speed back up so timing stays natural.
                    rate = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(aiff)],
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()
                    lowered = Path(tmp) / f"{i}-low.wav"
                    subprocess.run(
                        ["ffmpeg", "-loglevel", "error", "-i", str(aiff), "-af",
                         f"asetrate={rate}*0.85,aresample={rate},atempo={1 / 0.85:.4f}", str(lowered)],
                        check=True,
                    )
                    aiff = lowered
                pcm += to_pcm16(aiff) + gap
        case = add_case(f"synthetic-{script.stem}", pcm)
        case.reference_path.write_text(
            "# Generated from eval/synthetic/" + script.name + " by `clinic-eval synth`. Do not edit.\n"
            + "".join(f"{speaker}\t{text}\n" for _voice, speaker, text in rows),
            encoding="utf-8",
        )
        cases.append(case)
    return cases
