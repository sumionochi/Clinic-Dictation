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


class Transcriber(Protocol):
    model_name: str

    def transcribe(self, audio: np.ndarray, language: str | None) -> list[Segment]: ...


class WhisperTranscriber:
    """mlx-whisper. The model is downloaded on first use and cached in HF_HOME."""

    def __init__(self, model_name: str = FINAL_MODEL) -> None:
        self.model_name = model_name

    def transcribe(self, audio: np.ndarray, language: str | None) -> list[Segment]:
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
        segments: list[Segment] = []
        for seg in result.get("segments", []):
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            segments.append(
                Segment(
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    text=text,
                    words=[
                        Word(
                            word=str(w["word"]),
                            start=float(w["start"]),
                            end=float(w["end"]),
                            probability=float(w.get("probability", 0.0)),
                        )
                        for w in seg.get("words", [])
                    ],
                    avg_logprob=seg.get("avg_logprob"),
                    no_speech_prob=seg.get("no_speech_prob"),
                )
            )
        return segments


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
