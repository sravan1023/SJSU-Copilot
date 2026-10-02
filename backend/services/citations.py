"""
Citation normalisation for model output.

The RAG prompt asks for [1], [2] citations, and the UI only understands that
form. gpt-oss ignores the instruction and cites in its trained browsing format
instead -- 【2†L31-L33】 or 【2†source】 -- which would reach the user as raw
markers. The number is the prompt's source label, so it maps straight to [2].
"""
import re

_CITE_RE = re.compile(r"【\s*(\d+)\s*(?:†[^【】]*)?】")

# Longer than any real marker. An opening 【 that has not closed within this
# many characters is ordinary text, so stop holding it back.
_MAX_HELD_CHARS = 64


def normalize_citations(text: str) -> str:
    """Rewrite 【N†...】 markers as [N]. Anything else is left untouched."""
    return _CITE_RE.sub(r"[\1]", text)


class CitationStream:
    """Normalises citations in a token stream.

    A marker usually arrives split across deltas ("【", "2", "†L31", "-L33】"),
    so text from an unclosed 【 onward is held back until it closes. Everything
    before it is released immediately, so ordinary text is never delayed.
    """

    def __init__(self) -> None:
        self._held = ""

    def feed(self, chunk: str) -> str:
        text = self._held + chunk
        self._held = ""
        open_at = text.rfind("【")
        if (
            open_at != -1
            and "】" not in text[open_at:]
            and len(text) - open_at <= _MAX_HELD_CHARS
        ):
            self._held = text[open_at:]
            text = text[:open_at]
        return normalize_citations(text)

    def flush(self) -> str:
        """Release whatever is still held once the stream has ended."""
        text, self._held = self._held, ""
        return normalize_citations(text)
