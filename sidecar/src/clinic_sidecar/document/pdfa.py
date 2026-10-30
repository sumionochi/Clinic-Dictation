"""PDF → PDF/A-2b with Ghostscript, checked by veraPDF.

The KBV eArztbrief guideline (§ 383 SGB V) requires the letter's content as PDF/A,
the archival PDF profile: fonts embedded, colours tied to an ICC profile, metadata
in XMP, nothing that needs outside resources. Chromium's print output is converted
here and then validated with veraPDF, the reference validator. Nothing is exported
that veraPDF does not accept, as with the HL7 validator for FHIR.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

GS_CANDIDATES = ["/opt/homebrew/bin/gs", "/usr/local/bin/gs"]
ICC_CANDIDATES = ["/opt/homebrew/share/ghostscript/iccprofiles/srgb.icc", "/usr/local/share/ghostscript/iccprofiles/srgb.icc"]
VERAPDF = Path.home() / "Library" / "Application Support" / "clinic-dictation" / "tools" / "verapdf" / "verapdf"
JAVA_HOME_CANDIDATES = ["/opt/homebrew/opt/openjdk@21", "/opt/homebrew/opt/openjdk@17"]


class PdfaUnavailable(RuntimeError):
    pass


@dataclass
class PdfaReport:
    compliant: bool
    profile: str
    failures: list[str] = field(default_factory=list)


@dataclass
class PdfMetadata:
    title: str
    author: str
    subject: str
    keywords: str


def _first(paths: list[str]) -> str | None:
    return next((p for p in paths if Path(p).exists()), None) or None


def _ps_string(text: str) -> str:
    """A PostScript string for pdfmark. Non-ASCII goes in as UTF-16BE with BOM,
    which is how PDF text strings carry it."""
    if text.isascii():
        return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def to_pdfa(pdf: bytes, meta: PdfMetadata) -> bytes:
    gs = os.environ.get("CLINIC_GS") or shutil.which("gs") or _first(GS_CANDIDATES)
    icc = _first(ICC_CANDIDATES)
    if not gs or not icc:
        raise PdfaUnavailable("Ghostscript with its sRGB profile is needed for PDF/A (brew install ghostscript).")
    with tempfile.TemporaryDirectory() as tmp:
        src, out, defs = Path(tmp) / "in.pdf", Path(tmp) / "out.pdf", Path(tmp) / "pdfa_def.ps"
        src.write_bytes(pdf)
        # Adapted from Ghostscript's lib/PDFA_def.ps: the sRGB output intent PDF/A
        # needs, plus the document information that ends up in XMP metadata.
        defs.write_text(
            f"""%!
[ /Title {_ps_string(meta.title)} /Author {_ps_string(meta.author)}
  /Subject {_ps_string(meta.subject)} /Keywords {_ps_string(meta.keywords)} /DOCINFO pdfmark
/ICCProfile ({icc}) def
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} << /N 3 >> /PUT pdfmark
[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB) /Info (sRGB IEC61966-2.1) >> /PUT pdfmark
[{{Catalog}} << /OutputIntents [ {{OutputIntent_PDFA}} ] >> /PUT pdfmark
""",
            encoding="latin-1",
        )
        proc = subprocess.run(
            [
                gs, "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-dQUIET",
                "-sColorConversionStrategy=RGB", "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
                f"--permit-file-read={icc}", f"--permit-file-read={tmp}/",
                f"-sOutputFile={out}", str(defs), str(src),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not out.exists():
            raise PdfaUnavailable(f"Ghostscript failed: {proc.stderr[-400:] or proc.stdout[-400:]}")
        return out.read_bytes()


def validator_available() -> bool:
    return VERAPDF.exists() and _first(JAVA_HOME_CANDIDATES) is not None


def validate(pdf: bytes, flavour: str = "2b") -> PdfaReport:
    if not validator_available():
        raise PdfaUnavailable(f"veraPDF not found at {VERAPDF}")
    env = {**os.environ, "JAVA_HOME": _first(JAVA_HOME_CANDIDATES) or ""}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.pdf"
        path.write_bytes(pdf)
        proc = subprocess.run(
            [str(VERAPDF), "--flavour", flavour, "--format", "text", str(path)],
            capture_output=True, text=True, timeout=180, env=env,
        )
    text = proc.stdout
    compliant = text.strip().startswith("PASS")
    failures = [] if compliant else [ln.strip() for ln in text.splitlines()[1:] if ln.strip()][:10]
    if not compliant and not text.strip().startswith("FAIL"):
        raise PdfaUnavailable(f"veraPDF did not run: {proc.stderr[-400:] or text[-400:]}")
    return PdfaReport(compliant=compliant, profile=f"PDF/A-{flavour.upper()}", failures=failures)
