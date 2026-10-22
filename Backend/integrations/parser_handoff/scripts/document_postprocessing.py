"""Preserve recognized picture text and reading order in builder assets."""
from docling_core.types.doc import TableItem
from table_boundaries import logical_tables
from text_table_recovery import repaired_items


def build_document_payload(doc):
    pages = {page: [] for page in doc.pages}
    sections = []
    tables = []
    # OCR text can be attached beneath PictureItem. Default traversal skips it.
    for item in repaired_items(doc):
        numbers = sorted({p.page_no for p in getattr(item, "prov", []) or []})
        if isinstance(item, TableItem):
            chunks = []
            for part, notes in logical_tables(item):
                text = part.export_to_markdown(doc=doc)
                tables.append({"text": text, "html": part.export_to_html(doc=doc), "pages": numbers})
                chunks.append(text)
                chunks.extend(notes)
                sections.extend({"text": note, "pages": numbers} for note in notes)
            text = "\n\n".join(chunks)
        else:
            text = getattr(item, "text", "") or ""
            if not text.strip():
                continue
            marker = getattr(item, "marker", "") or ""
            if marker and not text.lstrip().startswith(marker):
                text = f"{marker} {text}"
            sections.append({"text": text, "pages": numbers})
        if text.strip():
            for page in numbers:
                pages.setdefault(page, []).append(text)
    ordered = ["\n".join(pages.get(page, [])) for page in range(1, max(pages, default=0) + 1)]
    return {"text": "\n\n".join(ordered), "pages": ordered,
            "sections": sections, "tables": tables, "page_count": len(ordered)}
