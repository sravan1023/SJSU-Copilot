"""
Chunking and section extraction.

Run from backend/ with:
    python -m pytest tests/test_kb_chunker.py

Pure and fast -- no network, no database. Worth having thorough coverage here
because chunking is where retrieval quality is decided, and because every bug
these tests pin was a real one found by surveying the actual seed list rather
than by reading the code.
"""
import pytest

from kb.chunker import (
    MIN_CHUNK_CHARS,
    OVERLAP_CHARS,
    TARGET_CHARS,
    Section,
    chunk_html,
    chunk_sections,
    chunk_text,
    split_into_sections,
)


def _page(body: str) -> str:
    return f"<html><body><main>{body}</main></body></html>"


# ── Heading paths ─────────────────────────────────────────────────────────────


def test_sibling_headings_replace_rather_than_nest():
    """The bug that shipped first: `del path[level - 1:]` made every h2 nest.

    Two sibling sections came out as "Admissions > Deadlines", which is a
    heading path that describes a document structure that does not exist.
    """
    sections = split_into_sections(
        _page("<h2>Admissions</h2><p>alpha</p><h2>Deadlines</h2><p>beta</p>")
    )
    paths = [s.heading_path for s in sections]
    assert paths == ["Admissions", "Deadlines"]


def test_deeper_headings_nest():
    sections = split_into_sections(
        _page("<h2>Transfer</h2><p>alpha</p><h3>Deadlines</h3><p>beta</p>")
    )
    assert [s.heading_path for s in sections] == ["Transfer", "Transfer > Deadlines"]


def test_a_shallower_heading_pops_the_stack():
    sections = split_into_sections(
        _page(
            "<h2>Transfer</h2><p>a</p>"
            "<h3>Deadlines</h3><p>b</p>"
            "<h2>Graduate</h2><p>c</p>"
        )
    )
    assert [s.heading_path for s in sections] == [
        "Transfer",
        "Transfer > Deadlines",
        "Graduate",
    ]


def test_a_page_starting_at_h2_is_not_treated_as_nested():
    """Plenty of SJSU pages have no h1 at all, so depth cannot be assumed."""
    sections = split_into_sections(_page("<h2>Parking</h2><p>alpha</p>"))
    assert [s.heading_path for s in sections] == ["Parking"]


def test_page_furniture_is_dropped():
    """Agrees with services/web_search._extract_main_text about what is chrome."""
    html = _page(
        "<nav><a href='/x'>Menu</a></nav>"
        "<h2>Content</h2><p>the real text</p>"
        "<footer>copyright</footer><script>var x=1</script>"
    )
    text = " ".join(s.text for s in split_into_sections(html))
    assert "the real text" in text
    assert "Menu" not in text and "copyright" not in text and "var x" not in text


def test_a_page_with_no_headings_still_yields_a_section():
    sections = split_into_sections(_page("<p>" + "word " * 60 + "</p>"))
    assert len(sections) == 1
    assert sections[0].heading_path == ""


# ── Chunk sizing ──────────────────────────────────────────────────────────────


def test_heading_dense_pages_do_not_produce_runts():
    """The measured failure: sjsu.edu/isss/ gave 16 chunks of 36-321 chars.

    Across the real seed list, 17 of 162 chunks were under 100 characters.
    `ts_rank_cd` normalises by document length, so a 36-character fragment that
    happens to contain a query term outranks the 900-character passage that
    answers the question.
    """
    body = "".join(f"<h2>Section {i}</h2><p>A short line about topic {i}.</p>" for i in range(14))
    chunks = chunk_html(_page(body))
    assert chunks, "a heading-dense page must still produce chunks"
    assert min(c.char_count for c in chunks) >= 100


def test_a_long_section_is_split_near_the_target():
    chunks = chunk_html(_page("<h2>Long</h2><p>" + ("word " * 600) + "</p>"))
    assert len(chunks) > 1
    assert max(c.char_count for c in chunks) <= TARGET_CHARS + OVERLAP_CHARS + 50


