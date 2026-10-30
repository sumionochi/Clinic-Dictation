import shutil
import subprocess

import pytest

from clinic_sidecar.document.pdfa import PdfMetadata, to_pdfa, validate, validator_available

needs_tools = pytest.mark.skipif(
    not (shutil.which("gs") and validator_available()), reason="Ghostscript and veraPDF not installed"
)


def plain_pdf(tmp_path) -> bytes:
    ps = tmp_path / "t.ps"
    ps.write_text("%!\n/Helvetica findfont 14 scalefont setfont 72 720 moveto (Aufnahme) show showpage\n")
    out = tmp_path / "t.pdf"
    subprocess.run(["gs", "-q", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite", f"-sOutputFile={out}", str(ps)], check=True)
    return out.read_bytes()


@needs_tools
def test_plain_pdf_is_not_pdfa_but_converted_one_is(tmp_path) -> None:
    raw = plain_pdf(tmp_path)
    assert not validate(raw).compliant
    meta = PdfMetadata("Aufnahmegespräch", "Clinic Dictation (KI-gestützt, ärztlich freigegeben)", "Anamnese", "AI-assisted")
    converted = to_pdfa(raw, meta)
    report = validate(converted)
    assert report.compliant, report.failures
    assert b"pdfaid" in converted  # PDF/A identification in the XMP metadata
