"""Tracing with Langtrace, which exports OpenTelemetry spans when LANGTRACE_API_KEY is set."""

import ipaddress
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from langtrace_python_sdk import SendUserFeedback, langtrace
from langtrace_python_sdk.constants.instrumentation.common import LANGTRACE_ADDITIONAL_SPAN_ATTRIBUTES_KEY
from opentelemetry import baggage, context, trace

TRACER = trace.get_tracer("justicio")


def initialize_tracing() -> bool:
    """Start Langtrace if it is configured. Returns whether tracing (and user feedback) is enabled."""
    api_key = os.environ.get("LANGTRACE_API_KEY")
    if api_key:
        langtrace.init(api_key=api_key)
    return bool(api_key)


def anonymize_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> str | None:
    """Network prefix of an IP (/24 for IPv4, /48 for IPv6), so traces do not store the full address."""
    if ip is None:
        return None
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False).network_address)


@contextmanager
def span_attributes(attributes: Mapping[str, str | None]) -> Iterator[None]:
    """Add attributes to the spans that Langtrace records within the block."""
    values = {key: value for key, value in attributes.items() if value is not None}
    token = context.attach(baggage.set_baggage(LANGTRACE_ADDITIONAL_SPAN_ATTRIBUTES_KEY, values))
    try:
        yield
    finally:
        context.detach(token)


def send_feedback(span_id: str, trace_id: str, user_score: int) -> None:
    """Attach a user score to a traced answer. Blocking: run it in a worker thread."""
    SendUserFeedback().evaluate(data={"spanId": span_id, "traceId": trace_id, "userScore": user_score, "userId": None})
