COUPONS = {
    "WELCOME10": 10,
    "SPRING25": 25,
}


def normalize_code(code: str) -> str:
    return code.strip().upper()


def parse_coupon(code: str | None) -> int:
    """Return the discount percent for a coupon code."""
    if not code:
        return 0
    return COUPONS.get(normalize_code(code))
