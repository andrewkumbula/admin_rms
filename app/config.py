import os


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
    RMS_API_BASE_URL = os.getenv("RMS_API_BASE_URL", "http://localhost:8080")
    RMS_API_BASE_URL_DEV = os.getenv("RMS_API_BASE_URL_DEV", "")
    RMS_API_BASE_URL_PROD = os.getenv("RMS_API_BASE_URL_PROD", "")
    # Server environments can respond slowly; keep a safe minimum timeout.
    RMS_API_TIMEOUT_SECONDS = max(int(os.getenv("RMS_API_TIMEOUT_SECONDS", "20")), 20)
    DEV_AUTH_STUB = os.getenv("DEV_AUTH_STUB", "true").lower() == "true"

    KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL", "")
    KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "")
    KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "")
    KEYCLOAK_REDIRECT_URI = os.getenv("KEYCLOAK_REDIRECT_URI", "http://localhost:5001/callback")
