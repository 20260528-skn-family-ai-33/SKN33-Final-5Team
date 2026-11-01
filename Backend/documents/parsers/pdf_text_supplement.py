"""Conservative, page-local recovery of embedded PDF text. Never performs OCR."""
import copy
import html
import re
import time
import unicodedata
from difflib import SequenceMatcher


def plain_text(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    return html.unescape(text).replace("**", "").replace("`", "").replace("|", " ")


def normalized(text):
    # Preserve numbers, decimal points, signs and punctuation: 1.5 != 15.
    text = unicodedata.normalize("NFC", plain_text(text))
    text = text.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))
    return re.sub(r"\s+", "", text)


def segments(text):
    return [s.strip() for s in re.split(r"\n+|(?<=[.!?。])\s+", plain_text(text)) if s.strip()]


def select_supplements(existing, markdown, native_text):
    """Reject duplicates and ambiguous overlaps; never rewrite existing text."""
    accepted, review = [], []
    coverage = normalized(existing)
    native = normalized(native_text)
    known = [normalized(s) for s in segments(existing)]
    for candidate in segments(markdown):
        key = normalized(candidate)
        if not key or key in coverage:
            continue
        # Must be a substantial verbatim fragment of this page's embedded text.
        if len(key) < 8 or key not in native:
            continue
        ambiguous = False
        for previous in known:
            if len(previous) < 8:
                continue
            match = SequenceMatcher(None, key, previous, autojunk=False)
            overlap = match.find_longest_match().size
            if match.ratio() >= .85 or (overlap >= 12 and overlap / min(len(key), len(previous)) >= .5):
                ambiguous = True
                break
        if ambiguous:
            review.append({"text": candidate, "reason": "partial_overlap_or_conflicting_text"})
            continue
        accepted.append(candidate)
        known.append(key)
        coverage += "\n" + key
    return accepted, review


def supplement_pdf_payload(source, payload, *, page_numbers=None, ignored_by_page=None, max_fallback_pages=20, time_budget=45):
    """Add missing prose with source metadata; do not infer table cells or relations.

    Cheap embedded-text coverage check precedes expensive PyMuPDF4LLM conversion.
    The time budget is checked between pages, not a hard per-page interrupt.
    """
    result = copy.deepcopy(payload)
    result.setdefault("supplements", [])
    report = {"engine": "pymupdf4llm", "ocr": False, "checked_pages": [],
              "converted_pages": [], "review": [], "deferred_pages": [], "errors": []}
    result["supplement_report"] = report
    started = time.monotonic()
    try:
        import pymupdf
    except ImportError:
        report["errors"].append({"reason": "pymupdf_not_installed"})
        return result
    with pymupdf.open(source) as pdf:
        numbers = sorted(set(page_numbers if page_numbers is not None else range(1, len(pdf)+1)))
        for number in numbers:
            if number < 1 or number > len(pdf) or number > len(result["pages"]):
                raise ValueError("Supplement page is outside the converted page range")
            existing = result["pages"][number-1]
            comparison_text = existing + "\n" + "\n".join((ignored_by_page or {}).get(number, []))
            native = pdf[number-1].get_text("text", sort=False)
            coverage = normalized(comparison_text)
            report["checked_pages"].append(number)
            if all(normalized(s) in coverage for s in segments(native) if len(normalized(s)) >= 8):
                continue
            if len(report["converted_pages"]) >= max_fallback_pages or time.monotonic()-started > time_budget:
                report["deferred_pages"].append(number)
                continue
            try:
                import pymupdf4llm
                markdown = pymupdf4llm.to_markdown(pdf, pages=[number-1], use_ocr=False, show_progress=False)
                report["converted_pages"].append(number)
                additions, review = select_supplements(comparison_text, markdown, native)
            except Exception as error:
                report["errors"].append({"page": number, "reason": type(error).__name__})
                continue
            report["review"].extend({"page": number, **r} for r in review)
            if additions:
                result["pages"][number-1] += f"\n[PDF {number}페이지 · 누락 보완 텍스트]"
            for text in additions:
                record = {"text": text, "pages": [number], "source": "pymupdf4llm",
                          "kind": "embedded_text_supplement", "placement": "page_end"}
                result["sections"].append(record)
                result["supplements"].append(record.copy())
                result["pages"][number-1] += ("\n" if result["pages"][number-1] else "") + text
    result["text"] = "\n\n".join(result["pages"])
    report["seconds"] = round(time.monotonic()-started, 3)
    return result


def supplement_docling_pdf(source, doc, payload=None, **options):
    from document_postprocessing import build_document_payload
    from lighton_supplement import enabled, supplement_picture_text
    lighton_report = None
    if enabled():
        doc, lighton_report = supplement_picture_text(source, doc)
        payload = build_document_payload(doc)
    ignored = {}
    for item in doc.texts:
        if str(item.content_layer.value) != "body" or str(item.label.value) in ("page_header", "page_footer"):
            for prov in item.prov:
                ignored.setdefault(prov.page_no, []).append(item.text)
    result = supplement_pdf_payload(source, payload if payload is not None else build_document_payload(doc),
                                  page_numbers=doc.pages.keys(), ignored_by_page=ignored, **options)
    if lighton_report is not None: result['lighton_report'] = lighton_report
    return result


def supplement_warnings(payload):
    report = payload.get("supplement_report", {})
    warnings = []
    lighton = payload.get('lighton_report', {})
    if lighton.get('accepted'):
        warnings.append('그림 속 일부 문구를 LightOn으로 보완했습니다. 위치와 두 번의 인식 결과를 대조했지만 원문 확인이 필요합니다.')
    if lighton.get('review') or lighton.get('errors') or lighton.get('deferred'):
        warnings.append('확신하기 어려운 LightOn 후보는 반영하지 않았습니다. lighton_report를 확인하세요.')
    if payload.get("supplements"):
        warnings.append("PDF의 누락된 텍스트를 PyMuPDF4LLM으로 페이지 끝에 보완했습니다. 표 셀 위치·관계는 복원하지 않았습니다.")
    if report.get("review"):
        warnings.append("일부 보완 후보는 기존 내용과 겹치거나 달라 자동 병합하지 않았습니다.")
    if report.get("errors") or report.get("deferred_pages"):
        warnings.append("일부 페이지의 텍스트 보완을 완료하지 못했습니다. supplement_report를 확인하세요.")
    return warnings
