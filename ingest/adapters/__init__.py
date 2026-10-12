from .base import HubAdapter, MesJobRow, MesScrapRow, MesUnavailable  # noqa: F401


def get_adapter(hub):
    """Return the HubAdapter configured for this hub in the admin."""
    from core.models import Hub

    if hub.adapter == Hub.ADAPTER_FIXTURE:
        from .fixture import FixtureAdapter

        return FixtureAdapter(hub)
    if hub.adapter == Hub.ADAPTER_CORES:
        from .cores import CoresHubAdapter

        return CoresHubAdapter(hub)
    if hub.adapter == Hub.ADAPTER_DIES:
        from .dies import DiesHubAdapter

        return DiesHubAdapter(hub)
    raise ValueError(f"Unknown adapter {hub.adapter!r}")
