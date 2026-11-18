# CloudSync Pro

A Flask-based file dashboard with version history, sync status, and PDF replacement uploads.

## Setup

1. Create a virtual environment and install dependencies with `pip install -r requirements.txt`.
2. Set `SECRET_KEY` to a persistent random value before deployment. If omitted, the app generates a temporary key at startup, which invalidates sessions on restart.
3. Run `python app.py`.

For Vercel, use Python 3.11 or newer and connect a Postgres database and a private Blob store to the project. The app reads `DATABASE_URL` (or Vercel's `POSTGRES_URL_NON_POOLING`/`POSTGRES_URL`) and the Blob store's `BLOB_READ_WRITE_TOKEN` (or `BLOB_STORE_ID` when using Vercel OIDC). It stores file content in private Blob objects and uses `/tmp` only for short-lived upload staging.

Outside Vercel, the local SQLite database and uploads are stored under `data/`, which is intentionally excluded from Git.

Run the test suite with `python -m unittest tests.test_suite`.
