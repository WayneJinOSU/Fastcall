import os
from dotenv import load_dotenv
load_dotenv(override=True)

mode = os.environ.get("APP_ENV", "prod")
