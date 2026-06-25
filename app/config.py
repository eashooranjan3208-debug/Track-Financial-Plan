import os
from datetime import timedelta
from dotenv import load_dotenv

# Explicitly load the .env file
load_dotenv()

class Config:
    """Base configuration containing your database and app secrets."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'fallback-secret-key')
    
    # MySQL Database Config 
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '3306')
    DB_NAME = os.environ.get('DB_NAME', 'track_act')
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')

    # --- Base Security Settings ---
    # Prevents JavaScript from reading the session cookie (XSS protection)
    SESSION_COOKIE_HTTPONLY = True
    # Prevents CSRF attacks by restricting cross-site cookie usage
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Automatically log idle users out after 30 minutes
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

class DevelopmentConfig(Config):
    DEBUG = True
    ENV = 'development'
    # Localhost runs on HTTP, so we cannot enforce Secure cookies here
    SESSION_COOKIE_SECURE = False 

class ProductionConfig(Config):
    DEBUG = False
    ENV = 'production'
    # In production, we NEVER want to fall back to a default secret key
    SECRET_KEY = os.environ.get('SECRET_KEY') 
    
    # --- Production-Only Security Overrides ---
    # Enforce HTTPS only for cookies in production to prevent network sniffing
    SESSION_COOKIE_SECURE = True 

# Dictionary to easily map environment names to config objects
config_by_name = {
    'dev': DevelopmentConfig,
    'development': DevelopmentConfig,
    'prod': ProductionConfig,
    'production': ProductionConfig
}