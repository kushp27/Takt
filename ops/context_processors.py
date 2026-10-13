from django.utils.functional import SimpleLazyObject

from . import freshness as _freshness


def freshness(request):
    """Every page gets `freshness`: a lazy ops.freshness.Snapshot for all hubs.

    Lazy, so pages that never touch it (and the anonymous login page) issue no queries.
    Computed at most once per request.
    """
    return {"freshness": SimpleLazyObject(_freshness.snapshot)}
