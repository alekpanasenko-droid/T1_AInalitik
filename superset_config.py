"""Local demonstration settings. The secret is provided by ignored environment file."""
import os

SECRET_KEY = os.environ['SUPERSET_SECRET_KEY']
SQLALCHEMY_DATABASE_URI = 'sqlite:////app/superset_home/superset.db'
FEATURE_FLAGS = {'EMBEDDED_SUPERSET': True}
TALISMAN_ENABLED = False
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False
GUEST_ROLE_NAME = 'Gamma'
ENABLE_CORS = True
CORS_OPTIONS = {'supports_credentials': True, 'origins': ['http://127.0.0.1:8765', 'http://127.0.0.1:8000']}
