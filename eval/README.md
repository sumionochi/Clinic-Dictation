# Evaluation

Measures how accurately the app's speech recognition captures German
consultations. Milestone 1 reports these numbers on our own role-play recordings.

| Metric | What it tells us |
|---|---|
| **WER / CER** | Word and character error rate over the whole transcript, after normalisation (case, punctuation, `5` = `fünf`, `mg` = `Milligramm`). |
| **Term recall** | Of the medical terms in `terms/medical_de.txt` that were said, how many were recognised exactly, *at the place they were said*. |
| **Negation recall** | The same for `kein`, `nicht`, `gegen` and similar. Losing one inverts a statement, so it is a safety metric of its own. |
| **Speaker accuracy** | Of the words recognised in the right place, how many are attributed to the right person. Each detected voice is mapped to the person it overlaps most (several voices may map to one person, as in the app). |
| **Live WER** | WER of the live pass: the same audio transcribed in ~20 s windows, as the app does while recording to feed the checklist. Compared with the final pass, it shows what transcribing without the rest of the conversation costs. |
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

A ready-made script is in `scripts/roleplay-01-atemnot.tsv` (doctor, patient and
his wife; about 8 to 10 minutes; gold facts in `roleplay-01-atemnot.gold.json`).

1. **Write a script** in German as `scripts/<name>.tsv` (`speaker<TAB>text`, see
   the checklist below) and its gold facts as `scripts/<name>.gold.json`. Scripts
   with gold facts also count as text-only extraction cases straight away.
2. **Record it in the app**: Consent → New consultation → everyone agrees →
   Session → Start recording. Speak naturally; small deviations from the script
   are fine.
3. **Import it**: `uv run clinic-eval add roleplay-01 --latest --script scripts/roleplay-01-atemnot.tsv`
   (or `--audio some-file.m4a` for a recording made elsewhere). This copies the
   script into `reference.tsv` and the gold facts next to the audio.
4. **Correct `fixtures/<name>/reference.tsv`** by listening: every line said
   differently from the script must be fixed. Never paste the app's transcript
   in: a reference made from the model's output hides exactly the errors we want
   to count.
5. **Run** `uv run clinic-eval run` (speech only) or `uv run clinic-eval e2e`
   (everything).

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

### Findings so far (30 Oct 2026, `results/20261030-1050-extraction.json`)

Four cases with perfect transcripts, 52 gold facts: three short constructed ones
and the 41-line role-play script. qwen3:8b for both systems.

| | Precision | Recall | Assertion flips | Contradictions flagged | Time |
|---|---|---|---|---|---|
| one prompt | 86.3% | 84.6% | 0 | 0/2 | 127 s |
| agents | 91.1% | 78.8% | 0 | 1/2 | 537 s |

- On a realistic consultation the agents trade recall for precision: they keep
  fewer facts, and the ones they keep are more often right, at about 4x the time.
  On the short cases alone they looked strictly better; the long role-play is what
  showed the trade-off.
- Both systems got assertion flips to zero only after two general prompt rules:
  "nicht mehr" means past, not denied; every denied symptom is its own fact.
- The agents flag contradictions; the one-prompt baseline never does. Both still
  record a relative's illness ("Herzkrankheiten in der Familie") as the patient's.
- medgemma:4b is too weak as a verifier (recall 43% in an earlier run); qwen3:8b
  is the default.
- Runs are not bit-for-bit reproducible even at temperature 0 (Ollama on the GPU),
  so differences of a few points on a handful of cases are noise. Repeat runs and
  more role-plays are needed before drawing firm conclusions.
- Name matching is fuzzy but literal: a fact named "Schwellung der Beine seit
  fünf Tagen" (details folded into the name) counts as wrong. A clinician rating a
  sample would settle such cases.

What made the difference while building it: correcting miscounted line numbers
when the quote is verbatim nearby, capping retries at one fact per rejected
fact, detecting contradictions before verification, letting the verifier write
its reason before its verdict, verifying a plain sentence ("The patient does NOT
take: Apixaban") instead of a JSON field, and telling both agents that automatic
speaker labels can be wrong.

## End to end

`uv run clinic-eval e2e` runs every case that has audio, a reference and gold
facts through the whole pipeline: Whisper → Sortformer → agents, plus the
one-prompt baseline on the same transcript and the agents on the perfect
reference transcript. The gap between the last two shows how many facts are lost
to speech and speaker errors rather than to extraction. It reports WER, term and
negation recall, speaker accuracy, fact precision and recall (omission rate =
1 − recall, unsupported-claim rate = 1 − precision), assertion flips,
contradictions, the verifier's own precision and recall, and the time from the
end of the recording to a drafted record. Results go to
`results/<date>-e2e.json` and a Markdown report `results/<date>-e2e.md`.

On the synthetic cases this mostly measures how badly near-identical computer
voices confuse speaker separation (every line ended up labelled as the patient in
one run). The end-to-end numbers that matter need role-plays with real voices.

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
