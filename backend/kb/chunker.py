"""Turning a page into retrievable chunks.

`services/web_search._extract_main_text` returns newline-joined text with **all
heading structure destroyed**, which is right for stuffing a whole page into a
prompt and useless here. A chunk needs to know where in the document it came
from: "Deadlines" under "Admissions > Transfer" is a different answer from
"Deadlines" under "Financial Aid", and the heading path is the cheapest signal
that distinguishes them. So the sectioning pass below is its own thing, using
the same container candidates and the same `decompose()` list so it agrees with
the live extractor about what counts as page furniture.

The chunk size is not a round number picked for looks. `MAX_TOTAL_CHARS` is
10,000 and `MAX_SOURCES` is 5, so about two chunks per document puts the whole
retrieved context near 2,000 characters -- roughly 560 tokens, against the
4,000-5,500 a crawled-page answer costs today. That reduction is the point:
Groq's free tier is 8,000 tokens a minute for the whole organisation, so smaller
prompts are more answers per minute, not just cheaper ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Kept in step with services/web_search._extract_main_text so both agree on what
# is content and what is chrome.
_CONTAINER_SELECTORS = (
    "main",
    "article",
    "div#content",
    "div#main-content",
    "div#page-content",
    "section",
    "body",
)
_STRIP_TAGS = ("script", "style", "noscript", "svg", "footer", "nav", "aside", "header", "form")

TARGET_CHARS = 900
OVERLAP_CHARS = 120
MIN_CHUNK_CHARS = 200
MAX_HEADING_DEPTH = 3

_WS = re.compile(r"[ \t\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
# Sentence end followed by whitespace. Deliberately simple: an abbreviation
# occasionally splits early, which costs nothing, where a complex rule that
# mis-handles a real sentence boundary costs a dead chunk.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Section:
    heading_path: str
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    heading: str | None
    content: str

    @property
    def char_count(self) -> int:
        return len(self.content)


def _clean(text: str) -> str:
    text = _WS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def split_into_sections(html: str) -> list[Section]:
    """Walk the main content, opening a new section at each h1/h2/h3.

    Falls back to one unheaded section when a page has no headings at all, which
    is common on landing pages that are mostly navigation.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    # Same heuristic as the live extractor: of the plausible containers, take
    # whichever holds the most text. Page templates vary too much for a single
    # selector to be right.
    container = None
    best = 0
    for selector in _CONTAINER_SELECTORS:
        for candidate in soup.select(selector):
            size = len(candidate.get_text(" ", strip=True))
            if size > best:
                best, container = size, candidate
    if container is None:
        return []

    heading_tags = {f"h{i}" for i in range(1, MAX_HEADING_DEPTH + 1)}
    # (level, title), outermost first. A stack rather than indexing by level:
    # plenty of SJSU pages start at h2, and an earlier version that wrote
    # `del path[level - 1:]` made every h2 nest under the previous h2, so two
    # sibling sections came out as "Admissions > Deadlines".
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    buffer: list[str] = []

    def flush() -> None:
        text = _clean("\n".join(buffer))
        if text:
            sections.append(Section(" > ".join(t for _, t in stack), text))
        buffer.clear()

    for node in container.find_all(
        list(heading_tags) + ["p", "li", "td", "th", "dd", "dt", "pre", "blockquote"]
    ):
        name = node.name.lower()
        if name in heading_tags:
            flush()
            level = int(name[1])
            title = node.get_text(" ", strip=True)
            # Pop while the top is at or below this level: h2 after h2 replaces,
            # h3 after h2 nests, h2 after h3 pops two.
            while stack and stack[-1][0] >= level:
                stack.pop()
            if title:
                stack.append((level, title))
        else:
            text = node.get_text(" ", strip=True)
            if text:
                buffer.append(text)
    flush()

    return sections


def _split_long(text: str) -> list[str]:
    """Break one over-long block on the best available boundary.

    Paragraph, then sentence, then word. Never mid-word: a half word in the
    generated tsvector is a lexeme that matches nothing.
    """
    if len(text) <= TARGET_CHARS:
        return [text]

    pieces: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= TARGET_CHARS:
            pieces.append(para)
            continue
        current = ""
        for sentence in _SENTENCE.split(para):
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 <= TARGET_CHARS:
                current = f"{current} {sentence}".strip()
                continue
            if current:
                pieces.append(current)
            if len(sentence) <= TARGET_CHARS:
                current = sentence
                continue
            # A single sentence longer than the target: hard-wrap on words.
            words = sentence.split(" ")
            current = ""
            for word in words:
                if len(current) + len(word) + 1 > TARGET_CHARS:
                    pieces.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
        if current:
            pieces.append(current)
    return pieces


