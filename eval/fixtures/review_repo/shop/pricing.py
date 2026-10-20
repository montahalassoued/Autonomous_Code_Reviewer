def apply_discount(price, percent):
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (100 - percent) / 100, 2)


def order_total(items):
    return round(sum(item["price"] * item["quantity"] for item in items), 2)
