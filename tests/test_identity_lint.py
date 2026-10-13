"""E-T1 lint: templates never render an operator's name, and views never put raw names in context.

Static complement to the runtime sweep (tests/test_identity_sweep.py), which also
checks every rendered response and its template context for fixture names.
Use {% operator %} / {% operator_display %} (web/templatetags/operator.py) instead.
"""

import ast
import re
from pathlib import Path

from django.conf import settings
from django.template import engines

BASE = Path(settings.BASE_DIR)

# Operator-ish owners: long words anywhere in an identifier, "op"/"ops" only as a whole word.
_LONG = r"(?:operator|alias|person|worker|employee)"
_SHORT = r"(?<![A-Za-z])ops?(?![A-Za-z])"
_OWNER = rf"(?:{_LONG}|{_SHORT})"

# A template variable path that reaches an operator-ish object and then .name, or any hub ID.
TEMPLATE_BAD = re.compile(
    rf"(?:\w*{_LONG}\w*|{_SHORT})(?:\.\w+)*\.(?:name|hub_operator_id)\b"
    rf"|\bhub_operator_id\b|{_LONG}\w*__\w*name\b",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"{{.*?}}|{%.*?%}", re.DOTALL)
# Bare output of an operator or alias object ({{ x.operator }}, {{ alias|upper }}): its
# str() is "Operator #12" / "cores:ZQ-101", which bypasses the policy.
BARE_OUTPUT = re.compile(r"^\{\{\s*(?:[\w.]+\.)?\w*" + _LONG + r"(?:e?s)?\s*(?:\|.*)?\}\}$", re.DOTALL | re.IGNORECASE)

# Python in the view layer: these may touch names (the policy itself and its tags).
VIEW_ALLOW = {"accounts/policy.py", "web/templatetags/operator.py"}
PY_LOOKUP_BAD = re.compile(r"(?:operator|alias)\w*__(?:\w+__)*name\b", re.IGNORECASE)


def template_files():
    dirs = set()
    for engine in engines.all():
        dirs.update(Path(d) for d in getattr(engine, "template_dirs", []))
    # Belt and braces: any templates/ directory in the repo, whatever the settings say.
    dirs.update(p for p in BASE.glob("*/templates") if p.is_dir())
    files = set()
    for d in dirs:
        if BASE in d.resolve().parents:
            files.update(p for p in d.rglob("*") if p.suffix in (".html", ".txt", ".xml", ".svg"))
    return sorted(files)


def template_violations(text):
    out = []
    for tag in TAG_RE.findall(text):
        if tag.startswith("{% comment") or tag.startswith("{#"):
            continue
        for m in TEMPLATE_BAD.finditer(tag):
            out.append(f"{tag.strip()!r} ({m.group(0)})")
        if BARE_OUTPUT.match(tag.strip()):
            out.append(f"{tag.strip()!r} (bare operator/alias output)")
    return out


def view_layer_files():
    for p in sorted(BASE.rglob("*.py")):
        rel = p.relative_to(BASE).as_posix()
        if rel.startswith(("tests/", ".")) or "/migrations/" in rel or "site-packages" in rel:
            continue
        if rel in VIEW_ALLOW:
            continue
        name = p.name
        if (name in ("views.py", "urls.py", "forms.py", "context_processors.py")
                or "/views/" in rel or "/templatetags/" in rel or rel.startswith("web/")):
            yield rel, p


def python_violations(source, filename="<src>"):
    out = []
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "name":
            owner = ast.unparse(node.value)
            if re.search(_OWNER, owner, re.IGNORECASE):
                out.append(f"{filename}:{node.lineno} {owner}.name")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and PY_LOOKUP_BAD.search(node.value):
            out.append(f"{filename}:{node.lineno} lookup {node.value!r}")
    return out


def test_templates_never_render_operator_names():
    files = template_files()
    assert files, "no templates found: the lint is looking in the wrong place"
    bad = []
    for p in files:
        for v in template_violations(p.read_text(errors="replace")):
            bad.append(f"{p.relative_to(BASE)}: {v}")
    assert not bad, "Render operators with {% operator %}, never .name:\n" + "\n".join(bad)


def test_views_never_pass_raw_operator_names():
    bad = []
    for rel, p in view_layer_files():
        bad += python_violations(p.read_text(), rel)
    assert not bad, "Views must not read operator names; pass objects and use the policy:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- lint self-tests


def test_template_lint_catches_known_shapes():
    for src in ["{{ operator.name }}", "{{ row.alias.operator.name }}", "{{ job.alias.operator.name|upper }}",
                "{% if op.name %}", "{{ a.hub_operator_id }}", "{% for n in operators.name %}",
                "{{ worker.name }}", '{% sort rows "alias__operator__name" %}', "{{ ops.0.name }}",
                "{{ row.operator }}", "{{ job.alias }}", "{{ operator }}", "{{ row.alias|default:'-' }}",
                "{{ x.operator|upper }}"]:
        assert template_violations(src), src


def test_template_lint_allows_station_and_line_names():
    for src in ["{{ station.name }}", "{{ line.name }}", "{% operator row.alias %}", "{{ area.name }}",
                "{{ part.family.name }}", "{{ options.name }}", "{{ shop.name }}", "{{ row.station }}",
                "{{ row.operator_count }}",
                "{% comment %}operator.name is banned{% endcomment %}"]:
        assert not template_violations(src), src


def test_python_lint_catches_known_shapes():
    src = """
def v(request, job):
    a = job.alias.operator.name
    b = op.name
    rows = Job.objects.values("alias__operator__name")
    c = F("alias__operator__name")
    return {"n": operator.name}
"""
    assert len(python_violations(src)) == 5


def test_python_lint_allows_other_names():
    src = """
def v(request, station):
    shop = options = station
    return {"s": station.name, "l": station.line.name, "x": shop.name, "o": options.name}
"""
    assert python_violations(src) == []
