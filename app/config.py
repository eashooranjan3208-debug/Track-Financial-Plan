import os
from dotenv import load_dotenv

# Explicitly load the .env file
load_dotenv()

class Config:
    """Base configuration containing your database and app secrets."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'fallback-secret-key')
    
    # MySQL Database Config (Matched to your .env keys)
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '3306')
    DB_NAME = os.environ.get('DB_NAME', 'track_act')
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')

class DevelopmentConfig(Config):
    DEBUG = True
    ENV = 'development'

class ProductionConfig(Config):
    DEBUG = False
    ENV = 'production'
    # In production, we NEVER want to fall back to a default secret key
    SECRET_KEY = os.environ.get('SECRET_KEY') 

# Dictionary to easily map environment names to config objects
# Dictionary to easily map environment names to config objects
config_by_name = {
    'dev': DevelopmentConfig,
    'development': DevelopmentConfig,
    'prod': ProductionConfig,
    'production': ProductionConfig
}