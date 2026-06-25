from app.database import query
import secrets 
import string
from datetime import datetime, timedelta
from app.services.customer_service import get_customer_by_identifier

def generate_otp(length=6):
    # Use cryptographically secure random choice
    return "".join(secrets.choice(string.digits) for _ in range(length))

def save_otp(identifier, otp):
    """
    Saves OTP and enforces a 60-second cooldown to prevent spam.
    Returns True if successful, False if rate-limited.
    """
    # 1. Check for recent OTP requests (Rate Limiting)
    recent = query(
        """
        SELECT created_at FROM otps 
        WHERE identifier = %s 
        ORDER BY created_at DESC LIMIT 1
        """,
        params=(identifier,),
        fetchone=True
    )
    
    if recent:
        # Assuming your DB driver returns datetime objects
        time_since_last = datetime.now() - recent['created_at']
        if time_since_last < timedelta(seconds=60):
            return False 

    # 2. Invalidate older OTPs
    query(
        "UPDATE otps SET is_used = 1 WHERE identifier = %s AND is_used = 0",
        params=(identifier,),
        commit=True
    )

    # 3. Save new OTP securely
    expires_at = datetime.now() + timedelta(minutes=10)
    
    query(
        """
        INSERT INTO otps (identifier, otp_code, expires_at)
        VALUES (%s, %s, %s)
        """,
        params=(identifier, otp, expires_at),
        commit=True
    )
    
    return True

def initiate_login(identifier):
    """
    Handles the first step of the login process: checking the user,
    generating the code, and triggering the notification.
    """
    customer = get_customer_by_identifier(identifier)
    
    if not customer:
        # Security Best Practice: Silently return True even if the user doesn't exist.
        # This prevents hackers from using the login form to guess valid client emails.
        return True 
    
    otp_code = generate_otp()
    
    # Attempt to save. If it returns False, the 60-second cooldown is active.
    success = save_otp(identifier, otp_code)
    
    if not success:
        # You can catch this in your route to show a "Please wait 60 seconds" error
        return False 
    
    # --- Integration Point: Email / SMS Dispatch ---
    # Hook this up to SendGrid, AWS SES, or your SMS gateway.
    print(f"\n{'='*40}")
    print(f"🔒 SYSTEM DISPATCH: Sending OTP {otp_code} to {identifier}")
    print(f"{'='*40}\n")
    
    return True

def verify_otp_and_login(identifier, provided_otp):
    """
    Validates the submitted OTP. Returns a tuple: (Success_Boolean, Customer_Data_or_Error_Message)
    """
    customer = get_customer_by_identifier(identifier)
    if not customer:
        return False, "Invalid account."

    # Fetch the absolute latest unused OTP for this identifier
    db_otp = query(
        """
        SELECT id, otp_code, expires_at 
        FROM otps 
        WHERE identifier = %s AND is_used = 0 
        ORDER BY created_at DESC 
        LIMIT 1
        """,
        params=(identifier,),
        fetchone=True
    )

    # 1. Check if an OTP even exists
    if not db_otp:
        return False, "No active OTP found. Please request a new one."

    # 2. Check if the 10-minute window has expired
    if datetime.now() > db_otp["expires_at"]:
        return False, "OTP has expired. Please request a new one."

    # 3. Check if the code actually matches (strip spaces just in case)
    if str(db_otp["otp_code"]) != str(provided_otp).strip():
        return False, "Incorrect OTP."

    # 4. Success! Burn the OTP so it can never be used again
    query(
        "UPDATE otps SET is_used = 1 WHERE id = %s",
        params=(db_otp["id"],),
        commit=True
    )

    # Pass the full customer database row back to the route so it can create the session
    return True, customer