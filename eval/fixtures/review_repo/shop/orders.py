from shop.db import fetch_all
from shop.pricing import apply_discount, order_total


def list_orders(user_id):
    return fetch_all("SELECT id, status FROM orders WHERE user_id = ?", (user_id,))


def order_items(order_id):
    rows = fetch_all("SELECT price, quantity FROM order_items WHERE order_id = ?", (order_id,))
    return [{"price": price, "quantity": quantity} for price, quantity in rows]


def checkout_total(order_id, coupon_percent=0):
    return apply_discount(order_total(order_items(order_id)), coupon_percent)


def page_of(items, page, size):
    start = (page - 1) * size
    return items[start:start + size]
