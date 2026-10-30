# Evaluation

Measures how accurately the app's speech recognition captures German
consultations. Milestone 1 reports these numbers on our own role-play recordings.

| Metric | What it tells us |
|---|---|
| **WER / CER** | Word and character error rate over the whole transcript, after normalisation (case, punctuation, `5` = `fünf`, `mg` = `Milligramm`). |
| **Term recall** | Of the medical terms in `terms/medical_de.txt` that were said, how many were recognised exactly, *at the place they were said*. |
| **Negation recall** | The same for `kein`, `nicht`, `gegen` and similar. Losing one inverts a statement, so it is a safety metric of its own. |
| **Speaker accuracy** | Of the words recognised in the right place, how many are attributed to the right person. Each detected voice is mapped to the person it overlaps most (several voices may map to one person, as in the app). |
| **Speed** | Audio length ÷ transcription time on this machine (model already loaded). |

Every run is saved to `results/<date>-<model>.json` with both transcripts, so
numbers can be compared between models and over time.

## Run it

```bash
cd eval
uv sync
uv run clinic-eval synth      # render the synthetic smoke-test cases (macOS `say`)
uv run clinic-eval run        # transcribe every case and score it
```

`run` uses the model the app already downloaded and stays offline. Pass
`--model <hugging face repo>` to compare another MLX Whisper model (add
`--allow-download` if it is not on disk yet), and case names to run only some.

Tests: `uv run pytest`.

## Adding a role-play consultation

The synthetic cases only check that the pipeline works: a clear computer voice
reading a script is far easier than two people talking. The numbers that matter
come from role-plays.

They are especially useless for speaker accuracy. The patient is the doctor's
voice pitched down, and Sortformer hears them as largely one person (about 65%
speaker accuracy). With a genuinely different system voice it reaches about 96%,
but those voices are too unclear for Whisper. Only two real people give a fair
measure.

1. **Write a script** in German (see the checklist below) and save it, e.g.
   `scripts/roleplay-03.md`.
2. **Record it in the app**: Consent → New consultation → everyone agrees →
   Session → Start recording. Speak naturally; small deviations from the script
   are fine.
3. **Import it**: `uv run clinic-eval add roleplay-03 --latest`
   (or `--audio some-file.m4a` for a recording made elsewhere).
4. **Write `fixtures/roleplay-03/reference.tsv`**: one turn per line,
   `speaker<TAB>text`, from the script, corrected by listening for anything said
   differently. Never paste the app's transcript in: a reference made from the
   model's output hides exactly the errors we want to count.
5. **Run** `uv run clinic-eval run`.

The audio stays local (`*.wav` is gitignored); only `reference.tsv` is committed.
Only use voices of people who agreed to it, and no real patient data.

### Script checklist

Each script should be an 8–15 minute internal medicine admission interview and
include most of these, because they are where transcription and extraction fail:

- [ ] Chief complaint with duration and severity ("seit drei Tagen", "Schmerz 7 von 10")
- [ ] Two or more medications with doses and timing (Ramipril 5 mg morgens)
- [ ] An allergy *and* an explicit "keine weiteren Allergien"
- [ ] Negated symptoms ("kein Fieber", "nicht geraucht")
- [ ] A number said as digits-worthy value (blood pressure, weight, Packungsjahre)
- [ ] Previous illnesses and operations
- [ ] Social situation (lives alone, Pflegedienst, Pflegegrad)
- [ ] One contradiction (stopped a drug, later says still taking it)
- [ ] A companion or interpreter speaking at least once
- [ ] Some interruptions, overlapping speech or background noise

## Extraction: agents vs. one prompt

`uv run clinic-eval extract` compares two ways of turning a transcript into
clinical facts, with the same local model:

- **baseline**: one prompt, facts out, no evidence, no checking;
- **agents**: extract with quoted evidence, check every quote is really on its
  line, verify each fact in a separate call, send failures back once with the
  reason (`sidecar/src/clinic_sidecar/agents/graph.py`).

Cases live in `extraction/<case>/`: `transcript.tsv` (`speaker<TAB>text`) and
`gold.json`, the facts a careful clinician would record. A fact counts when the
category, name (or a listed synonym) and assertion (`present` / `absent` /
`past`) agree.

| Column | Meaning |
|---|---|
| **prec / recall** | Facts that are right / gold facts that were found. |
| **flips** | Right fact, wrong assertion: a denied symptom recorded as present. The dangerous kind of error. |
| **contra** | Expected contradictions that were flagged for the doctor. |
| **calls** | Model calls used (the agents trade speed for checking). |

Needs Ollama running locally (`ollama serve`) with the model pulled. Compare
models with `--llm medgemma:4b --llm qwen3:8b`.

Gold facts are written by hand from the transcript. Like `reference.tsv`, never
fill them from a model's output.

### Findings so far (30 Oct 2026, `results/20261030-0023-extraction.json`)

Three short cases, 28 gold facts, perfect transcripts. Too small to be
statistically meaningful; it shows direction, not proof.

| qwen3:8b | Precision | Recall | Assertion flips | Contradictions flagged | Time |
|---|---|---|---|---|---|
| one prompt | 96.0% | 85.7% | 1 | 0/1 | 76 s |
| agents | 100% | 85.7% | 0 | 1/1 | 305 s |

- The agents never recorded a wrong present/absent/past, and caught the
  Metoprolol contradiction; the one-prompt baseline recorded the patient's
  mother's heart attack as the patient's.
- They cost about 4x the time (one verification call per fact).
- medgemma:4b is too weak as a verifier: it rejects correct facts and recall
  falls to 43%. qwen3:8b is the default.
- What made the difference while building it: correcting miscounted line numbers
  when the quote is verbatim nearby, capping retries at one fact per rejected
  fact, detecting contradictions before verification, letting the verifier write
  its reason before its verdict, and verifying a plain sentence ("The patient does
  NOT take: Apixaban") instead of a JSON field.

## FHIR export

`uv run clinic-eval fhir results/<date>-extraction.json` turns the agents' facts
from an extraction run into FHIR R4 document bundles
(`sidecar/src/clinic_sidecar/fhir/mapper.py`) and runs the official HL7 validator
on each. Bundles are written to `results/fhir/`. The command fails if any bundle
has a validation error.

Each fact becomes one resource (Condition, MedicationStatement,
AllergyIntolerance, Procedure or social-history Observation) with present / absent
/ past expressed in its status fields, plus a Provenance that quotes the transcript
line behind it. Everything is `unconfirmed` / `preliminary` until the doctor
approves it. Names are free text: no ICD-10-GM, ATC or SNOMED codes are proposed
(except SNOMED "No known allergy"), since suggesting codes moves a scribe towards
medical-device territory.

Validated against FHIR R4 core. Not yet claimed: German base profiles or ISiK,
and the patient is not identified (a real deployment links the record to the
practice or hospital system).
