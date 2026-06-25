from app.database import query
import re 

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

def get_customer_by_identifier(identifier):
    """
    Fetches an active customer record using either an email or a mobile number.
    """
    if not identifier:
        return None

    clean_id = identifier.strip()

    # Simple regex to check if the input looks like an email address
    is_email = re.match(r"[^@]+@[^@]+\.[^@]+", clean_id)

    if is_email:
        # It's an email: force lowercase for safe matching
        return query(
            "SELECT * FROM customers WHERE LOWER(email) = %s AND is_active = 1",
            params=(clean_id.lower(),),
            fetchone=True
        )
    else:
        # It's a mobile number: strip out any spaces, dashes, or parentheses
        clean_mobile = re.sub(r"\D", "", clean_id) 
        
        return query(
            "SELECT * FROM customers WHERE mobile = %s AND is_active = 1",
            params=(clean_mobile,),
            fetchone=True
        )
def get_all_customers():
    """
    Fetch all customers — used by admin panel.
    Upgraded to include PAN, plan status, and risk category for the UI.
    """
    sql = """
        SELECT 
            c.id, 
            c.pan, 
            c.name, 
            c.email, 
            c.mobile, 
            c.is_active,
            fp.uploaded_at AS plan_start_date,
            (fp.id IS NOT NULL) AS has_active_plan,
            NULL AS risk_category 
        FROM customers c
        LEFT JOIN financial_plans fp ON c.id = fp.customer_id AND fp.is_current = 1
        ORDER BY c.created_at DESC
    """
    return query(sql)