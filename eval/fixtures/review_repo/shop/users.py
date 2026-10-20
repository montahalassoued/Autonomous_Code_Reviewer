from shop.db import fetch_one


def get_user(user_id):
    row = fetch_one("SELECT id, email, role FROM users WHERE id = ?", (user_id,))
    if row is None:
        return None
    return {"id": row[0], "email": row[1], "role": row[2]}


def find_user_by_email(email):
    row = fetch_one("SELECT id, email, role FROM users WHERE email = ?", (email,))
    return {"id": row[0], "email": row[1], "role": row[2]} if row else None


def is_admin(user):
    return user is not None and user["role"] == "admin"
