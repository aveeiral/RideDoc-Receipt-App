"""Google Sheets connection shared by the price list and the bill log.

Settings come from `.streamlit/secrets.toml` (locally) or App -> Settings -> Secrets
(Streamlit Cloud):

    [google_sheets]
    spreadsheet          = "<URL of the bills sheet>"
    services_spreadsheet = "<URL of the price list sheet>"   # may be the same URL
    services_tab         = "Rates"                           # optional

    [gcp_service_account]
    ... values from the service account JSON key ...
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_client = None


def _secrets() -> dict:
    try:
        import streamlit as st
        return {k: st.secrets[k] for k in ("google_sheets", "gcp_service_account") if k in st.secrets}
    except Exception:  # no secrets file / not running under Streamlit
        return {}


def setting(name: str) -> str | None:
    value = _secrets().get("google_sheets", {}).get(name)
    return str(value).strip() if value else None


def is_configured() -> bool:
    return "gcp_service_account" in _secrets()


def service_account_email() -> str | None:
    return _secrets().get("gcp_service_account", {}).get("client_email")


def client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                import gspread
                _client = gspread.service_account_from_dict(dict(_secrets()["gcp_service_account"]))
    return _client


def open_spreadsheet(ref: str):
    """Open by full URL or by spreadsheet key; explain the usual sharing mistake."""
    import gspread

    try:
        return client().open_by_url(ref) if ref.startswith("http") else client().open_by_key(ref)
    except (gspread.SpreadsheetNotFound, PermissionError) as e:
        raise RuntimeError(
            f"Google Sheet not found or not shared. Share it with {service_account_email()} as Editor."
        ) from e
    except gspread.exceptions.APIError as e:
        if "403" in str(e):
            raise RuntimeError(
                f"No access to the Google Sheet. Share it with {service_account_email()} as Editor, "
                "and make sure the Google Sheets and Google Drive APIs are enabled."
            ) from e
        raise
