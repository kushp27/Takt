"""Cores Hub adapter: NOT IMPLEMENTED (stub).

We have no access to the real Cores Hub yet (design Open Question 1: database
engine, transaction table, stable transaction ID, last-modified column, operator
ID space, whether quantity is good or gross). Do not guess table or column names.

To implement: subclass HubAdapter, open a read-only connection using a service
account, set the 30 s statement timeout, page in BATCH_SIZE rows ordered by the
selection column then MES ID, and raise MesUnavailable on any connection or
query error. Then add a test against an anonymized extract loaded into a
throwaway container of the hub's own engine (eng review D8).
"""

from .base import HubAdapter


class CoresHubAdapter(HubAdapter):
    def _not_ready(self):
        raise NotImplementedError(
            "Cores Hub adapter is a stub until Open Question 1 is answered. "
            "Set this hub's adapter to 'fixture' in the admin for development."
        )

    def iter_jobs_since(self, since, batch_size=None):
        self._not_ready()

    def fetch_jobs_by_ids(self, mes_ids, batch_size=None):
        self._not_ready()

    def iter_scrap_since(self, since, batch_size=None):
        self._not_ready()
