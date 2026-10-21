import logging as lg
import time
from functools import wraps

from langtrace_python_sdk.constants.instrumentation.common import (
    LANGTRACE_ADDITIONAL_SPAN_ATTRIBUTES_KEY,
)
from opentelemetry import baggage, context


def timeit(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        logger = lg.getLogger(func.__name__)
        logger.info("<<< Starting  >>>")
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        delta = end_time - start_time
        msg = f"{delta:2.2f}s" if delta > 1 else f"{1000 * delta:2.1f}ms"
        logger.info("<<< Completed >>> in %s", msg)
        return result

    return wrapper


async def inject_additional_attributes(fn, attributes=None):
    if attributes:
        new_ctx = baggage.set_baggage(LANGTRACE_ADDITIONAL_SPAN_ATTRIBUTES_KEY, attributes)
        context.attach(new_ctx)

    return await fn()
