import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Official bulletins are public sites: identify the client honestly instead of impersonating a browser
USER_AGENT = "Justicio/1.0 (+https://github.com/bukosabino/justicio)"
DEFAULT_TIMEOUT_SECONDS = 30


class _TimeoutSession(requests.Session):
    """Session that applies a default timeout, so that no request can block an ETL indefinitely."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(method, url, **kwargs)


def create_session(retries: int = 5, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> requests.Session:
    """HTTP session with retries (exponential backoff, honouring Retry-After) and a default timeout."""
    session = _TimeoutSession(timeout)
    retry = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        # The POST requests made by the scrappers are idempotent search forms
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9"})
    return session


def get_soup(session: requests.Session, url: str, parser: str = "html.parser", **kwargs) -> BeautifulSoup:
    """GET a page and parse it, raising requests.HTTPError for error statuses."""
    response = session.get(url, **kwargs)
    response.raise_for_status()
    # Some sites declare their charset only in the HTTP header; otherwise BeautifulSoup detects it from the page
    declared_charset = "charset=" in response.headers.get("Content-Type", "").lower()
    return BeautifulSoup(response.content, parser, from_encoding=response.encoding if declared_charset else None)
