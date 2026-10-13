"""Operator identity policy (eng re-review D2) and Gate 1 letters (eng re-review D3).

Every view, htmx partial, print view and the dispute queue renders operator identity
only through operator_display(). Templates use {% operator %} (web/templatetags/operator.py)
and never render operator.name.

    named  - the real name ("Unmapped operator (OP17)" for an unmapped alias)
    letter - "Operator C" ("Unmapped operator C", no hub ID), before Gate 1
    hidden - outside the user's scope or role: never named, never lettered

Who sees what:
    builder (superuser)          named, always
    ops_manager/supervisor/lead  letter before Gate 1, named after; only within role scope
    admin, anonymous, no profile hidden (admins map aliases in the Django admin only)

Page tokens (D3, D12):
- Every full page load mints a random, signed token bound to the user, the minting
  view's name, the page's scope (bounded production dates), the mint time and the
  scope's unmapped aliases at that moment. Letters order the page's operators by
  HMAC(server secret, token nonce, operator key); they are dense (A, B, C...).
- The page passes the token to its partials via hx-vals ({% page_hx_vals %}). A partial
  reuses it only if it was minted by one of the partial's declared parent views and the
  partial's own scope fits inside the token's scope. It then letters from the token's
  scope, so letters match the page even when the partial shows a subset.
- The operator set is fixed at mint time: jobs first ingested later don't reshuffle
  letters, and an alias mapped after the page loaded keeps its letter.
- A token expires at the end of the production day it was minted on plus a short grace
  (TOKEN_GRACE), so nobody can carry one letter assignment across days.
- An htmx request whose token is missing, forged, expired, another user's, from another
  view, or out of scope never gets fresh letters: in letter mode page_context raises
  PageTokenRejected and @operator_bearing answers with HX-Refresh (a full reload, which
  mints a new token). In named mode a fresh context is used (names are stable anyway).

    scope = OperatorScope(area_ids=[area.pk], shift_ids=[shift.pk], date_from=d, date_to=d)
    ctx = page_context(request, scope)                              # full page
    ctx = page_context(request, scope, parents=("web:my_shift",))   # its htmx partial
    return render(request, "web/my_shift.html", {"takt_page": ctx, "rows": rows})
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cached_property

from django.core import signing
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core.models import Job, Operator, OperatorAlias, SiteSettings

from . import scope as role_scope

NAMED = "named"
LETTER = "letter"
HIDDEN = "hidden"

TOKEN_PARAM = "takt_pt"
TOKEN_SALT = "takt.accounts.page_token"
LETTER_HMAC_SALT = "takt.accounts.gate1_letters"
# A token lives until local midnight after the day it was minted, plus this grace, so a
# 3rd-shift page opened at 23:30 still works until the shift ends at 07:00.
TOKEN_GRACE = timedelta(hours=8)

HIDDEN_LABEL = "Operator not in your assignment"
GATE1_NOTICE = "Names hidden until HR approves operator-level views (Gate 1)."


class PageTokenRejected(Exception):
    """An htmx request in letter mode without a usable page token. @operator_bearing
    turns it into an HX-Refresh response; never render fresh letters instead."""


# --------------------------------------------------------------------------- scope


@dataclass(frozen=True)
class OperatorScope:
    """The operator set a page covers: operators (or unmapped aliases) with jobs on lines
    in these areas, on these shifts, between these production dates (inclusive).

    area_ids=None means every area the user may view; shift_ids=None means every shift
    the user may view. page_context() requires both dates.
    """

    area_ids: tuple | None = None
    shift_ids: tuple | None = None
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self):
        for name in ("area_ids", "shift_ids"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, tuple(sorted({int(getattr(x, "pk", x)) for x in v})))

    @property
    def bounded(self):
        return self.date_from is not None and self.date_to is not None and self.date_from <= self.date_to

    def contains(self, other):
        """True when every area, shift and date of `other` is inside this scope."""

        def ids_inside(mine, theirs):
            return mine is None or (theirs is not None and set(theirs) <= set(mine))

        if not (ids_inside(self.area_ids, other.area_ids) and ids_inside(self.shift_ids, other.shift_ids)):
            return False
        if self.date_from is not None and (other.date_from is None or other.date_from < self.date_from):
            return False
        if self.date_to is not None and (other.date_to is None or other.date_to > self.date_to):
            return False
        return True

    def key(self):
        def ids(v):
            return "*" if v is None else ".".join(map(str, v))

        def d(v):
            return v.isoformat() if v else "*"

        return f"a{ids(self.area_ids)}|s{ids(self.shift_ids)}|{d(self.date_from)}|{d(self.date_to)}"

    @classmethod
    def from_key(cls, key):
        a, s, f, t = key.split("|")

        def ids(v):
            v = v[1:]
            return None if v == "*" else tuple(int(x) for x in v.split(".") if x)

        def d(v):
            return None if v == "*" else date.fromisoformat(v)

        return cls(ids(a), ids(s), d(f), d(t))

    def restricted_to(self, user):
        """This scope intersected with what the user may see (a lead: own shift only)."""
        allowed_areas = role_scope.visible_area_ids(user)
        areas = allowed_areas if self.area_ids is None else allowed_areas & set(self.area_ids)
        allowed_shifts = None
        if role_scope.role_of(user) == role_scope.Profile.LEAD:
            own = user.profile.shift_id
            allowed_shifts = {own} if own else set()
        if self.shift_ids is None:
            shifts = allowed_shifts
        else:
            shifts = set(self.shift_ids) if allowed_shifts is None else allowed_shifts & set(self.shift_ids)
        return OperatorScope(tuple(areas), None if shifts is None else tuple(shifts), self.date_from, self.date_to)


# --------------------------------------------------------------------------- tokens


def token_expires_at(minted_at):
    """Local midnight ending the production day the token was minted on, plus TOKEN_GRACE."""
    local = timezone.localtime(minted_at)
    next_midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight + TOKEN_GRACE


def mint_token(user, scope, view, nonce=None, minted_at=None, unmapped=()):
    """A signed page token. Contains a nonce, ids, a view name, dates; never names."""
    payload = {
        "n": nonce or secrets.token_urlsafe(16),
        "u": user.pk,
        "v": view,
        "s": scope.key(),
        "t": (minted_at or timezone.now()).isoformat(),
        "m": sorted(unmapped),
    }
    return signing.dumps(payload, salt=TOKEN_SALT, compress=True)


def read_token(token, user, now=None):
    """The token's payload if it is genuine, unexpired and the user's own; else None."""
    if not token or not getattr(user, "is_authenticated", False):
        return None
    try:
        payload = signing.loads(token, salt=TOKEN_SALT)
        if payload.get("u") != user.pk or not isinstance(payload.get("n"), str) or not payload["n"]:
            return None
        if not isinstance(payload.get("v"), str):
            return None
        OperatorScope.from_key(payload["s"])
        minted_at = datetime.fromisoformat(payload["t"])
        if (now or timezone.now()) > token_expires_at(minted_at):
            return None
        payload["m"] = [int(x) for x in payload.get("m", [])]
    except (signing.BadSignature, KeyError, ValueError, TypeError, AttributeError):
        return None
    return payload


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _view_name(request):
    match = getattr(request, "resolver_match", None)
    return (match.view_name if match else None) or request.path


def page_context(request, scope, parents=None):
    """The identity context for one page or one of its htmx partials.

    Full page load (no HX-Request header): mints a new token for `scope`, bound to this view.
    htmx request: reuses the token sent via hx-vals (GET or POST `takt_pt`) only if it was
    minted by a view named in `parents` (default: this same view) and `scope` fits inside the
    token's scope; letters then come from the token's scope so they match the page. Otherwise,
    in letter mode, raises PageTokenRejected (-> HX-Refresh); in named mode, mints fresh.

    scope must have bounded dates (date_from and date_to).
    """
    if not scope.bounded:
        raise ValueError("page_context needs an OperatorScope with date_from <= date_to (bounded dates)")
    user = request.user
    view = _view_name(request)
    if _is_htmx(request):
        sent = request.GET.get(TOKEN_PARAM) or request.POST.get(TOKEN_PARAM)
        payload = read_token(sent, user)
        allowed_views = set(parents) if parents else {view}
        if payload and payload["v"] in allowed_views:
            token_scope = OperatorScope.from_key(payload["s"])
            if token_scope.contains(scope):
                return PageContext(user, token_scope, payload["n"], datetime.fromisoformat(payload["t"]),
                                   sent, reused=True, frozen_unmapped=frozenset(payload["m"]))
        fresh = _mint(user, scope, view)
        if fresh.mode == LETTER:
            raise PageTokenRejected(view)
        return fresh
    return _mint(user, scope, view)


def _mint(user, scope, view):
    ctx = PageContext(user, scope, secrets.token_urlsafe(16), timezone.now(), "", reused=False)
    ctx.operator_keys  # freezes the unmapped aliases before the token is signed
    ctx.token = mint_token(user, scope, view, ctx.nonce, ctx.minted_at, ctx.frozen_unmapped or ())
    return ctx


# --------------------------------------------------------------------------- display


@dataclass(frozen=True)
class OperatorDisplay:
    mode: str  # NAMED | LETTER | HIDDEN
    label: str
    sort_key: tuple
    unmapped: bool = False
    letter: str | None = None

    @property
    def visible(self):
        return self.mode != HIDDEN

    def __str__(self):
        return self.label


def _letters(index):
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _resolve(obj):
    """(alias_or_None, operator_or_None) for an Operator, OperatorAlias, or anything with .alias."""
    if isinstance(obj, Operator):
        return None, obj
    if not isinstance(obj, OperatorAlias):
        obj = getattr(obj, "alias", None)
    if isinstance(obj, OperatorAlias):
        return obj, (obj.operator if obj.operator_id else None)
    return None, None


@dataclass
class PageContext:
    user: object
    requested_scope: OperatorScope
    nonce: str
    minted_at: datetime
    token: str
    reused: bool = False
    # Alias pks that were unmapped in this scope when the token was minted. They keep their
    # 'a<pk>' key (and letter) for the token's life even if mapped meanwhile.
    frozen_unmapped: frozenset | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @cached_property
    def scope(self):
        """The requested scope intersected with the user's role scope."""
        return self.requested_scope.restricted_to(self.user)

    @cached_property
    def gate1_approved(self):
        return SiteSettings.load().gate1_approved

    @cached_property
    def mode(self):
        """What this user sees for in-scope operators on this page: NAMED, LETTER or HIDDEN."""
        if not role_scope.can_see_operator_metrics(self.user):
            return HIDDEN
        if role_scope.is_builder(self.user) or self.gate1_approved:
            return NAMED
        return LETTER

    @property
    def show_gate1_notice(self):
        return self.mode == LETTER

    @cached_property
    def operator_keys(self):
        """Deterministic operator set of the page's scope, as of the token's mint time.

        'o<operator pk>' for mapped operators (one letter across hubs), 'a<alias pk>' for
        aliases unmapped at mint time. Partials recompute the same set from the token.
        """
        s = self.scope
        if not s.area_ids or (s.shift_ids is not None and not s.shift_ids):
            if self.frozen_unmapped is None:
                self.frozen_unmapped = frozenset()
            return frozenset()
        qs = Job.objects.filter(station__line__area_id__in=s.area_ids, first_ingested_at__lte=self.minted_at)
        if s.shift_ids is not None:
            qs = qs.filter(shift_id__in=s.shift_ids)
        if s.date_from:
            qs = qs.filter(production_date__gte=s.date_from)
        if s.date_to:
            qs = qs.filter(production_date__lte=s.date_to)
        rows = list(qs.values_list("alias_id", "alias__operator_id").distinct())
        if self.frozen_unmapped is None:
            self.frozen_unmapped = frozenset(a for a, o in rows if o is None)
        return frozenset(
            f"a{alias_id}" if (operator_id is None or alias_id in self.frozen_unmapped) else f"o{operator_id}"
            for alias_id, operator_id in rows
        )

    def key_for(self, obj):
        alias, operator = _resolve(obj)
        if alias is not None and (alias.operator_id is None or alias.pk in (self.frozen_unmapped or ())):
            return f"a{alias.pk}"
        if operator is not None:
            return f"o{operator.pk}"
        return None

    @cached_property
    def letters(self):
        """key -> letter, ordered by HMAC(server secret, token nonce, key). Dense: A, B, C..."""
        ordered = sorted(
            self.operator_keys,
            key=lambda k: (salted_hmac(LETTER_HMAC_SALT, f"{self.nonce}:{k}", algorithm="sha256").hexdigest(), k),
        )
        return {k: _letters(i) for i, k in enumerate(ordered)}

    def display(self, obj):
        return operator_display(self.user, obj, self)

    def sort_key(self, obj):
        """Row order: real name when named, letter when lettered (never name order in letter mode)."""
        return self.display(obj).sort_key

    def sorted(self, rows, key=lambda r: r, drop_hidden=True):
        decorated = [(self.display(key(r)), r) for r in rows]
        if drop_hidden:
            decorated = [(d, r) for d, r in decorated if d.visible]
        return [r for d, r in sorted(decorated, key=lambda dr: dr[0].sort_key)]

    @property
    def hx_vals(self):
        """JSON for an hx-vals attribute. Put it on a wrapper so every partial inherits it."""
        return json.dumps({TOKEN_PARAM: self.token})


