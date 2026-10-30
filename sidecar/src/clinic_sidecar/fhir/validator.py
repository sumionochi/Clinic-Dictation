"""The official HL7 FHIR validator as an export gate.

No bundle leaves the app unless the validator reports zero errors. Runs the
validator jar with its own Java (JDK 17+), offline after the FHIR R4 core package
is in ~/.fhir/packages.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_JAR = Path.home() / "Library" / "Application Support" / "clinic-dictation" / "tools" / "validator_cli.jar"
JAVA_CANDIDATES = ["/opt/homebrew/opt/openjdk@21/bin/java", "/opt/homebrew/opt/openjdk@17/bin/java"]


@dataclass
class Issue:
    severity: str  # fatal | error | warning | information
    location: str
    message: str


@dataclass
class ValidationReport:
    valid: bool
    errors: list[Issue]
    warnings: list[Issue]


class ValidatorUnavailable(RuntimeError):
    pass


def find_validator() -> tuple[str, Path]:
    jar = Path(os.environ.get("FHIR_VALIDATOR_JAR", DEFAULT_JAR))
    java = os.environ.get("FHIR_VALIDATOR_JAVA") or next((j for j in JAVA_CANDIDATES if Path(j).exists()), None)
    if java is None or not jar.exists():
        raise ValidatorUnavailable(
            f"HL7 validator not set up (java: {java or 'JDK 17+ not found'}, jar: {jar}). "
            "Install with `brew install openjdk@21` and download validator_cli.jar."
        )
    return java, jar


def validate(bundle: dict) -> ValidationReport:
    java, jar = find_validator()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "bundle.json"
        out = Path(tmp) / "outcome.json"
        src.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            [java, "-jar", str(jar), str(src), "-version", "4.0.1", "-output", str(out)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if not out.exists():
            raise ValidatorUnavailable(f"Validator produced no result: {proc.stdout[-500:]} {proc.stderr[-500:]}")
        outcome = json.loads(out.read_text(encoding="utf-8"))
    issues = [
        Issue(
            severity=i.get("severity", ""),
            location=", ".join(i.get("expression", []) or i.get("location", [])),
            message=i.get("details", {}).get("text") or i.get("diagnostics", ""),
        )
        for i in outcome.get("issue", [])
    ]
    errors = [i for i in issues if i.severity in ("error", "fatal")]
    warnings = [i for i in issues if i.severity == "warning"]
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


def validator_available() -> bool:
    try:
        find_validator()
        return True
    except ValidatorUnavailable:
        return False
