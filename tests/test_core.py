"""Offline unit tests (no models needed)."""
import os
import tempfile

os.environ.setdefault("POLISCI_RAG_HOME", tempfile.mkdtemp())

import pymupdf  # noqa: E402

from polisci_rag import export, pdf_ingest  # noqa: E402
from polisci_rag.retrieval import fts_query  # noqa: E402
from polisci_rag.store import get_store  # noqa: E402


def _make_pdf(path, chapters=3, pages_per=6):
    doc = pymupdf.open()
    toc = []
    for c in range(chapters):
        for k in range(pages_per):
            p = doc.new_page()
            if k == 0:
                toc.append([1, f"Chapter {c + 1}", doc.page_count])
            body = "Header Line\n\n" + f"Chapter {c + 1} discusses sovereignty and war-making. " * 20
            p.insert_textbox(pymupdf.Rect(50, 50, 550, 780), body + f"\n{doc.page_count}", fontsize=10)
    doc.set_toc(toc)
    doc.save(path)
    return path


def test_sections_and_chunks(tmp_path):
    path = _make_pdf(str(tmp_path / "t.pdf"))
    doc = pymupdf.open(path)
    pages = pdf_ingest.clean_pages([p.get_text() for p in doc])
    assert all("Header Line" not in p for p in pages)  # running header removed
    secs = pdf_ingest.detect_sections(doc, pages)
    assert [s["title"] for s in secs] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert secs[0]["start"] == 1 and secs[-1]["end"] == 18
    chunks = pdf_ingest.chunk_pages(pages, secs, 200, 30)
    assert chunks and all(c["pno_start"] <= c["pno_end"] for c in chunks)
    assert {c["section_idx"] for c in chunks} == {0, 1, 2}


def test_long_sections_are_split():
    pages = ["text " * 50] * 200
    doc = pymupdf.open()
    for _ in range(200):
        doc.new_page()
    secs = pdf_ingest.detect_sections(doc, pages)
    assert all(s["end"] - s["start"] + 1 <= pdf_ingest.MAX_SECTION_PAGES for s in secs)
    assert secs[-1]["end"] == 200


def test_fts_query():
    q = fts_query("What does the author argue about the bellicist theory of state formation?")
    assert '"bellicist"' in q and '"the"' not in q


def test_export_docx():
    st = get_store()
    did = st.add_document("Coercion, Capital and European States", "Charles Tilly", "x.pdf", "x.pdf")
    md = "# Title\n\n## Part\n\nWar made the state [D%d p.14].\n\n- a **bold** point\n\n| A | B |\n|---|---|\n| 1 | 2 |\n" % did
    out = export.humanize_citations(md)
    assert "p. 14)" in out and "Sources" in out
    p = export.to_docx("test", md)
    assert p.exists() and p.stat().st_size > 1000
