"""Operator identity template tags (eng re-review D2/D3). The only way templates show an operator.

    {% load operator %}
    <div {% page_hx_vals %}>                       {# every htmx request inside sends the page token #}
      {% gate1_notice %}                           {# D12 notice, only when this user sees letters #}
      {% operators_sorted rows "alias" as rows %}  {# drops hidden rows; name order or letter order #}
      {% for row in rows %}<td>{% operator row.alias %}</td>{% endfor %}
      {% operator_display job.alias as who %}{% if who.unmapped %}…{% endif %}
    </div>

Every tag reads the PageContext from the template variable `takt_page`
(accounts.policy.page_context). Without it, operators render hidden.
"""

import logging

from django import template
from django.utils.html import format_html

from accounts import policy

register = template.Library()
log = logging.getLogger(__name__)


def _page(context):
    page = context.get("takt_page")
    if page is None:
        log.warning("{%% operator %%} used without takt_page in the template context; rendering hidden")
    return page


def _user(context, page):
    request = context.get("request")
    if request is not None:
        return request.user
    return page.user if page is not None else None


@register.simple_tag(takes_context=True)
def operator(context, operator_or_alias):
    """The operator's label as the policy allows: a name, 'Operator C', or the hidden label."""
    page = _page(context)
    return policy.operator_display(_user(context, page), operator_or_alias, page).label


@register.simple_tag(takes_context=True)
def operator_display(context, operator_or_alias):
    """{% operator_display obj as who %}: who.mode, who.label, who.visible, who.unmapped, who.sort_key."""
    page = _page(context)
    return policy.operator_display(_user(context, page), operator_or_alias, page)


@register.simple_tag(takes_context=True)
def operators_sorted(context, rows, attr=None, drop_hidden=True):
    """Rows ordered by the policy's sort key (name when named, letter when lettered).

    attr: dotted path from each row to its Operator/OperatorAlias, e.g. "alias" or "job.alias".
    Hidden rows are dropped unless drop_hidden=False.
    """
    page = _page(context)
    if page is None:
        return []

    def get(row):
        for part in (attr or "").split("."):
            if part:
                row = row[part] if isinstance(row, dict) else getattr(row, part)
        return row

    return page.sorted(list(rows), key=get, drop_hidden=drop_hidden)


@register.simple_tag(takes_context=True)
def operator_ref(context, operator_or_alias):
    """{% operator_ref row.alias as ref %}: the opaque per-page reference for URLs, values and ids.
    Never put an alias or operator pk in HTML (D12)."""
    page = _page(context)
    return page.ref(operator_or_alias) if page is not None else ""


@register.inclusion_tag("partials/gate1_notice.html", takes_context=True)
def gate1_notice(context):
    """D12: the persistent notice above any anonymized table. Renders nothing in named mode."""
    page = context.get("takt_page")
    return {"show": bool(page and page.show_gate1_notice), "text": policy.GATE1_NOTICE}


@register.simple_tag(takes_context=True)
def page_hx_vals(context):
    """An hx-vals attribute carrying the page token. htmx inherits it from ancestors."""
    page = context.get("takt_page")
    if page is None:
        return ""
    return format_html('hx-vals="{}"', page.hx_vals)
