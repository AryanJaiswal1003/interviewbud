import os

# Tests never touch the dev database: they run against interviewbud_test on the same server. Set before any app
# module is imported (app.config caches settings; app.db builds the engine at import).
_dev = os.environ.get("DATABASE_URL", "postgresql+asyncpg://interviewbud:interviewbud@localhost:5544/interviewbud")
os.environ["DATABASE_URL"] = _dev.rsplit("/", 1)[0] + "/interviewbud_test"
os.environ.setdefault("DB_NULLPOOL", "1")  # see app/db.py


def pytest_configure(config):
    from app.config import get_settings

    url = get_settings().database_url
    assert url.endswith("/interviewbud_test"), f"refusing to run tests against {url}"


import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _test_db():
    """Schema plus the real problem bank (from the already-downloaded dataset files) in the test database."""
    from app.coding import dataset
    from app.db import init_db

    await init_db()
    await dataset.ingest()
