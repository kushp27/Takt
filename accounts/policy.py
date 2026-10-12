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

Page tokens (D3): every full page load mints a random token. Letters order the
operators of the page's scope by HMAC(server secret, token, operator key). The page
passes the token to its htmx partials via hx-vals ({% page_hx_vals %}); a partial with
a valid token reuses the token's scope, so its letters match the page even when it
renders only a subset. The operator set is fixed by the token's mint time (jobs first
ingested after the page loaded don't reshuffle letters). A missing, forged, expired or
other-user token mints a fresh one: letters may differ, names never leak.

    scope = OperatorScope(area_ids=[area.pk], shift_ids=[shift.pk], date_from=d, date_to=d)
    ctx = page_context(request, scope)          # full page or partial, same call
    rows.sort(key=lambda r: ctx.sort_key(r.alias))
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
# Long enough for a live My Shift page left open for a whole 12-hour shift.
TOKEN_MAX_AGE = timedelta(hours=16)

HIDDEN_LABEL = "Operator not in your assignment"
GATE1_NOTICE = "Names hidden until HR approves operator-level views (Gate 1)."


# --------------------------------------------------------------------------- scope


@dataclass(frozen=True)
class OperatorScope:
    """The operator set a page covers: operators (or unmapped aliases) with jobs on lines
    in these areas, in this production-date range, on these shifts.

    area_ids=None means every area the user may view; shift_ids=None means every shift
    the user may view; a None date bound is open-ended.
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


def mint_token(user, scope, nonce=None, minted_at=None):
    """A signed page token: random nonce + user + scope + mint time. Never contains names."""
    payload = {
        "n": nonce or secrets.token_urlsafe(16),
        "u": user.pk,
        "s": scope.key(),
        "t": (minted_at or timezone.now()).isoformat(),
    }
    return signing.dumps(payload, salt=TOKEN_SALT, compress=True)


def read_token(token, user):
    """The token's payload if it is genuine, unexpired and the user's own; else None."""
    if not token or not getattr(user, "is_authenticated", False):
        return None
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        if payload.get("u") != user.pk:
            return None
        OperatorScope.from_key(payload["s"])
        datetime.fromisoformat(payload["t"])
        if not isinstance(payload.get("n"), str) or not payload["n"]:
            return None
    except (signing.BadSignature, KeyError, ValueError, TypeError, AttributeError):
        return None
    return payload


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def page_context(request, scope):
    """The identity context for one page and its htmx partials.

    Full page load (no HX-Request header): always mints a new token for `scope`.
    htmx partial: reuses the token sent via hx-vals (GET or POST `takt_pt`) and THAT
    token's scope, so letters match the parent page; `scope` is only the fallback used
    when the token is missing or invalid.
    """
    user = request.user
    payload = None
    if _is_htmx(request):
        payload = read_token(request.GET.get(TOKEN_PARAM) or request.POST.get(TOKEN_PARAM), user)
    if payload is None:
        minted_at = timezone.now()
        nonce = secrets.token_urlsafe(16)
        token = mint_token(user, scope, nonce=nonce, minted_at=minted_at)
        return PageContext(user, scope, nonce, minted_at, token, reused=False)
    return PageContext(
        user,
        OperatorScope.from_key(payload["s"]),
        payload["n"],
        datetime.fromisoformat(payload["t"]),
        request.GET.get(TOKEN_PARAM) or request.POST.get(TOKEN_PARAM),
        reused=True,
    )


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
    """(key, alias_or_None, operator_or_None) for an Operator, OperatorAlias, or anything with .alias."""
    if isinstance(obj, Operator):
        return f"o{obj.pk}", None, obj
    if not isinstance(obj, OperatorAlias):
        obj = getattr(obj, "alias", None)
    if isinstance(obj, OperatorAlias):
        if obj.operator_id:
            return f"o{obj.operator_id}", obj, obj.operator
        return f"a{obj.pk}", obj, None
    return None, None, None


@dataclass
class PageContext:
    user: object
    requested_scope: OperatorScope
    nonce: str
    minted_at: datetime
    token: str
    reused: bool = False
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
        unmapped aliases. Partials recompute the same set from the token.
        """
        s = self.scope
        if not s.area_ids or (s.shift_ids is not None and not s.shift_ids):
            return frozenset()
        qs = Job.objects.filter(station__line__area_id__in=s.area_ids, first_ingested_at__lte=self.minted_at)
        if s.shift_ids is not None:
            qs = qs.filter(shift_id__in=s.shift_ids)
        if s.date_from:
            qs = qs.filter(production_date__gte=s.date_from)
        if s.date_to:
            qs = qs.filter(production_date__lte=s.date_to)
        keys = set()
        for alias_id, operator_id in qs.values_list("alias_id", "alias__operator_id").distinct():
            keys.add(f"o{operator_id}" if operator_id else f"a{alias_id}")
        return frozenset(keys)

    @cached_property
    def letters(self):
        """key -> letter, ordered by HMAC(server secret, token nonce, key)."""
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
    key, alias, operator = _resolve(operator_or_alias)
    if key is None or key not in context.operator_keys:
        return _HIDDEN
    unmapped = operator is None
    if context.mode == NAMED:
        if unmapped:
            return OperatorDisplay(NAMED, f"Unmapped operator ({alias.hub_operator_id})",
                                   (1, 0, alias.hub_operator_id.casefold()), unmapped=True)
        return OperatorDisplay(NAMED, operator.name, (0, 0, operator.name.casefold(), operator.pk))
    letter = context.letters[key]
    label = f"Unmapped operator {letter}" if unmapped else f"Operator {letter}"
    return OperatorDisplay(LETTER, label, (0, len(letter), letter), unmapped=unmapped, letter=letter)
