from app.database import query
import random
import string


def generate_otp(length=6):
    return "".join(random.choices(string.digits, k=length))


def save_otp(identifier, otp):
    # Delete old unused OTPs for this email/mobile
    query(
        "DELETE FROM otps WHERE identifier = %s",
        params=(identifier,),
        commit=True
    )

    # Save new OTP with 10-minute expiry
    query(
        """
        INSERT INTO otps (identifier, otp_code, expires_at)
        VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 10 MINUTE))
        """,
        params=(identifier, otp),
        commit=True
    )


def verify_otp(identifier, otp_entered):
    record = query(
        """
        SELECT * FROM otps
        WHERE identifier = %s
          AND otp_code = %s
          AND is_used = 0
          AND expires_at >= NOW()
        """,
        params=(identifier, otp_entered),
        fetchone=True
    )

    if record:
        query(
            """
            UPDATE otps
            SET is_used = 1
            WHERE id = %s
            """,
            params=(record["id"],),
            commit=True
        )
        return True

    return False