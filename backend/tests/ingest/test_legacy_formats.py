"""Office formats other than PDF / DOCX: OpenDocument needs odfdo, the binary .doc / .rtf / .xls / .ppt need LibreOffice
(Docling converts them with `soffice`; the Docker image installs it)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from spott.ingest.parsing.build import build_document, make_converter

ACT = [
    "DECIZIE nr. 12/14 din 28.07.2020",
    "Cu privire la aprobarea Planului de acțiuni pentru elaborarea Planului urbanistic general",
    "1. Se aprobă Planul de acțiuni pentru elaborarea Planului urbanistic general al municipiului Chișinău.",
]


def parse(path: Path) -> dict:
    result = make_converter(ocr=True).convert(path, raises_on_error=False)
    assert result.status.value == "success", [e.error_message for e in result.errors]
    file = {"sha256": "x", "path": path.name, "extension": path.suffix, "size": 0, "content_type": ""}
    return build_document(result.document, file=file, sources=[], text_layer={})


def assert_is_the_act(parsed: dict) -> None:
    meta = parsed["metadata"]
    assert (meta["doc_type"], meta["number"], meta["date"]) == ("decizie", "12/14", "2020-07-28")
    assert any("Se aprobă Planul de acțiuni" in (b.get("text") or "") for b in parsed["blocks"])


def test_opendocument_text(tmp_path):
    from odfdo import Document, Header, Paragraph

    doc = Document("text")
    doc.body.append(Header(1, ACT[0]))
    for line in ACT[1:]:
        doc.body.append(Paragraph(line))
    doc.save(tmp_path / "act.odt")
    assert_is_the_act(parse(tmp_path / "act.odt"))


@pytest.mark.parametrize("ext", ["doc", "rtf"])
def test_legacy_word_through_libreoffice(tmp_path, ext):
    soffice = shutil.which("soffice")
    if not soffice:
        pytest.skip("LibreOffice is not installed")
    import docx

    doc = docx.Document()
    doc.add_heading(ACT[0], 1)
    for line in ACT[1:]:
        doc.add_paragraph(line)
    doc.save(tmp_path / "act.docx")
    subprocess.run([soffice, "--headless", "--convert-to", ext, "--outdir", str(tmp_path), str(tmp_path / "act.docx")],
                   capture_output=True, timeout=180, check=False)
    if not (tmp_path / f"act.{ext}").exists():
        pytest.skip("LibreOffice has no Writer component here")
    assert_is_the_act(parse(tmp_path / f"act.{ext}"))
