from .base import CaeAdapter, CaseOutcome
from .data import DataAdapter
from .mock import MockAdapter
from .script import ScriptAdapter

REGISTRY: dict[str, CaeAdapter] = {a.name: a for a in (MockAdapter(), ScriptAdapter(), DataAdapter())}


def get_adapter(name: str) -> CaeAdapter:
    if name not in REGISTRY:
        raise KeyError(f"알 수 없는 어댑터: {name} (사용 가능: {', '.join(REGISTRY)})")
    return REGISTRY[name]
