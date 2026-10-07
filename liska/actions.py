"""Akciók, döntés, okkódok. Az akciók dict formában is érkezhetnek (LLM, web)."""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from typing import Any, Optional, Protocol, Sequence

# okkódok
OK = "OK"
NOT_HOLDER = "NOT_HOLDER"
OWN_MODULE = "OWN_MODULE"
PRICE_DROP_LIMIT = "PRICE_DROP_LIMIT"
CAP_BELOW_PRICE = "CAP_BELOW_PRICE"
BID_BELOW_MIN = "BID_BELOW_MIN"
INSUFFICIENT_COVER = "INSUFFICIENT_COVER"
MAX_LEVEL = "MAX_LEVEL"
NO_OPEN_VOTE = "NO_OPEN_VOTE"
DUPLICATE = "DUPLICATE"
INVALID_TARGET = "INVALID_TARGET"
NOT_IN_REGIME = "NOT_IN_REGIME"
NO_CRISIS = "NO_CRISIS"
NO_SHIP = "NO_SHIP"


@dataclass(frozen=True)
class SetValuation:
    module: int
    price: float
    cap: float


@dataclass(frozen=True)
class Bid:
    module: int
    amount: float


@dataclass(frozen=True)
class Maintain:
    module: int
    amount: float


@dataclass(frozen=True)
class Upgrade:
    module: int


@dataclass(frozen=True)
class Vote:
    choice: float


@dataclass(frozen=True)
class Pledge:
    """Felajánlás a ma esedékes válság elhárítására. Ha az elhárítás nem jön össze, visszajár."""
    amount: float


@dataclass(frozen=True)
class BidItem:
    """Zárt licit a kereskedőhajó egyik, még el nem kelt eszközére, amíg a hajó itt van."""
    item: int
    amount: float


ACTION_TYPES = {c.__name__: c for c in (SetValuation, Bid, Maintain, Upgrade, Vote, Pledge, BidItem)}


@dataclass(frozen=True)
class Decision:
    actions: Sequence[Any] = ()   # prioritási sorrendben
    rationale: str = ""           # indoklás, csak naplózásra


class Policy(Protocol):
    def decide(self, obs: dict) -> Decision: ...


def _plain(v: Any, depth: int = 0) -> Any:
    """Naplózható, futásról futásra azonos alak (memóriacímet tartalmazó repr nélkül)."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v[:200]
    if depth < 2 and isinstance(v, dict):
        return {str(k)[:50]: _plain(x, depth + 1) for k, x in list(v.items())[:20]}
    if depth < 2 and isinstance(v, (list, tuple)):
        return [_plain(x, depth + 1) for x in v[:20]]
    return f"<{type(v).__name__}>"


def action_to_dict(a: Any) -> dict:
    if type(a) in ACTION_TYPES.values():
        return {"type": type(a).__name__, **{k: _plain(v) for k, v in asdict(a).items()}}
    return {"type": "?", "raw": _plain(a)}


def parse_action(obj: Any) -> Optional[Any]:
    """Dataclass-példányt vagy {"type": ..., mezők} dictet fogad. Hibás alak: None."""
    if type(obj) in ACTION_TYPES.values():
        return obj
    if isinstance(obj, dict):
        cls = ACTION_TYPES.get(obj.get("type"))
        if cls is None:
            return None
        names = [f.name for f in fields(cls)]
        if any(n not in obj for n in names):
            return None
        return cls(**{n: obj[n] for n in names})
    return None
