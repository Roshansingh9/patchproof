from shop.cart import Cart
from shop.models import DiscountedItem, Item
from shop.pricing import apply_discount


def test_total_without_coupon():
    cart = Cart()
    cart.add(Item("pen", 250, 2))
    assert cart.checkout_total() == 500


def test_total_with_known_coupon():
    cart = Cart()
    cart.add(Item("book", 1000))
    assert cart.checkout_total("welcome10") == 900


def test_total_with_unknown_coupon():
    cart = Cart()
    cart.add(DiscountedItem("lamp", 2000, item_discount_percent=50))
    assert cart.checkout_total("EXPIRED50") == 1000


def test_apply_discount_bounds():
    assert apply_discount(1000, 0) == 1000
