"""Load the fixed fact file."""

import json
import os
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "store.json"


class Fact(BaseModel):
    id: str
    topic: str
    text: str
    # other words a customer may use, only for search
    keywords: str = ""


def load_facts(path: str | Path | None = None) -> list[Fact]:
    path = Path(path or os.environ.get("FACTS_PATH") or DEFAULT_PATH)
    with path.open(encoding="utf-8") as f:
        return [Fact(**item) for item in json.load(f)]
