"""Destination selection after an immutable access decision."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Protocol, TypeVar

from app.db.models import AccessResult, TargetUrl


class RandomChoice(Protocol):
    def choice(self, sequence: Sequence[TargetUrl]) -> TargetUrl: ...

    def choices(self, population: Sequence[TargetUrl], weights: Sequence[int], k: int) -> list[TargetUrl]: ...


T = TypeVar("T", bound=TargetUrl)


def choose_target(destinations: Sequence[T], result: AccessResult | str, rng: RandomChoice = random) -> T | None:
    """Choose an active destination of the class selected by the access decision."""
    target_type = "allowed" if str(result) == "allowed" else "blocked"
    active = [
        destination
        for destination in destinations
        if destination.is_active and str(destination.url_type) == target_type
    ]
    if not active:
        return None

    positive_weights = [destination.weight for destination in active if destination.weight > 0]
    if not positive_weights:
        return rng.choice(active)

    weighted = [destination for destination in active if destination.weight > 0]
    return rng.choices(weighted, weights=[destination.weight for destination in weighted], k=1)[0]
