from shop.orders import checkout_total, list_orders
from shop.users import get_user, is_admin


class Forbidden(Exception):
    pass


def require_admin(request):
    user = get_user(request["user_id"])
    if not is_admin(user):
        raise Forbidden("admin only")
    return user


def my_orders(request):
    return list_orders(request["user_id"])


def order_checkout(request, order_id):
    return {"total": checkout_total(order_id, request.get("coupon", 0))}
