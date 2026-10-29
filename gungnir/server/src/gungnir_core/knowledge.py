"""Knowledge files: where they live and the provenance every entry must carry.

S3 keeps workload class rules, control thresholds and impact thresholds as
TOML files in `knowledge/`; S4 moves them into the knowledge base. A file
with an entry lacking provenance does not load at all, so unsourced
knowledge cannot slip in.
"""

import os
import tomllib
from pathlib import Path

GRADES = ("documented", "measured", "operator", "unverified")


class KnowledgeError(ValueError):
    pass


def directory() -> Path:
    default = Path(__file__).resolve().parents[3] / "knowledge"
    return Path(os.environ.get("GUNGNIR_KNOWLEDGE_DIR", default))


def local_file(host: str) -> Path:
    """Host-local overrides by the operator: `knowledge/local/<host>.toml`,
    one file per host holding every kind of override."""
    return directory() / "local" / f"{host}.toml"


def load(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def entries(path: Path, table: str) -> list[dict]:
    """The entries of an array of tables, each checked for provenance:
    `provenance = { source = "...", grade = "documented" }`."""
    if not path.exists():
        return []
    items = load(path).get(table, [])
    for i, item in enumerate(items, 1):
        where = f"{path}: [[{table}]] #{i}"
        prov = item.get("provenance")
        if not isinstance(prov, dict) or not str(prov.get("source", "")).strip():
            raise KnowledgeError(f"{where} has no provenance source")
        if prov.get("grade") not in GRADES:
            raise KnowledgeError(f"{where}: provenance grade must be one of {', '.join(GRADES)}")
    return items
