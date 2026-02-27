from decimal import Decimal

from planner.templatetags.planner_extras import money


def test_money_filter_preserves_cents():
    assert money(Decimal("1620.95"), "USD") == "USD 1,620.95"
    assert money("0", "USD") == "USD 0.00"
    assert money(None, "USD") == "USD 0.00"
