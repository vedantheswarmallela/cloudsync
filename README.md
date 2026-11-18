# CloudSync Pro

A Flask-based file dashboard with version history, sync status, and PDF replacement uploads.

## Setup

1. Create a virtual environment and install dependencies with `pip install -r requirements.txt`.
2. Set `SECRET_KEY` to a persistent random value before deployment. If omitted, the app generates a temporary key at startup, which invalidates sessions on restart.
3. Run `python app.py`.

The local database and uploads are stored under `data/` and are intentionally excluded from Git.

Run the test suite with `python -m unittest tests.test_suite`.
