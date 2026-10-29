# Adapted from TranscriptionSuite (https://github.com/homelab-00/TranscriptionSuite) —
# server/backend/core/stt/backends/mlx_thread_pin.py: the finding that MLX work must be
# pinned to one owning thread, solved here with a single-worker executor.
"""Speech recognition for the final (accurate) pass, with mlx-whisper on Apple Silicon.

MLX binds each GPU stream to the thread that created it, so every MLX call must
run on one thread. `TranscriptionWorker` owns a single worker thread and runs
all jobs there, one at a time.
"""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000
FINAL_MODEL = "mlx-community/whisper-large-v3-mlx"
# Per-line language check in "detect automatically" mode: a line is re-transcribed
# in another language only when Whisper is this sure, and the line is long enough
# for detection to mean something.
LANGUAGE_SWITCH_MIN_PROB = 0.6
LANGUAGE_CHECK_MIN_SECONDS = 1.0


@dataclass
class Word:
    word: str
    start: float
    end: float
    probability: float


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    language: str | None = None  # ISO code, e.g. "de", "en"


class Transcriber(Protocol):
    model_name: str

    def transcribe(self, audio: np.ndarray, language: str | None) -> list[Segment]: ...


class WhisperTranscriber:
    """mlx-whisper. The model is downloaded on first use and cached in HF_HOME."""

    def __init__(self, model_name: str = FINAL_MODEL) -> None:
        self.model_name = model_name

    def transcribe(self, audio: np.ndarray, language: str | None) -> list[Segment]:
        segments, detected = self._run(audio, language)
        if language is None:
            # Whisper picks one language from the first 30 s and forces it on the whole
            # recording, so an English answer in a German consultation comes out
            # translated into German. Check each line on its own instead.
            segments = self._fix_language_per_line(audio, segments, detected)
        return segments

    def _run(self, audio: np.ndarray, language: str | None, offset: float = 0.0) -> tuple[list[Segment], str]:
        import mlx_whisper  # imported here so it loads on the worker thread

        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model_name,
            language=language,
            word_timestamps=True,
            # Feeding earlier text back in makes Whisper prone to repeating itself
            # on silence; each window stands on its own instead.
            condition_on_previous_text=False,
            verbose=None,
        )
        used = str(result.get("language") or language or "")
        segments: list[Segment] = []
        for seg in result.get("segments", []):
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            segments.append(
                Segment(
                    start=float(seg["start"]) + offset,
                    end=float(seg["end"]) + offset,
                    text=text,
                    words=[
                        Word(
                            word=str(w["word"]),
                            start=float(w["start"]) + offset,
                            end=float(w["end"]) + offset,
                            probability=float(w.get("probability", 0.0)),
                        )
                        for w in seg.get("words", [])
                    ],
                    avg_logprob=seg.get("avg_logprob"),
                    no_speech_prob=seg.get("no_speech_prob"),
                    language=used or None,
                )
            )
        return segments, used

    def _detect_language(self, audio: np.ndarray) -> tuple[str, float]:
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.transcribe import ModelHolder

        # The same cached model transcribe() uses, in its default fp16.
        model = ModelHolder.get_model(self.model_name, mx.float16)
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = model.detect_language(mel)
        best = max(probs, key=probs.get)
        return best, float(probs[best])

    def _fix_language_per_line(self, audio: np.ndarray, segments: list[Segment], main: str) -> list[Segment]:
        out: list[Segment] = []
        for seg in segments:
            if seg.end - seg.start < LANGUAGE_CHECK_MIN_SECONDS:
                out.append(seg)
                continue
            lo = max(0, int((seg.start - 0.1) * SAMPLE_RATE))
            hi = min(audio.size, int((seg.end + 0.1) * SAMPLE_RATE))
            clip = audio[lo:hi]
            lang, prob = self._detect_language(clip)
            if lang == main or prob < LANGUAGE_SWITCH_MIN_PROB:
                out.append(seg)
                continue
            redone, _ = self._run(clip, lang, offset=lo / SAMPLE_RATE)
            out.extend(redone or [seg])
        return out


class TranscriptionWorker:
    """Runs jobs one at a time on a single dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-whisper")

    def submit(self, job: Callable[[], None]) -> Future[None]:
        return self._executor.submit(job)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def pcm16_to_float32(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
