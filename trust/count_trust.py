"""Count trust (design Component 4a): automatic revocation.

Revoked automatically when confirmed count disputes exceed 5% of a station's jobs over a
rolling 2 weeks (SiteSettings.count_dispute_revoke_share / _days). A "count dispute" is a
dispute confirmed with affects_counts=True (the count itself was wrong). Rejected and open
disputes never count. Both sides of the ratio use the job's production date, and a job with
several confirmed count disputes counts once.

Re-establishing trust is manual (a fresh 20-job physical sample, then the plant admin sets the
flag with evidence); this module never sets the flag.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone as djtz

from core.models import CountTrustChange, Job, SiteSettings, Station
from core.shifts import local_date
from disputes.models import Dispute


@dataclass
class RevocationCheck:
    station: Station
    jobs: int
    confirmed_count_disputes: int
    share: float
    revoke: bool


def check_station(station, as_of=None, settings=None):
    s = settings or SiteSettings.load()
    as_of_d = local_date(station.line.hub, as_of or djtz.now())
    date_from = as_of_d - timedelta(days=s.count_dispute_revoke_days - 1)
    jobs = Job.objects.filter(station=station, completed_at__isnull=False,
                              production_date__range=(date_from, as_of_d))
    agg = jobs.aggregate(
        n=Count("id", distinct=True),
        bad=Count("id", distinct=True, filter=Q(disputes__status=Dispute.CONFIRMED,
                                                disputes__affects_counts=True)),
    )
    n, bad = agg["n"], agg["bad"]
    share = bad / n if n else 0.0
    return RevocationCheck(station, n, bad, share, bool(n) and share > s.count_dispute_revoke_share)


@transaction.atomic
def auto_revoke(stations=None, as_of=None, settings=None):
    """Revoke count trust where the rule fires. Writes a CountTrustChange (auto_revoke) with the
    evidence. Returns the RevocationChecks that revoked."""
    s = settings or SiteSettings.load()
    qs = stations if stations is not None else Station.objects.filter(count_trusted=True)
    revoked = []
    for st in qs.select_related("line__hub") if hasattr(qs, "select_related") else qs:
        if not st.count_trusted:
            continue
        chk = check_station(st, as_of, s)
        if not chk.revoke:
            continue
        evidence = (f"Automatic: {chk.confirmed_count_disputes} confirmed count disputes on {chk.jobs} jobs "
                    f"({chk.share:.1%}) over the last {s.count_dispute_revoke_days} days, above "
                    f"{s.count_dispute_revoke_share:.0%}. Re-establish with a fresh 20-job physical sample.")
        st.count_trusted = False
        st.count_trust_set_by = None
        st.count_trust_set_at = djtz.now()
        st.count_trust_evidence = evidence
        st.save(update_fields=["count_trusted", "count_trust_set_by", "count_trust_set_at", "count_trust_evidence"])
        CountTrustChange.objects.create(station=st, action=CountTrustChange.AUTO_REVOKE, evidence=evidence)
        revoked.append(chk)
    return revoked


__all__ = ["check_station", "auto_revoke", "RevocationCheck"]
