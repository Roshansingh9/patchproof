from .models import Item
from .pricing import compute_total


class Cart:
    def __init__(self) -> None:
        self.items: list[Item] = []

    def add(self, item: Item) -> None:
        self.items.append(item)

    def checkout_total(self, coupon_code: str | None = None) -> int:
        return compute_total(self.items, coupon_code)
