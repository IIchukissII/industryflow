"""
Pytest configuration for ML Service tests
"""
import pytest
import asyncio


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def pytest_configure(config):
    """Configure pytest markers"""
    config.addinivalue_line(
        "markers", "redis: tests that require Redis server running"
    )
    config.addinivalue_line(
        "markers", "integration: integration tests (slower)"
    )
