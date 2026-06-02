from app.database import query


def get_financial_plan(customer_id):
    """
    Fetch the active financial plan for a customer.
    Returns the most recent active plan or None.
    """
    return query(
        """
        SELECT * FROM financial_plans
        WHERE customer_id = %s AND is_current = 1
        ORDER BY uploaded_at DESC
        LIMIT 1
        """,
        params=(customer_id,),
        fetchone=True
    )


def get_archived_plans(customer_id):
    """
    Fetch all inactive (archived) financial plans for a customer.
    """
    return query(
        """
        SELECT * FROM financial_plans
        WHERE customer_id = %s AND is_current = 0
        ORDER BY created_at DESC
        """,
        params=(customer_id,)
    )


def get_yearly_investments(customer_id):
    """
    Fetch all yearly investment records for a customer.
    Ordered by year ascending so charts display correctly.
    """
    return query(
        """
        SELECT * FROM yearly_investments
        WHERE customer_id = %s
        ORDER BY year ASC
        """,
        params=(customer_id,)
    )


def get_portfolio_values(customer_id):
    """
    Fetch all portfolio value records for a customer.
    """
    return query(
        """
        SELECT * FROM portfolio_values
        WHERE customer_id = %s
        ORDER BY year ASC
        """,
        params=(customer_id,)
    )


def get_asset_allocation(customer_id):
    """
    Fetch asset allocation breakdown for a customer.
    """
    return query(
        """
        SELECT * FROM asset_allocation
        WHERE customer_id = %s
        ORDER BY year ASC, asset_class ASC 
        """,
        params=(customer_id,)
    )


def get_other_assets(customer_id):
    """
    Fetch other assets for a customer.
    """
    return query(
        """
        SELECT * FROM other_assets
        WHERE customer_id = %s
        ORDER BY updated_at DESC 
        """,
        params=(customer_id,)
    )