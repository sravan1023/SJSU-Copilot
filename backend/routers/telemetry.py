"""
Client timing sink.

The browser batches its own per-message timings (UI/src/services/
telemetryService.ts) and posts them here, and this route only writes them to
the log, one JSON line per event, keyed by the same request_id the server's
"request timings" line carries. There is deliberately no database table: this
is measurement, not analytics, and a write on the chat path's neighbour would
cost more than it tells us.

Unauthenticated, like the rest of the API for now, so the shape is tight:
numbers and short labels only, bounded counts, no free text.
"""
import logging
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("client_timings")

router = APIRouter(tags=["telemetry"])

MAX_EVENTS = 50
MAX_MARKS = 24


class ClientTiming(BaseModel):
    kind: Literal["send", "regenerate", "edit"]
    outcome: Literal["ok", "error", "aborted"]
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    model: str | None = Field(default=None, max_length=16, pattern=r"^[a-z0-9_-]+$")
    # ms since the user pressed send, e.g. {"ack": 3, "first_token": 5012}
    marks: dict[str, float]

    @field_validator("marks")
    @classmethod
    def _bounded_marks(cls, marks: dict[str, float]) -> dict[str, float]:
        if len(marks) > MAX_MARKS:
            raise ValueError(f"at most {MAX_MARKS} marks")
        for name, value in marks.items():
            if not (1 <= len(name) <= 32) or not name.replace("_", "").isalnum():
                raise ValueError("mark names are short identifiers")
            if not (0 <= value <= 3_600_000):
                raise ValueError("mark values are ms within an hour")
        return {name: round(value, 1) for name, value in marks.items()}


class TelemetryBatch(BaseModel):
    events: list[ClientTiming] = Field(min_length=1, max_length=MAX_EVENTS)


@router.post("/telemetry", status_code=204)
async def telemetry(batch: TelemetryBatch) -> Response:
    for event in batch.events:
        logger.info("client timings", extra=event.model_dump())
    return Response(status_code=204)
