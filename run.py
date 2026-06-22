import os
from app import create_app

# The environment config should dictate how the app runs
app = create_app(os.environ.get('FLASK_ENV', 'dev'))

if __name__ == "__main__":
    # Get debug state from environment, defaulting to False for safety
    is_debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(debug=is_debug)