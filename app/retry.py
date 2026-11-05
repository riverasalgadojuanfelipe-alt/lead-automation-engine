"""Reintentos con backoff exponencial + jitter."""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Tuple, Type, TypeVar

log = logging.getLogger("retry")
T = TypeVar("T")


class PermanentError(Exception):
    """Error que NO tiene sentido reintentar (p. ej. 400, API key inválida)."""


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    label: str = "operation",
) -> T:
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except PermanentError:
            raise
        except retry_on as exc:  # noqa: PERF203
            last = exc
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter: evita reintentos sincronizados
            log.warning("%s falló (intento %d/%d): %s — reintento en %.1fs", label, attempt, attempts, exc, delay)
            sleep(delay)
    assert last is not None
    raise last
