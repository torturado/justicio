import re

from lxml import etree


class ScrapperError(Exception):
    """A downloaded page does not have the expected structure."""


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_text(text: str) -> str:
    """Replace non-breaking spaces, tabs and line breaks with spaces."""
    return re.sub(r"(\xa0|\t+|\n+)", " ", text)


def xml_element_text(element: etree._Element | None) -> str:
    """Text of an XML element and its descendants, with whitespace collapsed."""
    if element is None:
        return ""
    return normalize_whitespace(" ".join(element.itertext()))


def xml_to_text(element: etree._Element) -> str:
    """Plain text of a BOE XML text element (HTML-like markup).

    One line per paragraph (also inside notes and quotes), tables flattened row by row with cells separated by " | ", images dropped.
    """
    lines: list[str] = []
    for child in element:
        _append_text_lines(child, lines)
    return "\n".join(lines)


def _append_text_lines(element: etree._Element, lines: list[str]) -> None:
    # Comments and processing instructions have a non-string tag
    if not isinstance(element.tag, str):
        return
    tag_name = etree.QName(element).localname.lower()
    if tag_name == "img":
        return
    if tag_name == "table":
        for row in element.iter("{*}tr"):
            cells = [xml_element_text(cell) for cell in row if etree.QName(cell).localname.lower() in {"td", "th"}]
            if any(cells):
                lines.append(" | ".join(cells))
        return
    if tag_name in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"} or not len(element):
        text = xml_element_text(element)
        if text:
            lines.append(text)
        return
    for child in element:
        _append_text_lines(child, lines)