_HIDDEN = OperatorDisplay(HIDDEN, HIDDEN_LABEL, (9, 0, ""))


def operator_display(user, operator_or_alias, context):
    """The single identity decision: named, letter or hidden (eng re-review D2).

    user: the requesting user; context: the PageContext from page_context(). An object
    outside the page's (role-restricted) operator set is hidden. No context = hidden.
    """
    if context is None or user is None or getattr(user, "pk", None) != getattr(context.user, "pk", None):
        return _HIDDEN
    if context.mode == HIDDEN:
        return _HIDDEN
    key = context.key_for(operator_or_alias)
    if key is None or key not in context.operator_keys:
        return _HIDDEN
    alias, operator = _resolve(operator_or_alias)
    if context.mode == NAMED:
        if operator is None:
            return OperatorDisplay(NAMED, f"Unmapped operator ({alias.hub_operator_id})",
                                   (1, 0, alias.hub_operator_id.casefold()), unmapped=True)
        return OperatorDisplay(NAMED, operator.name, (0, 0, operator.name.casefold(), operator.pk))
    letter = context.letters[key]
    unmapped = key.startswith("a")
    label = f"Unmapped operator {letter}" if unmapped else f"Operator {letter}"
    return OperatorDisplay(LETTER, label, (0, len(letter), letter), unmapped=unmapped, letter=letter)
