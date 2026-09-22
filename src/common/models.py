from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ContractSpec:
    contract_type: str
    contract: object
    metadata: Dict[str, str] = field(default_factory=dict)
