"""The knowledge base as one document for reading in the interface (slice
S7): every individual of the ontology with its label, comment and
provenance, and the rules, policy, expected behavior, workload classes,
detection limits and knowledge tests that refer to them.

Built once from the base the server loaded; the base changes only with a
restart.
"""

from dataclasses import asdict
from pathlib import Path

from rdflib import RDF, RDFS, Literal

from gungnir_server import cases, knowledge, ontology, workload
from gungnir_server.base import Base
from gungnir_server.detection.detectors import load_limits
from gungnir_server.ontology import BASE, CATEGORIES, G
from gungnir_server.rules import Rule


def _text(graph, node, prop, lang: str | None = None) -> str | None:
    for value in graph.objects(node, prop):
        if isinstance(value, Literal) and (lang is None or value.language == lang):
            return str(value)
    return None


def annotations(file: Path | None = None) -> dict[str, dict[str, dict]]:
    """Category -> name -> label, comment, source and grade."""
    graph = ontology.parse(file or ontology.path())
    out: dict[str, dict[str, dict]] = {}
    for cat, cls in CATEGORIES.items():
        ns = f"{BASE}/{cat}#"
        found = {}
        for node in graph.subjects(RDF.type, G[cls]):
            name = str(node)[len(ns) :]
            entry = {
                "label": _text(graph, node, RDFS.label, "en"),
                "comment": _text(graph, node, RDFS.comment, "en"),
                "source": _text(graph, node, G.source),
                "grade": _text(graph, node, G.grade),
            }
            found[name] = entry
        out[cat] = found
    return out


def _domain(where: str) -> str:
    """The rule file's stem: disk, memory, cpu, cross."""
    return Path(where.split(":", 1)[0]).stem


def _rule(rule: Rule) -> dict:
    return {
        "cause": rule.cause,
        "file": _domain(rule.where),
        "source": rule.source,
        "grade": rule.grade,
        "requires": {k: sorted(v) for k, v in rule.requires.items()},
        "excludes": {k: sorted(v) for k, v in rule.excludes.items()},
        "symptoms": [
            {
                "kind": m.kind,
                "names": list(m.names),
                "role": m.role,
                "strength": m.strength,
                "leading": m.leading,
                "impact": m.impact,
                "window": list(m.window) if m.window else None,
                "direction": m.direction,
                "record": m.record,
                "level": m.level,
                "detectors": list(m.detectors),
                "at_most": m.at_most,
                "above": m.above,
                "source": m.source,
                "grade": m.grade,
            }
            for m in rule.symptoms
        ],
    }


def document(base: Base, host: str | None = None) -> dict:
    onto = base.ontology
    notes = annotations()

    def described(cat: str, name: str, **fields) -> dict:
        return {"name": name, **notes[cat].get(name, {}), **fields}

    pol = base.policy
    return {
        "resources": [described("resource", n) for n in sorted(onto.resources)],
        "entity_types": [described("entity", n) for n in sorted(onto.entity_types)],
        "roles": [
            described("role", r.name, entity_types=sorted(r.entity_types)) for r in onto.roles.values()
        ],
        "signals": [
            described(
                "signal",
                s.name,
                resource=s.resource,
                entity=s.entity,
                unit=s.unit,
                kind=s.kind,
                impact=s.impact,
                reflects=sorted(s.reflects),
            )
            for s in onto.signals.values()
        ],
        "events": [
            described(
                "event",
                e.name,
                resource=e.resource,
                emitted_by=e.emitted_by,
                message_id=e.message_id,
                pattern=e.pattern.pattern if e.pattern else None,
            )
            for e in onto.events.values()
        ],
        "processes": [
            described("process", p.name, resource=p.resource, pattern=p.pattern, state=p.state)
            for p in onto.process_symptoms.values()
        ],
        "facts": [
            described("fact", f.name, resource=f.resource, entity=f.entity, values=sorted(f.values))
            for f in onto.facts.values()
        ],
        "classes": [described("class", n) for n in sorted(onto.classes)],
        "causes": [
            described(
                "cause",
                c.name,
                resources=sorted(c.resources),
                culprit=c.culprit,
                domain=_domain(base.rules[c.name].where) if c.name in base.rules else None,
            )
            for c in sorted(onto.causes.values(), key=lambda c: c.name)
        ],
        "actions": [
            described("action", a.name, target=a.target, resource=a.resource, proportional=a.proportional)
            for a in onto.actions.values()
        ],
        "protected": [
            described("protected", p.name, units=list(p.units), commands=list(p.commands))
            for p in onto.protected.values()
        ],
        "rules": [_rule(r) for r in base.rules.values()],
        "policy": [
            {
                "cause": e.cause,
                "action": e.action,
                "target": e.target,
                "when": e.when,
                "signal": e.signal,
                "share": e.share,
                "effectiveness": e.effectiveness,
                "side_effects": [asdict(s) for s in e.side_effects],
                "requires": {k: sorted(v) for k, v in e.requires.items()},
                "excludes": {k: sorted(v) for k, v in e.excludes.items()},
                "source": e.source,
            }
            for e in pol.entries
        ],
        "advisories": [asdict(a) for a in pol.advisories],
        "contraindications": [
            {"cause": cause, "actions": sorted(actions)}
            for cause, actions in sorted(pol.contraindicated.items())
        ],
        "expected": [
            {
                "class": e.cls,
                "signals": list(e.signals),
                "direction": e.direction,
                "members": e.members,
                "source": e.source,
            }
            for e in base.expected
        ],
        "workload_rules": [
            {
                "class": r.cls,
                "priority": r.priority,
                "command": list(r.command),
                "unit": list(r.unit),
                "group": list(r.group),
                "source": r.source,
            }
            for r in workload.load_rules(host)
        ],
        "limits": [
            {
                "kind": kind,
                "signal": lim.signal,
                "above": lim.above,
                "below": lim.below,
                "level": lim.level,
                "source": lim.source,
            }
            for kind, table, file in (
                ("threshold", "threshold", "detection/static_thresholds.toml"),
                ("impact", "impact", "detection/impact.toml"),
            )
            for lim in load_limits(table, file, host)
        ],
        "tests": [
            {
                "name": c.name,
                "domain": c.domain,
                "expect": c.expect,
                "within": c.within,
                "harm": c.harm,
                "not": list(c.not_),
                "expected": c.expected,
                "facts": c.facts,
                "culprit_class": c.culprit_class,
                "items": [asdict(i) for i in c.items],
            }
            for c in cases.load(onto, base.rules)
        ],
        "grades": list(knowledge.GRADES),
    }
