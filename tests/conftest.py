import pytest


@pytest.fixture(autouse=True)
def _db_for_all(db):
    """Every Takt test runs against the real Postgres test database (eng review D8)."""
