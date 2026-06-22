from app.database import query
import secrets # <-- Replaced 'random' with 'secrets'
import string
from datetime import datetime, timedelta

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
        # Assuming 'created_at' is a column in your DB (you should add it if not)
        time_since_last = datetime.now() - recent['created_at']
        if time_since_last < timedelta(seconds=60):
            # Tell the route to return an error: "Please wait 60s before requesting a new OTP"
            return False 

    # 2. Invalidate older OTPs
    query(
        "UPDATE otps SET is_used = 1 WHERE identifier = %s AND is_used = 0",
        params=(identifier,),
        commit=True
    )

    # 3. Save new OTP (Added created_at logic contextually)
    query(
        """
        INSERT INTO otps (identifier, otp_code, expires_at)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 10 MINUTE))
        """,
        params=(identifier, otp),
        commit=True
    )
    
    return True

# verify_otp remains largely the same, but ensure your SQL schema has 'is_used' as TINYINT/BOOLEAN