def test_splitting_never_breaks_a_word():
    """A half word in the generated tsvector is a lexeme that matches nothing."""
    words = [f"token{i:04d}" for i in range(400)]
    chunks = chunk_html(_page("<h2>H</h2><p>" + " ".join(words) + "</p>"))
    for chunk in chunks:
        for token in chunk.content.split():
            token = token.strip(".,")
            if token.startswith("token"):
                assert token in words, f"{token!r} was cut mid-word"


def test_consecutive_chunks_overlap():
    """So a definition straddling a split stays retrievable from either side."""
    sentences = " ".join(f"Sentence number {i} says something distinct." for i in range(80))
    chunks = chunk_html(_page("<h2>H</h2><p>" + sentences + "</p>"))
    assert len(chunks) >= 2
    tail = chunks[0].content[-OVERLAP_CHARS:]
    shared = [w for w in tail.split() if w in chunks[1].content]
    assert shared, "no overlap between consecutive chunks"


def test_chunk_indices_are_contiguous_from_zero():
    """`unique (document_id, chunk_index)` and replace_document_chunks rely on it."""
    body = "".join(f"<h2>S{i}</h2><p>{'word ' * 120}</p>" for i in range(5))
    chunks = chunk_html(_page(body))
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_subheadings_survive_grouping_in_the_text():
    """Grouping can leave `heading` empty; the leaves must not vanish with it.

    When a group spans sections with no common ancestor the group heading is
    empty, so each section's own heading is kept inline -- the generated tsv
    concatenates heading and content, so an inlined heading is still searchable.
    """
    body = "<h2>Parking</h2><p>alpha</p><h2>Shuttles</h2><p>beta</p>"
    chunks = chunk_html(_page(body))
    combined = " ".join((c.heading or "") + " " + c.content for c in chunks)
    assert "Parking" in combined and "Shuttles" in combined


def test_the_heading_is_not_duplicated_into_the_content():
    """`tsv` is generated as heading || ' ' || content, so prepending double-counts."""
    chunks = chunk_html(_page("<h2>Unique Heading Token</h2><p>" + ("word " * 250) + "</p>"))
    headed = [c for c in chunks if c.heading == "Unique Heading Token"]
    assert headed, "expected the heading to be captured"
    assert not headed[0].content.startswith("Unique Heading Token")


# ── chunk_text, the PDF path ──────────────────────────────────────────────────


def test_chunk_text_handles_plain_text():
    chunks = chunk_text("word " * 500, heading="Policy")
    assert len(chunks) > 1
    assert all(c.heading == "Policy" for c in chunks)


def test_empty_input_is_not_a_chunk():
    assert chunk_html(_page("")) == []
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_a_single_short_section_is_kept_rather_than_dropped():
    """A one-paragraph page is still worth indexing, runt rules notwithstanding."""
    chunks = chunk_sections([Section("Title", "A single short paragraph of text.")])
    assert len(chunks) == 1
    assert chunks[0].char_count < MIN_CHUNK_CHARS


# ── PDF extraction ────────────────────────────────────────────────────────────


def test_pdf_furniture_is_stripped():
    """pypdf returns the running header and footer on every page.

    Left in, a thirty-page policy document carries its title through the body
    thirty times, which dominates the tsvector and makes every chunk look alike.
    """
    pytest.importorskip("pypdf")
    from kb.fetcher import extract_pdf_text

    class _FakePage:
        def __init__(self, n):
            self.n = n

        def extract_text(self):
            return f"SJSU POLICY MANUAL\nBody paragraph number {self.n}.\nPage {self.n}"

    class _FakeReader:
        def __init__(self, *_a, **_kw):
            self.pages = [_FakePage(i) for i in range(6)]

    import kb.fetcher as fetcher_mod
    import sys
    import types

    fake = types.ModuleType("pypdf")
    fake.PdfReader = _FakeReader
    saved = sys.modules.get("pypdf")
    sys.modules["pypdf"] = fake
    try:
        text = extract_pdf_text(b"%PDF-1.4 fake")
    finally:
        if saved is not None:
            sys.modules["pypdf"] = saved
        else:
            del sys.modules["pypdf"]

    assert "Body paragraph number 3." in text
    # Repeated on every page, so it is furniture.
    assert text.count("SJSU POLICY MANUAL") == 0
    # "Page N" differs per page, so it is not furniture and survives.
    assert "Page 3" in text
