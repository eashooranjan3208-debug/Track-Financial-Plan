from dotenv import load_dotenv
import os

load_dotenv()

print(os.getenv("DB_HOST"))
print(os.getenv("DB_USER"))
print(os.getenv("DB_NAME"))