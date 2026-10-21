import random
import re
import typing as tp

import requests
from bs4 import BeautifulSoup
from lxml import etree
from requests.exceptions import Timeout

from src.etls.utils import create_retry_session


class ScrapperError(Exception):
    """
    Custom exception for scraping errors.
    """

    def __init__(self, message="Error durante el proceso de scraping", *args, **kwargs):
        """
        Initializes the exception with a custom error message.

        :param message: Error message describing the failure.
        :param args: Additional positional arguments.
        :param kwargs: Additional keyword arguments.
        """
        super().__init__(message, *args, **kwargs)
        self.message = message

    def __str__(self):
        """
        Returns a string representation of the exception, including the error message.
        """
        return f"ScrapperError: {self.message}"


class HTTPRequestException(Exception):
    """
    Exception for errors occurring during HTTP requests made by HTTPRequester.
    """

    def __init__(self, message="HTTP request error", *args):
        super().__init__(message, *args)


class HTTPRequester:
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
    ]

    default_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    @classmethod
    def get_random_user_agent(cls):
        """
        Selects and returns a random User-Agent from the list of user_agents.
        """
        return random.choice(cls.user_agents)

    @classmethod
    def get_headers(cls):
        """
        Generates and returns headers including a random User-Agent.
        """
        headers = cls.default_headers.copy()
        headers["User-Agent"] = cls.get_random_user_agent()
        return headers

    @staticmethod
    def get_soup(url, timeout=30, markup="html.parser"):
        """
        Performs an HTTP GET request to the provided URL, using random headers, and returns a BeautifulSoup
        object if the response is successful. If there is an error or timeout, it throws HTTPRequestException.
        """
        headers = HTTPRequester.get_headers()
        try:
            session = create_retry_session(retries=5)
            response = session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return BeautifulSoup(response.content, markup)
        except Timeout as e:
            raise HTTPRequestException(f"HTTP request timed out: {e}")
        except requests.RequestException as e:
            raise HTTPRequestException(f"HTTP request failed: {e}")


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def xml_element_text(element: tp.Optional[etree._Element]) -> str:
    """Text of an XML element and its descendants, with whitespace collapsed."""
    if element is None:
        return ""
    return normalize_whitespace(" ".join(element.itertext()))


def xml_to_text(element: etree._Element) -> str:
    """Plain text of a BOE XML text element (HTML-like markup).

    One line per paragraph (also inside notes and quotes), tables flattened row by row with cells separated by " | ", images dropped.
    """
    lines: tp.List[str] = []
    for child in element:
        _append_text_lines(child, lines)
    return "\n".join(lines)


def _append_text_lines(element: etree._Element, lines: tp.List[str]) -> None:
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
