import asyncio

import pytest
from fastapi import HTTPException

from src.service.limits import AdmissionLimiter, RateLimiter


def test_rate_limiter_rejects_requests_over_the_limit_until_the_next_minute():
    now = [120.0]
    limiter = RateLimiter(requests_per_minute=2, clock=lambda: now[0])

    limiter.check("203.0.113.7")
    limiter.check("203.0.113.7")
    limiter.check("198.51.100.1")
    with pytest.raises(HTTPException) as error:
        limiter.check("203.0.113.7")
    assert error.value.status_code == 429

    now[0] = 180.0
    limiter.check("203.0.113.7")


def test_admission_limiter_rejects_requests_beyond_the_concurrency_limit():
    limiter = AdmissionLimiter(max_concurrent=1)

    async def scenario():
        async with limiter.slot():
            with pytest.raises(HTTPException) as error:
                async with limiter.slot():
                    pass
            assert error.value.status_code == 503
        async with limiter.slot():
            pass

    asyncio.run(scenario())
