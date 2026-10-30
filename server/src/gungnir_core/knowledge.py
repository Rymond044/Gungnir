"""Knowledge files: where they live and the provenance every entry must carry.

The layers of the base are directories of `knowledge/` (ADR 0033):
`ontology/`, `rules/`, `expected/`, `policy/`, `detection/`, `tests/`, and
`local/` for the operator's host-local entries. A file with an entry
lacking provenance does not load at all, so unsourced knowledge cannot
slip in.
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
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except OSError as err:
        raise KnowledgeError(f"cannot read knowledge file {path}: {err.strerror}") from err


def entries(path: Path, table: str, required: bool = True) -> list[dict]:
    """The entries of an array of tables, each checked for provenance:
    `provenance = { source = "...", grade = "documented" }`. A shipped file
    that cannot be read is an error, never an empty rule set; an optional
    file (the host-local one) may be absent."""
    if not required:
        try:
            path.stat()
        except FileNotFoundError:
            return []
        except OSError as err:
            raise KnowledgeError(f"cannot read knowledge file {path}: {err.strerror}") from err
    items = load(path).get(table, [])
    for i, item in enumerate(items, 1):
        problem = provenance_problem(item, f"{path}: [[{table}]] #{i}")
        if problem:
            raise KnowledgeError(problem)
    return items


def provenance_problem(item: dict, where: str) -> str | None:
    """The complaint about an entry's provenance, or None."""
    prov = item.get("provenance")
    if not isinstance(prov, dict) or not str(prov.get("source", "")).strip():
        return f"{where} has no provenance source"
    if prov.get("grade") not in GRADES:
        return f"{where}: provenance grade must be one of {', '.join(GRADES)}"
    return None
