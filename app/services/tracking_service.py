import json
import re
import os 
from decimal import Decimal, InvalidOperation

from flask import current_app, has_app_context

from app.database import query
from app.services.dashboard_service import get_networth_statement


DEFAULT_DRIFT_THRESHOLD_PCT = Decimal("10")
DEFAULT_TRANSACTION_TABLE = "customer_transactions"
DEFAULT_TRANSACTION_AMOUNT_COLUMN = "total_amount"
CLIENT_ASSETS_TABLE = "client_assets"

TARGET_ALLOCATION_KEYS = {
    "Equity": ("w_eq", "weight_equity"),
    "Debt": ("w_de", "weight_debt"),
    "Liquid": ("w_li", "weight_liquid"),
    "Gold": ("w_go", "weight_gold"),
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TrackingError(ValueError):
    """Raised when tracking calculations cannot be completed safely."""

def get_actual_investments(customer_id):
    """
    Sum the actual investments from the transactions table.
    Hardcoded table and column names for strict security and simplicity.
    """
    result = query(
        """
        SELECT COALESCE(SUM(amount), 0) AS actual_investments
        FROM transactions
        WHERE customer_id = %s
        """,
        params=(customer_id,),
        fetchone=True,
    )
    return _money(result.get("actual_investments") if result else 0)

def _coerce_json_payload(json_data):
    """Accept a plan dict, JSON string, or file path and return the plan dictionary."""
    if isinstance(json_data, dict):
        payload = json_data
    elif isinstance(json_data, str):
        stripped = json_data.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise TrackingError("Failed to parse JSON string.") from exc
        else:
            # Bug Fix: Ensure the file actually exists before trying to open it
            if not os.path.exists(stripped):
                raise TrackingError(f"Financial plan file not found at path: {stripped}")
            
            try:
                with open(stripped, "r", encoding="utf-8") as plan_file:
                    payload = json.load(plan_file)
            except Exception as exc:
                raise TrackingError("Failed to read or parse the JSON file.") from exc
    else:
        raise TrackingError("json_data must be a dictionary, JSON string, or file path.")

    if not isinstance(payload, dict):
        raise TrackingError("json_data must resolve to a JSON object.")

    if len(payload) == 1:
        only_value = next(iter(payload.values()))
        if isinstance(only_value, dict):
            return only_value

    return payload

def calculate_investment_deviation(customer_id, json_data):
    """
    Compare actual invested money against the plan target surplus for a customer.
    Flags 'Off Track' if actual investment falls more than 10% below the target.
    """
    payload = _coerce_json_payload(json_data)
    target = _money(payload.get("target_surplus"))
    
    # We replaced the dynamic table helper with the hardcoded function we wrote earlier
    actual = get_actual_investments(customer_id) 
    
    drift_threshold = _config_decimal("TRACKING_DRIFT_THRESHOLD_PCT", Decimal("10.0"))
    minimum_required = target * (Decimal("1") - (drift_threshold / Decimal("100")))
    deviation = actual - target
    deviation_pct = _percentage(deviation, target)
    
    status = "Off Track" if target > 0 and actual < minimum_required else "In Line"

    # Removed float() casts to preserve strict financial precision
    return {
        "target_surplus": target,
        "actual_investments": actual,
        "minimum_required": minimum_required,
        "deviation": deviation,
        "deviation_pct": deviation_pct,
        "status": status,
    }


def calculate_allocation_deviation(customer_id, json_data):
    """
    Compare target allocation percentages from JSON against actual client assets.
    Flags 'Action Req' if any asset class drifts more than ±10%.
    """
    payload = _coerce_json_payload(json_data)
    target_allocations = _target_allocations(payload)
    actual_values = _actual_asset_values(customer_id)
    total_value = sum(actual_values.values(), Decimal("0"))
    
    drift_threshold = _config_decimal("TRACKING_DRIFT_THRESHOLD_PCT", Decimal("10.0"))
    rows = []

    for asset_class, target_pct in target_allocations.items():
        current_value = actual_values.get(asset_class, Decimal("0"))
        actual_pct = _percentage(current_value, total_value)
        deviation = actual_pct - target_pct
        
        # abs() correctly triggers if deviation is +11% or -11%
        status = "Action Req" if abs(deviation) > drift_threshold else "In Line"

        # Removed float() casts
        rows.append(
            {
                "class_name": asset_class, # Mapped to your HTML template variable
                "target_pct": target_pct,
                "actual_pct": actual_pct,
                "deviation": deviation,
                "status": status,
            }
        )

    return rows


def _sum_actual_investments(customer_id):
    """
    Sum actual investment transactions for a customer using validated SQL identifiers.

    Table and column names can be overridden in Flask config while values remain
    parameterized to prevent SQL injection.
    """
    table_name = _sql_identifier(_config_value("TRANSACTION_TABLE", DEFAULT_TRANSACTION_TABLE))
    amount_column = _sql_identifier(
        _config_value("TRANSACTION_AMOUNT_COLUMN", DEFAULT_TRANSACTION_AMOUNT_COLUMN)
    )
    result = query(
        f"""
        SELECT COALESCE(SUM({amount_column}), 0) AS actual_investments
        FROM {table_name}
        WHERE customer_id = %s
        """,
        params=(customer_id,),
        fetchone=True,
    )
    return _money(result.get("actual_investments") if result else 0)


def _actual_asset_values(customer_id):
    """Return actual client asset values grouped into the target allocation classes."""
    rows = query(
        """
        SELECT asset_class, COALESCE(SUM(current_value), 0) AS current_value
        FROM client_assets
        WHERE customer_id = %s
        GROUP BY asset_class
        """,
        params=(customer_id,),
    )

    values = {asset_class: Decimal("0") for asset_class in TARGET_ALLOCATION_KEYS}
    for row in rows or []:
        asset_class = _normalise_asset_class(row.get("asset_class"))
        if asset_class in values:
            values[asset_class] += _money(row.get("current_value"))

    return values


def _target_allocations(payload):
    """Extract target allocation percentages from supported JSON key aliases."""
    targets = {}
    for asset_class, aliases in TARGET_ALLOCATION_KEYS.items():
        targets[asset_class] = _money(_first_payload_value(payload, aliases))
    return targets




def _first_payload_value(payload, keys):
    """Return the first value available from a set of JSON key aliases."""
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return 0


def _normalise_asset_class(asset_class):
    """Map free-form asset class labels to the canonical allocation buckets."""
    text = str(asset_class or "").strip().lower()

    if "equity" in text or "stock" in text or "share" in text:
        return "Equity"
    if "debt" in text or "bond" in text or "fixed" in text:
        return "Debt"
    if "liquid" in text or "cash" in text or "money market" in text:
        return "Liquid"
    if "gold" in text or "commodity" in text:
        return "Gold"

    return str(asset_class or "").strip().title()


def _money(value):
    """Convert numeric inputs to Decimal while tolerating commas and blanks."""
    if value is None:
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "").strip() or "0")
    except (InvalidOperation, AttributeError) as exc:
        raise TrackingError(f"Invalid numeric value: {value!r}") from exc


