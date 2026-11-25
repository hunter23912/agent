"""Split a page at paragraph or sentence boundaries when possible."""
from bisect import bisect_left, bisect_right
import re


MAX_CHUNK_SIZE = 1000
MAX_OVERLAP = 200
_PREFERRED_MIN = 800
_SOFT_MIN = 700
_PARAGRAPH = re.compile(r'\n[ \t]*\n+|(?m:^[ \t]*#{1,6}[ \t]+)')
_SENTENCE = re.compile(r'([。！？]+|[.!?]+)[”’"\'）)\]】》]*(?:[ \t]*\[\d+(?:[ ,;–-]+\d+)*\])*')
_ABBREVIATION = re.compile(
    r'(?<![a-z])(?:figs?|eqs?|refs?|secs?|tabs?|vol|nos?|pp?|vs|cf|dr|prof|mrs?|ms|etc|et al|e\.g|i\.e|[a-z])\.$',
    re.IGNORECASE,
)
_SPACE = re.compile(r'\s+')


def _sentence_boundaries(text):
    boundaries = []
    for match in _SENTENCE.finditer(text):
        mark = match[1]
        end = match.end()
        if mark.isascii() and end < len(text) and not text[end].isspace():
            continue
        if mark == '.' and _ABBREVIATION.search(text[max(0, match.start() - 18):match.start() + 1]):
            continue
        boundaries.append(end)
    return boundaries


def _last_boundary(boundaries, low, high):
    index = bisect_right(boundaries, high)
    if index and boundaries[index - 1] >= low:
        return boundaries[index - 1]
    return None


def _chunk_end(text, start, limit, paragraphs, sentences, boundaries):
    preferred = start + _PREFERRED_MIN
    for positions, minimum in ((paragraphs, preferred), (sentences, preferred), (boundaries, start + _SOFT_MIN)):
        end = _last_boundary(positions, minimum, limit)
        if end is not None:
            return end
    return max((match.start() for match in _SPACE.finditer(text, preferred, limit)), default=limit)


def _start_with_overlap(text, end, boundaries):
    earliest = end - MAX_OVERLAP
    previous = bisect_left(boundaries, end) - 1
    if previous >= 0 and boundaries[previous] >= earliest:
        return boundaries[previous]
    space = _SPACE.search(text, earliest, end)
    return space.end() if space and space.end() < end else earliest


def split_page(page_text):
    """Return faithful excerpts, with no more than 200 characters of overlap."""
    text = page_text.strip()
    if not text:
        return []
    if len(text) <= MAX_CHUNK_SIZE:
        return [text]

    paragraphs = [match.start() for match in _PARAGRAPH.finditer(text)]
    sentences = _sentence_boundaries(text)
    boundaries = sorted(set(paragraphs + sentences))
    chunks = []
    start = 0
    while start + MAX_CHUNK_SIZE < len(text):
        end = _chunk_end(text, start, start + MAX_CHUNK_SIZE, paragraphs, sentences, boundaries)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = _start_with_overlap(text, end, boundaries)
        while start < len(text) and text[start].isspace():
            start += 1
    if start < len(text):
        chunks.append(text[start:].strip())
    return chunks
