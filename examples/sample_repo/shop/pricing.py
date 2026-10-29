from .coupons import parse_coupon
from .models import Item


def subtotal(items: list[Item]) -> int:
    return sum(item.line_total() for item in items)


def apply_discount(amount_cents: int, percent: int) -> int:
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return amount_cents - amount_cents * percent // 100


def compute_total(items: list[Item], coupon_code: str | None = None) -> int:
    percent = parse_coupon(coupon_code)
    return apply_discount(subtotal(items), percent)