def _tail_overlap(text: str) -> str:
    """The last ~OVERLAP_CHARS of a chunk, cut at a sentence boundary.

    Overlap exists so a definition that straddles a split stays retrievable from
    either side. Cutting mid-sentence would put a fragment at the head of the
    next chunk, which reads badly when it is the text the model is shown.
    """
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    parts = _SENTENCE.split(tail, maxsplit=1)
    return parts[-1].strip() if len(parts) > 1 else tail.strip()


def _common_prefix(paths: list[str]) -> str:
    """The deepest heading path all of these share."""
    splits = [path.split(" > ") if path else [] for path in paths]
    if not splits:
        return ""
    out: list[str] = []
    for parts in zip(*splits):
        if len(set(parts)) == 1:
            out.append(parts[0])
        else:
            break
    return " > ".join(out)


def _group_sections(sections: list[Section]) -> list[Section]:
    """Coalesce consecutive sections until each group is near the target size.

    Without this, a heading-dense page becomes a pile of fragments. Measured on
    the real seed list: sjsu.edu/isss/ produced 16 chunks of 36 to 321
    characters, and 17 of 162 chunks across the corpus were under 100 -- because
    the runt merge only fired for chunks sharing a heading, and consecutive
    sections never do.

    That matters beyond tidiness. `ts_rank_cd` normalises by document length, so
    a 36-character fragment containing a query term outranks the 900-character
    passage that actually answers the question.

    When a group spans several sections, each one's own leaf heading is kept
    inline in the text and the group's `heading` becomes their common ancestor.
    Nothing is discarded: the subheadings stay searchable through the generated
    tsvector and stay visible to the model.
    """
    groups: list[Section] = []
    bucket: list[Section] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if not bucket:
            return
        shared = _common_prefix([s.heading_path for s in bucket])
        parts: list[str] = []
        for section in bucket:
            leaf = section.heading_path
            if shared and leaf.startswith(shared):
                leaf = leaf[len(shared):].lstrip(" >")
            # Only worth a marker when this section sits below the group heading.
            parts.append(f"{leaf}\n{section.text}" if leaf else section.text)
        groups.append(Section(shared, "\n\n".join(parts)))
        bucket.clear()
        size = 0

    for section in sections:
        # Add, then close once the target is reached -- not "close before
        # overshooting", which left a group of two one-line sections at 18
        # characters because the next section was too big to join it. An
        # over-long group is fine: _split_long breaks it on a sentence boundary.
        bucket.append(section)
        size += len(section.text)
        if size >= TARGET_CHARS:
            flush()
    flush()
    return groups


def chunk_sections(sections: list[Section]) -> list[Chunk]:
    """Pack sections into chunks, preserving the heading path on each.

    The heading is stored separately rather than prepended to the content: the
    generated `tsv` column on `document_chunks` already concatenates
    `heading || ' ' || content`, so prepending would index it twice and show it
    twice in the prompt.
    """
    chunks: list[Chunk] = []
    for section in _group_sections(sections):
        heading = section.heading_path or None
        carry = ""
        for piece in _split_long(section.text):
            body = f"{carry} {piece}".strip() if carry else piece
            # An empty chunk costs a row, an embedding call and a tsvector that
            # matches nothing. _split_long returns [""] for empty input, so this
            # is reachable from an extractor that produced only whitespace.
            if not body.strip():
                continue
            chunks.append(Chunk(len(chunks), heading, body))
            carry = _tail_overlap(body)

    # Final safety net for a trailing runt that grouping could not absorb -- the
    # last section of a page, with nothing after it to join.
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and chunk.char_count < MIN_CHUNK_CHARS
            and merged[-1].heading == chunk.heading
            and merged[-1].char_count + chunk.char_count <= TARGET_CHARS + OVERLAP_CHARS
        ):
            previous = merged.pop()
            merged.append(
                Chunk(previous.chunk_index, previous.heading, f"{previous.content} {chunk.content}")
            )
        else:
            merged.append(Chunk(len(merged), chunk.heading, chunk.content))

    return [Chunk(i, c.heading, c.content) for i, c in enumerate(merged)]


def chunk_html(html: str) -> list[Chunk]:
    """Convenience: HTML straight to chunks."""
    return chunk_sections(split_into_sections(html))


def chunk_text(text: str, heading: str | None = None) -> list[Chunk]:
    """Chunk already-extracted text, for the PDF path which has no HTML."""
    cleaned = _clean(text)
    if not cleaned:
        return []
    return chunk_sections([Section(heading or "", cleaned)])
