"""seed_demo and Lane B's seed_demo_users both touch "builder"; order must not lock it out."""

from django.contrib.auth import get_user_model
from django.core.management import call_command

from accounts.management.commands.seed_demo_users import DEV_PASSWORD


def test_seed_demo_then_seed_demo_users_builder_can_log_in(client, monkeypatch):
    monkeypatch.delenv("TAKT_DEMO_PASSWORD", raising=False)
    call_command("seed_demo", end="2026-10-12T06:00", days=2, verbosity=0)
    builder = get_user_model().objects.get(username="builder")
    assert not builder.has_usable_password()  # seed_demo never sets a password
    call_command("seed_demo_users", verbosity=0)
    assert client.login(username="builder", password=DEV_PASSWORD)


def test_seed_demo_users_keeps_an_existing_usable_password(client, monkeypatch):
    monkeypatch.delenv("TAKT_DEMO_PASSWORD", raising=False)
    User = get_user_model()
    u = User.objects.create_user("builder", password="their-own-pw-123", is_superuser=True, is_staff=True)
    call_command("seed_demo", end="2026-10-12T06:00", days=2, verbosity=0)
    call_command("seed_demo_users", verbosity=0)
    u.refresh_from_db()
    assert u.check_password("their-own-pw-123")
