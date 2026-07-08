import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SSEEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(self.data)}\n\n"