def _percentage(numerator, denominator):
    """Return a rounded percentage, using zero when the denominator is empty."""
    if not denominator:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(Decimal("0.01"))


def _config_value(key, default):
    """Read a Flask config value when an app context exists."""
    if has_app_context():
        return current_app.config.get(key, default)
    return default


def _config_decimal(key, default):
    """Read a numeric config value as Decimal."""
    return _money(_config_value(key, default))


def _sql_identifier(identifier):
    """Validate a configured table or column name before interpolating into SQL."""
    if not IDENTIFIER_RE.match(str(identifier)):
        raise TrackingError(f"Unsafe SQL identifier configured: {identifier!r}")
    return f"`{identifier}`"

def calculate_portfolio_deviation(customer_id, json_data):
    """
    Compare actual combined networth against the expected portfolio value in the JSON.
    Flags 'Off Track' if the actual portfolio is more than 10% below the target.
    """
    payload = _coerce_json_payload(json_data)
    
    # Extract the target portfolio value from the JSON payload
    # Note: Adjust the exact key 'Expected_Portfolio_Value' if your JSON nests it differently
    target_portfolio_str = _first_payload_value(payload, ["Expected_Portfolio_Value", "expected_portfolio_value"])
    target = _money(target_portfolio_str)
    
    # Get the actual portfolio value using the dashboard service we already optimized
    networth_data = get_networth_statement(customer_id)
    actual = networth_data["grand_total"]
    
    drift_threshold = _config_decimal("TRACKING_DRIFT_THRESHOLD_PCT", Decimal("10.0"))
    minimum_required = target * (Decimal("1") - (drift_threshold / Decimal("100")))
    
    deviation = actual - target
    deviation_pct = _percentage(deviation, target)
    
    status = "Off Track" if target > 0 and actual < minimum_required else "In Line"

    return {
        "target_portfolio": target,
        "actual_portfolio": actual,
        "minimum_required": minimum_required,
        "deviation": deviation,
        "deviation_pct": deviation_pct,
        "status": status,
    }