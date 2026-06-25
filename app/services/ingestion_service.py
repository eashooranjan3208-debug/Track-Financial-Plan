import json
import logging
from decimal import Decimal, InvalidOperation

import mysql.connector

from app.database import get_db


CUSTOMER_EMAIL_KEYS = ("email", "client_email", "customer_email", "email_id", "mail", "mail_id")
CUSTOMER_NAME_KEYS = ("client_name", "customer_name", "name", "full_name")
PAN_KEYS = ("pan_number", "pan_no", "pan", "pan_card", "pan_card_number")
CURRENT_ASSETS_KEY = "dfcurrent_assets"


class IngestionError(ValueError):
    """Raised when an uploaded plan JSON cannot be safely ingested."""


def process_json_upload(filepath):
    """
    Read an uploaded financial plan JSON file, upsert the customer, and sync current assets.

    The function loads and unwraps the plan payload, extracts the customer email/name/PAN,
    writes the customer through an INSERT ... ON DUPLICATE KEY UPDATE statement, then batch
    upserts dfcurrent_assets into client_assets for that customer. Current assets are treated
    as managed assets unless the incoming row explicitly marks them as held away.
    """
    payload = _load_plan_payload(filepath)
    email = _required_text(_first_value(payload, CUSTOMER_EMAIL_KEYS), "client email").lower()
    client_name = _required_text(_first_value(payload, CUSTOMER_NAME_KEYS), "client name")
    pan_number = _required_text(_first_value(payload, PAN_KEYS), "PAN number")
    assets = _normalise_current_assets(payload.get(CURRENT_ASSETS_KEY))

    connection = get_db()
    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)
        customer_id = _upsert_customer(cursor, email, client_name, pan_number)

        if assets:
            _upsert_client_assets(cursor, customer_id, assets)

        connection.commit()
        return {
            "customer_id": customer_id,
            "email": email,
            "client_name": client_name,
            "pan_number": pan_number,
            "assets_processed": len(assets),
        }
    except (mysql.connector.Error, IngestionError):
        connection.rollback()
        logging.exception("Failed to process financial plan JSON upload: %s", filepath)
        raise
    finally:
        if cursor:
            cursor.close()
        connection.close()


def _load_plan_payload(filepath):
    """
    Load a plan JSON file and unwrap single-key customer/PAN envelopes when present.

    Some exports arrive as {"PAN": {...plan...}} while others contain the plan fields at
    the top level. This helper normalizes both shapes into the inner plan dictionary.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as plan_file:
            raw_payload = json.load(plan_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestionError(f"Unable to read valid JSON from {filepath!r}") from exc

    if not isinstance(raw_payload, dict):
        raise IngestionError("Uploaded plan JSON must contain an object at the top level.")

    if len(raw_payload) == 1:
        wrapper_key, wrapped_payload = next(iter(raw_payload.items()))
        if isinstance(wrapped_payload, dict) and not _contains_any(raw_payload, PAN_KEYS):
            wrapped_payload.setdefault("pan_no", wrapper_key)
            return wrapped_payload

    return raw_payload


def _upsert_customer(cursor, email, client_name, pan_number):
    """
    Insert or update the customer row and return the database customer id.

    The LAST_INSERT_ID trick makes MySQL expose the existing id through cursor.lastrowid
    when ON DUPLICATE KEY UPDATE is triggered by a unique email or PAN constraint.
    """
    cursor.execute(
        """
        INSERT INTO customers (email, name, pan, is_active)
        VALUES (%s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            pan = VALUES(pan),
            is_active = 1,
            id = LAST_INSERT_ID(id)
        """,
        (email, client_name, pan_number),
    )

    customer_id = cursor.lastrowid
    if customer_id:
        return customer_id

    cursor.execute(
        "SELECT id FROM customers WHERE LOWER(email) = %s LIMIT 1",
        (email,),
    )
    customer = cursor.fetchone()
    if not customer:
        raise IngestionError("Customer upsert succeeded but no customer id was returned.")

    return customer["id"]


def _upsert_client_assets(cursor, customer_id, assets):
    """
    Batch upsert client assets for the customer.

    The client_assets table should have a unique key on (customer_id, asset_name) so an
    uploaded asset row updates the existing customer asset instead of creating duplicates.
    """
    cursor.executemany(
        """
        INSERT INTO client_assets (
            customer_id, asset_name, asset_class, current_value, is_held_away
        ) VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            asset_class = VALUES(asset_class),
            current_value = VALUES(current_value),
            is_held_away = VALUES(is_held_away)
        """,
        [
            (
                customer_id,
                asset["asset_name"],
                asset["asset_class"],
                asset["current_value"],
                asset["is_held_away"],
            )
            for asset in assets
        ],
    )


def _normalise_current_assets(raw_assets):
    """
    Convert dfcurrent_assets into rows that can be inserted into client_assets.

    The source may be a list of objects, a JSON string, or a column-oriented dataframe
    dictionary. Invalid rows without an asset name are skipped; numeric values are safely
    converted to Decimal to preserve money precision for MySQL.
    """
    rows = _normalise_table(raw_assets)
    assets = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        asset_name = _clean_text(row.get("asset_name") or row.get("name"))
        if not asset_name:
            continue

        assets.append(
            {
                "asset_name": asset_name,
                "asset_class": _clean_text(row.get("asset_class") or row.get("class")),
                "current_value": _money(row.get("current_value") or row.get("value") or 0),
                "is_held_away": _as_bool(row.get("is_held_away") or row.get("held_away")),
            }
        )

    return assets


def _normalise_table(raw_value):
    """
    Normalize common JSON dataframe shapes into a list of dictionaries.

    Supports plain row lists, JSON-encoded lists/dicts, dict-of-row objects, and
    column-oriented dictionaries such as {"name": [...], "current_value": [...]}.
    """
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise IngestionError("dfcurrent_assets must be valid JSON when supplied as text.") from exc

    if isinstance(raw_value, list):
        return raw_value

    if isinstance(raw_value, dict):
        if all(isinstance(value, list) for value in raw_value.values()):
            keys = list(raw_value.keys())
            return [dict(zip(keys, values)) for values in zip(*raw_value.values())]

        if all(isinstance(value, dict) for value in raw_value.values()):
            return list(raw_value.values())

        return [raw_value]

    raise IngestionError("dfcurrent_assets must be a list, object, or JSON-encoded table.")


def _first_value(payload, keys):
    """Return the first non-empty value found for a group of possible JSON keys."""
    for key in keys:
        value = payload.get(key)
        if _clean_text(value):
            return value
    return None


def _contains_any(payload, keys):
    """Return True when any expected key is present in the provided mapping."""
    return any(key in payload for key in keys)


def _required_text(value, label):
    """Clean a required text value or raise a readable ingestion error."""
    text = _clean_text(value)
    if not text:
        raise IngestionError(f"Uploaded plan JSON is missing {label}.")
    return text


def _clean_text(value):
    """Normalize optional text values from the JSON payload."""
    if value is None:
        return None

    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return None

    return text


def _money(value):
    """Convert a source money value to Decimal without trusting formatting."""
    if value is None:
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "").strip() or "0")
    except (InvalidOperation, AttributeError) as exc:
        raise IngestionError(f"Invalid money value in current assets: {value!r}") from exc


def _as_bool(value):
    """Convert common JSON truthy/falsey representations to a MySQL-friendly integer."""
    if isinstance(value, bool):
        return int(value)

    if value is None:
        return 0

    text = str(value).strip().lower()
    return int(text in {"1", "true", "yes", "y", "held away", "held_away"})
