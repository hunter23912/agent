"""Locally reflow PDF prose for the preview, keeping stored extracts intact."""
from dataclasses import dataclass
import re
from statistics import median
from threading import Lock


# Guard both text inspection and page rendering across Streamlit sessions.
PDFIUM_LOCK = Lock()
_HEADING = re.compile(
    r'^(?:(?:\d+(?:\.\d+)*\.?\s+)?(?:abstract|introduction|related work|background|method(?:s|ology)?|'
    r'experiments?|results?|discussion|conclusions?|references|appendix|limitations?|acknowledg[e]?ments?)|'
    r'摘要|引言|方法|实验|结论|参考文献|附录|致谢)$', re.IGNORECASE,
)
_NUMBERED_HEADING = re.compile(r'^\d+(?:\.\d+)*\.?\s+[A-Z][^.!?。！？]*$')
_CAPTION = re.compile(r'^(?:figure|fig\.|table|algorithm|图|表)\s*\d+[a-z]?(?:\s*[:.：]|\s|$)', re.IGNORECASE)
_LIST = re.compile(r'^(?:[•●▪*†‡]\s*|[-–]\s+|\(?\d+\)\s+|[a-zA-Z]\)\s+|\[\d+\]\s+|[（(][一二三四五六七八九十]+[）)])')
_NUMBER = re.compile(r'(?<!\w)\d+(?:\.\d+)?%?(?!\w)')
_END_WORD = re.compile(r'([A-Za-z0-9]+(?:[-‐‑][A-Za-z0-9]+)*)([-‐\u00ad])$')
_START_WORD = re.compile(r'[A-Za-z0-9]+')
_CJK = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7a3]')
_SENTENCE_END = re.compile(r'[.!?。！？][”’"\')）\]]*$')
_COMPOUND_PREFIXES = frozenset({'anti', 'cross', 'high', 'low', 'multi', 'non', 'post', 'pre', 'self', 'semi', 'well'})


@dataclass(frozen=True)
class _LinePosition:
    left: float
    right: float
    y: float
    font_size: float
    start: int
    end: int


def _page_positions(pdf_bytes, page_number, lines):
    import pypdfium2 as pdfium
    from pypdfium2 import raw

    with PDFIUM_LOCK, pdfium.PdfDocument(pdf_bytes, password='') as document:
        page = document[page_number - 1]
        text_page = page.get_textpage()
        try:
            stream = ''.join(chr(raw.FPDFText_GetUnicode(text_page.raw, index)) for index in range(text_page.count_chars()))
            normalized, char_indices = [], []
            for index, character in enumerate(stream):
                if character.isalnum():
                    folded = character.casefold()
                    normalized.append(folded)
                    char_indices.extend([index] * len(folded))
            text = ''.join(normalized)
            cursor = 0
            positions = []
            for line in lines:
                key = ''.join(character.casefold() for character in line if character.isalnum())
                position = text.find(key, cursor, cursor + len(key) + 768) if key else -1
                if position < 0:
                    positions.append(None)
                    continue
                cursor = position + len(key)
                first, last = char_indices[position], char_indices[cursor - 1]
                try:
                    left, y, _, _ = text_page.get_charbox(first)
                    _, last_y, right, _ = text_page.get_charbox(last)
                    font = raw.FPDFText_GetFontSize(text_page.raw, first)
                except pdfium.PdfiumError:
                    positions.append(None)
                    continue
                # A physical line must not span an unrelated row or column.
                positions.append(_LinePosition(left, right, y, font, first, last)
                                 if font > 0 and right >= left and abs(last_y - y) <= font else None)
            return positions, stream
        finally:
            text_page.close()
            page.close()


def _heading(line):
    return bool(_HEADING.fullmatch(line) or (len(line) <= 72 and _NUMBERED_HEADING.fullmatch(line)))


def _fixed_line(line):
    if _heading(line):
        return True
    if ('@' in line and len(line.split()) <= 10) or re.fullmatch(r'[\d\W_]+', line):
        return True
    if re.match(r'^[*†‡]', line) or re.search(r'\S(?:\t| {3,})\S', line):
        return True
    if re.search(r'[=∑∫√≤≥]', line) and len(line.split()) <= 14:
        return True
    return len(line) <= 65 and len(_NUMBER.findall(line)) >= 2 and len(line.split()) <= 9


