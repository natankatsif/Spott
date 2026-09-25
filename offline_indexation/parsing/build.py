"""Docling conversion and the corpus JSON built from its output.

Output schema (data/parsed/<sha>.json):
    sha256, parser_version, file{...}, sources[...]   — what the document is and where it was found
    metadata{title, lang, doc_type, number, date}
    pages[{n, text_layer}]                            — text_layer=false → the page went through OCR
    blocks[{id, type, text, page, section[], lang, (level) | (header, rows)}]
        type: heading | paragraph | list_item | table
        section: headings above the block, outermost first — used in citations
"""

import sys
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import OcrMacOptions, PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel, DoclingDocument, SectionHeaderItem, TableItem, TextItem

from . import metadata
from .normalize import detect_lang, normalize_text

PARSER_VERSION = "1"
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".rtf", ".odt",
    ".xlsx", ".xls", ".ods", ".csv",
    ".pptx", ".ppt", ".odp",
}
DOCUMENT_TIMEOUT = 900.0  # seconds per document
TEXT_LAYER_MIN_CHARS = 20

SKIPPED_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}
BLOCK_TYPES = {
    DocItemLabel.TITLE: "heading",
    DocItemLabel.SECTION_HEADER: "heading",
    DocItemLabel.LIST_ITEM: "list_item",
}


def make_converter() -> DocumentConverter:
    if sys.platform == "darwin":
        ocr = OcrMacOptions(lang=["ro-RO", "ru-RU"])  # Apple Vision
    else:
        ocr = TesseractCliOcrOptions(lang=["ron", "rus"])
    pdf = PdfPipelineOptions(do_ocr=True, do_table_structure=True, ocr_options=ocr,
                             document_timeout=DOCUMENT_TIMEOUT)
    pdf.heading_hierarchy_options.enabled = True
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf)})


def text_layer_pages(path: Path) -> dict[int, bool]:
    """Which PDF pages have extractable text; the rest are scans."""
    if path.suffix.lower() != ".pdf":
        return {}
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        return {i + 1: pdf[i].get_textpage().count_chars() >= TEXT_LAYER_MIN_CHARS for i in range(len(pdf))}
    finally:
        pdf.close()


def page_of(item) -> int | None:
    return item.prov[0].page_no if getattr(item, "prov", None) else None


def table_block(item: TableItem, doc: DoclingDocument) -> dict:
    grid = [[normalize_text(cell.text) for cell in row] for row in item.data.grid]
    header_rows = 0
    for row in item.data.grid:
        if row and all(cell.column_header for cell in row):
            header_rows += 1
        else:
            break
    header = grid[header_rows - 1] if header_rows else []
    return {
        "type": "table",
        "header": header,
        "rows": grid[header_rows:],
        "text": normalize_text(item.export_to_markdown(doc)),
    }


def build_blocks(doc: DoclingDocument) -> list[dict]:
    blocks: list[dict] = []
    section: list[tuple[int, str]] = []  # (level, heading text)

    for item, _ in doc.iterate_items():
        if isinstance(item, TableItem):
            block = table_block(item, doc)
        elif isinstance(item, TextItem) and item.label not in SKIPPED_LABELS and (text := normalize_text(item.text)):
            block = {"type": BLOCK_TYPES.get(item.label, "paragraph"), "text": text}
            # Docling strips "1." / "5.2" from list items; citations need them ("pct. 5").
            if marker := getattr(item, "marker", "").strip():
                block |= {"marker": marker, "text": f"{marker} {text}"}
        else:
            continue

        is_heading = block["type"] == "heading"
        if is_heading:
            level = item.level if isinstance(item, SectionHeaderItem) else 0
            block["level"] = level
            while section and section[-1][0] >= level:
                section.pop()
        block |= {
            "id": len(blocks),
            "page": page_of(item),
            "section": [t for _, t in section],  # a heading's section is the path above it
            "lang": detect_lang(block["text"]),
        }
        blocks.append(block)
        if is_heading:
            section.append((level, block["text"]))
    return blocks


def build_markdown(doc: DoclingDocument, parsed: dict) -> str:
    meta = parsed["metadata"]
    header = [f"sha256: {parsed['sha256']}"]
    header += [f"source: {s['url']}" + (f"  (found on {s['found_on']})" if s.get("found_on") else "")
               for s in parsed["sources"]]
    header += [f"{k}: {meta[k]}" for k in ("doc_type", "number", "date", "lang") if meta.get(k)]
    return "<!--\n" + "\n".join(header) + "\n-->\n\n" + normalize_text(doc.export_to_markdown()) + "\n"


def build_document(doc: DoclingDocument, *, file: dict, sources: list[dict], text_layer: dict[int, bool]) -> dict:
    blocks = build_blocks(doc)
    body = "\n".join(b["text"] for b in blocks)
    n_pages = len(doc.pages) or max(text_layer, default=0)

    return {
        "sha256": file["sha256"],
        "parser_version": PARSER_VERSION,
        "file": file,
        "sources": sources,
        "metadata": metadata.extract(blocks, sources) | {"lang": detect_lang(body)},
        "pages": [{"n": n, "text_layer": text_layer.get(n)} for n in range(1, n_pages + 1)],
        "stats": {
            "pages": n_pages,
            "ocr_pages": sum(1 for has_text in text_layer.values() if not has_text),
            "blocks": len(blocks),
            "chars": len(body),
        },
        "blocks": blocks,
    }
