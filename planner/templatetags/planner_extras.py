from decimal import Decimal

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def is_external(value: str) -> bool:
    return isinstance(value, str) and (value.startswith("http") or value.startswith("/"))


@register.filter
def money(value, currency: str = "USD") -> str:  # noqa: ANN001
    if value is None:
        return f"{currency} 0.00"
    amount = Decimal(value)
    return f"{currency} {amount:,.2f}"


@register.filter
def duration_hm(minutes: int) -> str:
    minutes = int(minutes or 0)
    hours, rem = divmod(minutes, 60)
    return f"{hours}h {rem}m"


@register.filter
def minutes_ago(value) -> str:  # noqa: ANN001
    if not value:
        return "unknown"
    delta = timezone.now() - value
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"