def _same_paragraph(left, right, left_pos, right_pos, line_width, uses_positions):
    if not left or not right or _fixed_line(left) or _fixed_line(right) or _CAPTION.match(right) or _LIST.match(right):
        return False
    if left_pos and right_pos:
        font = left_pos.font_size
        if abs(right_pos.font_size - font) > font * 0.15 or abs(right_pos.left - left_pos.left) > font * 0.65:
            return False
        if not font * 0.55 <= left_pos.y - right_pos.y <= font * 1.55:
            return False
        return not (_SENTENCE_END.search(left) and right_pos.right - left_pos.right > font * 3)
    if uses_positions:
        return False
    # Older imports only have extracted lines, so merge only prose-length lines.
    return (len(left) >= line_width * 0.78 and len(right) >= min(24, line_width * 0.55)
            and (len(left.split()) >= 4 and len(right.split()) >= 3 or _CJK.search(left) and _CJK.search(right)))


def _join_lines(left, right, left_pos, right_pos, source, original_words):
    tail, head = _END_WORD.search(left), _START_WORD.match(right)
    if tail and head:
        first, hyphen = tail.groups()
        second = head[0]
        compound = (first + '-' + second).casefold()
        marked = left_pos and right_pos and '\x02' in source[left_pos.end + 1:right_pos.start]
        conservative = (first.casefold() in _COMPOUND_PREFIXES or re.search(r'[-‐‑0-9]', first)
                        or first.isupper() or second[0].isupper() or compound in original_words)
        if hyphen == '\u00ad' or not conservative and (marked or (first + second).casefold() in original_words):
            return '', True
        return '', False
    chinese_punctuation = '，。；：！？、（）【】《》'
    if ((_CJK.fullmatch(left[-1]) or left[-1] in chinese_punctuation) and _CJK.fullmatch(right[0])
            or right[0] in chinese_punctuation):
        return '', False
    return ' ', False


def _paragraph_gap(left, right, left_pos, right_pos):
    if not left or not right:
        return False
    if _heading(left) or _heading(right) or _CAPTION.match(right):
        return True
    if _fixed_line(left) or _fixed_line(right) or _LIST.match(right) or _CAPTION.match(left):
        return False
    if left_pos and right_pos:
        font = left_pos.font_size
        if abs(left_pos.font_size - right_pos.font_size) <= font * 0.15 and abs(left_pos.left - right_pos.left) <= font * 1.25:
            return True
    return min(len(left), len(right)) >= 26 and bool(_SENTENCE_END.search(left))


def reflow_pdf_text(raw_text, pdf_bytes=None, page_number=1):
    """Remove visual line wraps without rewriting or reordering the extract."""
    lines = [line.strip() for line in raw_text.splitlines()]
    if len(lines) < 2:
        return raw_text
    positions, source = [None] * len(lines), ''
    if pdf_bytes:
        try:
            from pypdfium2 import PdfiumError
        except ImportError:
            pass
        else:
            try:
                positions, source = _page_positions(pdf_bytes, page_number, lines)
            except (PdfiumError, OSError, ValueError, IndexError):
                pass  # Use the stored text when the original cannot be read.

    uses_positions = any(positions)
    widths = [len(line) for line in lines if len(line) >= 32 and not _fixed_line(line)]
    if not uses_positions and not widths:
        return raw_text
    line_width = median(widths) if widths else 0
    original_words = set(re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*', (raw_text + '\n' + source).casefold()))
    output = [lines[0]]
    for i, right in enumerate(lines[1:], 1):
        left = lines[i - 1]
        left_pos, right_pos = positions[i - 1], positions[i]
        if _same_paragraph(left, right, left_pos, right_pos, line_width, uses_positions):
            separator, drop_hyphen = _join_lines(left, right, left_pos, right_pos, source, original_words)
            if drop_hyphen:
                output[-1] = output[-1][:-1]
        elif _paragraph_gap(left, right, left_pos, right_pos):
            separator = '\n\n'
        else:
            separator = '\n'
        output.extend((separator, right))
    return ''.join(output)
