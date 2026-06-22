"""Abstract base class every source worker implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Iterator

from ..models import FetchTarget, PriceSnapshot
from ..ratelimit import TokenBucket


class Source(ABC):
    """Fetches current prices for a set of targets and yields normalized
    PriceSnapshot objects. Implementations must call ``self.limiter.acquire()``
    before each upstream request to honor the vendor's documented rate limit.
    """

    #: stable identifier, also the key used in the crosswalk and the registry
    name: str = ""

    def __init__(self, limiter: TokenBucket) -> None:
        self.limiter = limiter

    @abstractmethod
    def fetch(self, targets: Iterable[FetchTarget]) -> Iterator[PriceSnapshot]:
        """Yield a snapshot for each target that has a current price."""
        raise NotImplementedError
