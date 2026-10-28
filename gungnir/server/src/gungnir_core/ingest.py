"""Wire format of a telemetry batch sent by an agent.

A batch carries samples in long form, one row per (time, signal, entity), so a
new signal needs no change here or in the schema. Every batch also repeats the
agent's probe results, so the core learns them even after its own restart.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Signal names are `resource.quantity_unit`, e.g. `disk.read_await_ms`.
SignalName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=64)]

MAX_SAMPLES = 10_000


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Sample(Strict):
    ts: Annotated[int, Field(ge=0, description="milliseconds since the Unix epoch")]
    signal: SignalName
    entity: Annotated[str, Field(min_length=1, max_length=255)]
    value: Annotated[float, Field(allow_inf_nan=False)]


class SourceStatus(Strict):
    module: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]
    state: Literal["preferred", "fallback", "disabled"]
    origin: Annotated[str | None, Field(max_length=255)] = None
    reason: Annotated[str | None, Field(max_length=2000)] = None


class AgentInfo(Strict):
    version: Annotated[str, Field(max_length=32)]
    started_at: Annotated[int, Field(ge=0)]
    # Optional so that an agent older than the field is still accepted.
    utc_offset_s: Annotated[
        int | None, Field(ge=-12 * 3600, le=14 * 3600, description="host's local time minus UTC")
    ] = None


class Batch(Strict):
    agent: AgentInfo
    seq: Annotated[int, Field(ge=1)]
    sources: Annotated[list[SourceStatus], Field(max_length=64)]
    dropped: Annotated[int, Field(ge=0)]
    samples: Annotated[list[Sample], Field(max_length=MAX_SAMPLES)]
