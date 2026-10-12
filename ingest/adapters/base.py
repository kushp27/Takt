"""The HubAdapter interface: the only code that knows how a hub stores transactions.

Ingest talks to Cores Hub and Dies Hub only through this interface. Adapters are
read-only: Takt never writes to the MES (design Constraints).

Timestamps come back exactly as the MES stores them. If they are naive local
plant times, ingest localizes them with Hub.plant_timezone (eng review D2).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Sequence

BATCH_SIZE = 5000  # eng review D9: 5,000-row batches
STATEMENT_TIMEOUT_SECONDS = 30  # eng review D9: 30 s statement timeout


class MesUnavailable(Exception):
    """The hub could not be reached or a query failed/timed out. Ingest commits nothing for the run."""


@dataclass(frozen=True)
class MesJobRow:
    mes_id: str
    station_code: str
    part_code: str
    operator_id: str
    started_at: datetime
    completed_at: datetime | None
    quantity: int


@dataclass(frozen=True)
class MesScrapRow:
    mes_id: str
    occurred_at: datetime
    station_code: str
    part_code: str
    scrap_code: str
    quantity: int


class HubAdapter:
    """Subclasses implement the three read methods. All must be bounded (D9)."""

    #: True if the hub exposes a last-modified column; otherwise selection uses start time.
    has_last_modified = False

    def __init__(self, hub):
        self.hub = hub

    def iter_jobs_since(self, since: datetime, batch_size: int = BATCH_SIZE) -> Iterator[list[MesJobRow]]:
        """Yield batches of jobs modified (or, without a last-modified column, started) at or after `since`."""
        raise NotImplementedError

    def fetch_jobs_by_ids(self, mes_ids: Sequence[str], batch_size: int = BATCH_SIZE) -> Iterator[list[MesJobRow]]:
        """Yield batches of the given jobs by MES ID (re-read of still-open jobs, eng review D3)."""
        raise NotImplementedError

    def iter_scrap_since(self, since: datetime, batch_size: int = BATCH_SIZE) -> Iterator[list[MesScrapRow]]:
        """Yield batches of scrap events at or after `since`."""
        raise NotImplementedError
