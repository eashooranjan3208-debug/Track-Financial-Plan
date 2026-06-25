from decimal import Decimal, InvalidOperation
from flask import current_app, has_app_context
from app.database import query

DEFAULT_LIQUID_ASSET_CLASSES = ("liquid", "cash", "money market", "savings")

def get_networth_statement(customer_id):
    """
    Build a networth statement from client_assets for a customer.
    Executes a single, optimized query to categorize and sum all assets.
    """
    liquid_classes = _liquid_asset_classes()
    placeholders = ", ".join(["%s"] * len(liquid_classes))
    
    # We pass customer_id once for the WHERE clause, and then unpack the liquid_classes 
    # twice for the two IN clauses inside the CASE statements.
    params = (customer_id, *liquid_classes, *liquid_classes)

    # A single query hitting the table once is significantly faster than three separate queries.
    sql = f"""
        SELECT 
            COALESCE(SUM(CASE WHEN is_held_away = 0 THEN current_value ELSE 0 END), 0) AS vasupradah_assets,
            
            COALESCE(SUM(CASE WHEN is_held_away = 1 AND LOWER(asset_class) IN ({placeholders}) 
                         THEN current_value ELSE 0 END), 0) AS liquid_assets,
                         
            COALESCE(SUM(CASE WHEN is_held_away = 1 AND (asset_class IS NULL OR LOWER(asset_class) NOT IN ({placeholders})) 
                         THEN current_value ELSE 0 END), 0) AS non_liquid_assets
        FROM client_assets
        WHERE customer_id = %s
    """
    
    result = query(sql, params=params, fetchone=True)
    
    if not result:
        result = {"vasupradah_assets": 0, "liquid_assets": 0, "non_liquid_assets": 0}

    # Convert to strict Decimal for financial accuracy
    vasupradah_managed = _money(result.get("vasupradah_assets"))
    held_away_liquid = _money(result.get("liquid_assets"))
    non_liquid = _money(result.get("non_liquid_assets"))
    grand_total = vasupradah_managed + held_away_liquid + non_liquid

    # Return Decimals. Jinja handles Decimals perfectly with formatting like {:,.0f}
    return {
        "vasupradah_managed_assets": vasupradah_managed,
        "held_away_liquid_assets": held_away_liquid,
        "non_liquid_assets": non_liquid,
        "grand_total": grand_total,
        "vasupradah_total": vasupradah_managed, # Added to map to your HTML template variables
        "heldaway_total": held_away_liquid,     # Added to map to your HTML template variables
        "non_liquid_total": non_liquid          # Added to map to your HTML template variables
    }

def _liquid_asset_classes():
    """Read liquid asset-class labels from Flask config or use production defaults."""
    if has_app_context():
        configured = current_app.config.get("LIQUID_ASSET_CLASSES", DEFAULT_LIQUID_ASSET_CLASSES)
    else:
        configured = DEFAULT_LIQUID_ASSET_CLASSES

    classes = tuple(str(asset_class).strip().lower() for asset_class in configured if asset_class)
    return classes or DEFAULT_LIQUID_ASSET_CLASSES

def _money(value):
    """Convert database numeric values to Decimal for precise subtotal math."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid money value returned from database: {value!r}") from exc