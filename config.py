import os
import secrets
import tempfile

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
IS_VERCEL = os.environ.get('VERCEL', '').lower() in {'1', 'true'}
POSTGRES_URL = (
    os.environ.get('DATABASE_URL')
    or os.environ.get('POSTGRES_URL_NON_POOLING')
    or os.environ.get('POSTGRES_URL')
)
if POSTGRES_URL and POSTGRES_URL.startswith('postgres://'):
    POSTGRES_URL = POSTGRES_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif POSTGRES_URL and POSTGRES_URL.startswith('postgresql://'):
    POSTGRES_URL = POSTGRES_URL.replace('postgresql://', 'postgresql+psycopg://', 1)

if IS_VERCEL and not POSTGRES_URL:
    raise RuntimeError('Set DATABASE_URL or connect Vercel Postgres before deploying.')

if IS_VERCEL and not (
    os.environ.get('BLOB_READ_WRITE_TOKEN') or os.environ.get('BLOB_STORE_ID')
):
    raise RuntimeError('Connect Vercel Blob before deploying.')

DATA_DIR = (
    os.path.join(tempfile.gettempdir(), 'cloudsync')
    if IS_VERCEL
    else os.path.join(BASE_DIR, 'data')
)

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    IS_VERCEL = IS_VERCEL
    USE_VERCEL_BLOB = IS_VERCEL
    DATA_DIR = DATA_DIR
    UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')
    TEMP_FOLDER = os.path.join(DATA_DIR, 'tmp')
    SQLALCHEMY_DATABASE_URI = POSTGRES_URL or f"sqlite:///{os.path.join(DATA_DIR, 'cloudsync.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max per upload
    DEFAULT_USER_QUOTA = 5 * 1024 * 1024 * 1024  # 5 GB in bytes
    DEFAULT_ADMIN_QUOTA = 50 * 1024 * 1024 * 1024  # 50 GB in bytes
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'data', 'test_uploads')
