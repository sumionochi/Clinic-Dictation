# Adapted from TranscriptionSuite (https://github.com/homelab-00/TranscriptionSuite) —
# server/backend/core/sortformer_engine.py: Sortformer via mlx-audio, streaming in 5 s
# chunks so memory stays bounded on long recordings.
"""Who spoke when, with NVIDIA Sortformer (up to 4 speakers) on Apple Silicon.

Like the transcriber, this must run on the transcription worker thread: MLX
binds GPU streams to the thread that created them.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

DIARIZATION_MODEL = "mlx-community/diar_sortformer_4spk-v1-fp32"
CHUNK_SECONDS = 5.0
THRESHOLD = 0.5


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str  # "S1".."S4", in order of first appearance


class Diarizer(Protocol):
    model_name: str

    def diarize(self, audio: np.ndarray) -> list[SpeakerTurn]: ...


class SortformerDiarizer:
    def __init__(self, model_name: str = DIARIZATION_MODEL) -> None:
        self.model_name = model_name
        self._model = None

    def diarize(self, audio: np.ndarray) -> list[SpeakerTurn]:
        if self._model is None:
            from mlx_audio.vad import load  # imported here so it loads on the worker thread

            self._model = load(self.model_name)
        raw = []
        for result in self._model.generate_stream(
            audio, sample_rate=16_000, chunk_duration=CHUNK_SECONDS, threshold=THRESHOLD
        ):
            raw.extend(result.segments)
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:  # noqa: BLE001 - only frees memory sooner
            pass
        return renumber([SpeakerTurn(float(s.start), float(s.end), str(s.speaker)) for s in raw])


def renumber(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """Name speakers S1, S2, … by when they first speak, so 'S1' is whoever opened
    the conversation, however the model happened to index them."""
    turns = sorted(turns, key=lambda t: t.start)
    names: dict[str, str] = {}
    for t in turns:
        names.setdefault(t.speaker, f"S{len(names) + 1}")
    return [SpeakerTurn(t.start, t.end, names[t.speaker]) for t in turns]
