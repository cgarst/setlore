# Agent Guidelines & Project Instructions

## Testing & Automation Guidelines
- **Screenshot & Demo Builder**: Do NOT automatically run the screenshot and demo builder scripts (or tests/tools that invoke them). Only run screenshot generation and the static demo builder when explicitly requested by the user.
- **Running Tests**: Run targeted tests (e.g. `python3 manage.py test tests.test_django_app` or specific test files/classes) for verification during regular development to avoid spinning up long-running live server screenshot processes.

## Development & Architecture Guidelines
- **Zero-Hardcoding Policy**: Keep artist, venue, musician, and concert data fully dynamic. Do not hardcode specific band names or metadata in core application logic.
