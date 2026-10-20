from shop.pricing import apply_discount, order_total


def test_apply_discount():
    assert apply_discount(100.0, 25) == 75.0


def test_order_total():
    assert order_total([{"price": 2.5, "quantity": 2}]) == 5.0
