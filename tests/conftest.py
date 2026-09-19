import pytest


@pytest.fixture(autouse=True)
async def _dispose_engine_per_test():
    """Each pytest-asyncio test gets a fresh event loop; the SQLAlchemy async engine's
    connection pool is bound to whichever loop created it, so dispose it after every
    test to force reconnection on the next loop."""
    yield
    from app.db.session import engine

    await engine.dispose()
