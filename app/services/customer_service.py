from app.database import query


def get_customer_by_id(customer_id):
    """
    Fetch a single customer's full profile by their ID.
    Returns a dict or None if not found.
    """
    return query(
        "SELECT * FROM customers WHERE id = %s",
        params=(customer_id,),
        fetchone=True
    )


def get_customer_by_email(email):
    """
    Fetch a customer by email address.
    Used during login to identify who is logging in.
    """
    return query(
        "SELECT * FROM customers WHERE email = %s",
        params=(email,),
        fetchone=True
    )


def get_customer_by_mobile(mobile):
    """
    Fetch a customer by mobile number.
    Used during login when customer enters mobile instead of email.
    """
    return query(
        "SELECT * FROM customers WHERE mobile = %s",
        params=(mobile,),
        fetchone=True
    )


def get_all_customers():
    """
    Fetch all customers — used by admin panel.
    Returns a list of dicts.
    """
    return query(
        "SELECT id, name, email, mobile, created_at FROM customers ORDER BY created_at DESC"
    )