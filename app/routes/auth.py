import logging
import secrets
import string
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from app.database import query

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Start the OTP login flow for a customer email.

    On POST, the route checks whether the submitted email belongs to an active
    customer. If it does, it generates a six-digit OTP, stores it in otp_codes
    with a fifteen-minute expiration, logs the simulated email dispatch, and
    moves the browser to the verification step.
    """
    if session.get("user_id"):
        return redirect(url_for("customer.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email.", "warning")
            return redirect(url_for("auth.login"))

        customer = _get_customer_by_email(email)
        if customer:
            otp_code = _generate_otp()
            _store_otp(customer["id"], email, otp_code)
            logging.info("Simulated OTP email to %s with code %s", email, otp_code)

        session["pending_email"] = email
        flash("If an account exists, an OTP has been sent to your email.", "info")
        return redirect(url_for("auth.verify"))

    return render_template("auth/login.html")


@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """
    Complete the OTP login flow and create the customer session.

    On POST, the route retrieves the latest unused OTP for the pending email,
    verifies the supplied code, checks the expiration timestamp, marks the OTP
    as used, stores session['user_id'], and redirects to the customer dashboard.
    """
    email = session.get("pending_email")
    if not email:
        flash("Session expired. Please try logging in again.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        otp_code = (request.form.get("otp_code") or request.form.get("otp") or "").strip()
        otp_record = _get_latest_unused_otp(email)

        if not otp_record or str(otp_record["otp_code"]) != otp_code:
            flash("Invalid OTP. Please try again.", "danger")
            return redirect(url_for("auth.verify"))

        if _is_expired(otp_record["expires_at"]):
            flash("OTP has expired. Please request a new code.", "danger")
            return redirect(url_for("auth.verify"))

        _mark_otp_used(otp_record["id"])
        session.clear()
        session.permanent = True
        session["user_id"] = otp_record["customer_id"]
        session["role"] = "customer"

        flash("Login successful! Welcome back.", "success")
        return redirect(url_for("customer.dashboard"))

    return render_template("auth/verify.html", email=email)


@auth_bp.route("/logout")
def logout():
    """Clear all authentication state and return the user to the login page."""
    session.clear()
    flash("You have been successfully logged out.", "success")
    return redirect(url_for("auth.login"))


def _get_customer_by_email(email):
    """
    Fetch an active customer by email for the login flow.

    The lookup is case-insensitive and parameterized so user input never becomes
    executable SQL.
    """
    return query(
        """
        SELECT id, email, name
        FROM customers
        WHERE LOWER(email) = %s AND is_active = 1
        LIMIT 1
        """,
        params=(email.lower(),),
        fetchone=True,
    )


def _generate_otp():
    """
    Generate a cryptographically secure numeric OTP.

    OTP length is read from Flask config with a six-digit default matching the
    product requirement.
    """
    length = int(current_app.config.get("OTP_LENGTH", 6))
    return "".join(secrets.choice(string.digits) for _ in range(length))


def _store_otp(customer_id, email, otp_code):
    """
    Persist a new OTP code with a configured expiration timestamp.

    Older unused OTPs for the same email are marked used first so only the latest
    code remains valid during verification.
    """
    expires_at = datetime.utcnow() + timedelta(
        minutes=int(current_app.config.get("OTP_EXPIRY_MINUTES", 15))
    )

    query(
        """
        UPDATE otp_codes
        SET is_used = 1
        WHERE LOWER(email) = %s AND is_used = 0
        """,
        params=(email.lower(),),
        commit=True,
    )
    query(
        """
        INSERT INTO otp_codes (customer_id, email, otp_code, expires_at, is_used)
        VALUES (%s, %s, %s, %s, 0)
        """,
        params=(customer_id, email, otp_code, expires_at),
        commit=True,
    )


def _get_latest_unused_otp(email):
    """
    Retrieve the most recent unused OTP for an email address.

    Expiration is checked in Python after retrieval so the route can produce a
    clear error path and still mark only explicitly verified codes as used.
    """
    return query(
        """
        SELECT id, customer_id, otp_code, expires_at
        FROM otp_codes
        WHERE LOWER(email) = %s AND is_used = 0
        ORDER BY expires_at DESC, id DESC
        LIMIT 1
        """,
        params=(email.lower(),),
        fetchone=True,
    )


def _mark_otp_used(otp_id):
    """Mark a successfully verified OTP as used so it cannot be replayed."""
    query(
        "UPDATE otp_codes SET is_used = 1 WHERE id = %s",
        params=(otp_id,),
        commit=True,
    )


def _is_expired(expires_at):
    """Return True when an OTP expiration timestamp is in the past."""
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    return datetime.utcnow() > expires_at
