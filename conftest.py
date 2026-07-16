"""Shared pytest fixtures.

Every test here talks to the database, so rather than decorating each one with
@pytest.mark.django_db, the marker is applied to the whole package below.
"""
import pytest

from tests.factories import make_owner, make_business, make_product


def pytest_collection_modifyitems(items):
    """Give every test in this package DB access — none of them are pure-unit."""
    for item in items:
        item.add_marker(pytest.mark.django_db)


@pytest.fixture
def owner():
    user, _sub = make_owner()
    return user


@pytest.fixture
def business(owner):
    biz, _bp = make_business(owner)
    return biz


@pytest.fixture
def product(business):
    return make_product(business, selling_price='100', cost_price='60')
