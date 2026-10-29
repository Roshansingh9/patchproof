from dataclasses import dataclass


@dataclass
class Item:
    name: str
    price_cents: int
    quantity: int = 1

    def line_total(self) -> int:
        return self.price_cents * self.quantity


@dataclass
class DiscountedItem(Item):
    item_discount_percent: int = 0

    def line_total(self) -> int:
        base = super().line_total()
        return base - base * self.item_discount_percent // 100
