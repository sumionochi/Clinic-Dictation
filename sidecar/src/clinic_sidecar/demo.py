"""Development-only: a synthetic consultation that opens straight on Review.

`demo_consultation.json` holds a scripted admission interview
(eval/extraction/tricky-family-contradiction) and the facts the agent pipeline
drafted for it on qwen3:8b. Seeding writes it through the same tables a real
consultation uses: a session with everyone's consent in the log, a transcribed
recording with matched speakers, and a ready record. Nothing is downloaded or
recorded, and no model runs, so it loads instantly.
"""

import json
import uuid
from pathlib import Path

from clinic_sidecar.agents.schema import Fact
from clinic_sidecar.consent import ConsentService
from clinic_sidecar.db import Database
from clinic_sidecar.record import RecordService, ReviewFact, _now
from clinic_sidecar.transcriber import SAMPLE_RATE

CASE = Path(__file__).with_name("demo_consultation.json")


def seed_demo_consultation(db: Database, consent: ConsentService, records: RecordService) -> str:
    """Creates the demo consultation and returns its session id."""
    case = json.loads(CASE.read_text(encoding="utf-8"))
    session_id = consent.create_session().id
    by_role: dict[str, str] = {}
    for person in case["participants"]:
        session = consent.add_participant(session_id, person["role"], person["label"])
        participant_id = session.participants[-1].id
        consent.record_decision(session_id, participant_id, "granted")
        by_role[person["role"]] = participant_id

    segments = case["segments"]
    recording_id = uuid.uuid4().hex
    rec = case["record"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO recordings (id, session_id, status, language, sample_rate, samples, started_at,"
            " stopped_at, model, diarization_model) VALUES (?, ?, 'transcribed', ?, ?, ?, ?, ?, ?, ?)",
            (
                recording_id,
                session_id,
                case["language"],
                SAMPLE_RATE,
                int(segments[-1]["end"] * SAMPLE_RATE),
                _now(),
                _now(),
                "mlx-community/whisper-large-v3-mlx",
                "mlx-community/diar_sortformer_4spk-v1-fp32",
            ),
        )
        conn.executemany(
            "INSERT INTO transcript_segments (recording_id, idx, start_s, end_s, text, words, speaker, language)"
            " VALUES (?, ?, ?, ?, ?, '[]', ?, ?)",
            [
                (recording_id, i, s["start"], s["end"], s["text"], s["speaker"], case["language"])
                for i, s in enumerate(segments)
            ],
        )
        conn.executemany(
            "INSERT INTO recording_speakers (recording_id, speaker, participant_id) VALUES (?, ?, ?)",
            [(recording_id, speaker, by_role[role]) for speaker, role in case["speakers"].items()],
        )

    lines = records.transcript_lines(session_id)
    facts = [
        ReviewFact(
            id=uuid.uuid4().hex[:12],
            fact=Fact.model_validate(f["fact"]),
            state=f["state"],
            origin=f["origin"],
            reason=f["reason"],
        )
        for f in rec["facts"]
    ]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO records (session_id, recording_id, status, model, lines, facts, rejected,"
            " contradictions, log, extract_seconds, created_at) VALUES (?, ?, 'ready', ?, ?, ?, '[]', ?, ?, ?, ?)",
            (
                session_id,
                recording_id,
                rec["model"],
                json.dumps([ln.model_dump() for ln in lines], ensure_ascii=False),
                records._dump(facts),
                json.dumps(rec["contradictions"], ensure_ascii=False),
                json.dumps(rec["log"], ensure_ascii=False),
                rec["extract_seconds"],
                _now(),
            ),
        )
    return session_id
