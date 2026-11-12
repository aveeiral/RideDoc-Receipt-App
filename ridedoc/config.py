"""Central configuration: business details, file locations, tax rates, coupons."""
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"

# Price list (fixed file, read on every change of the file).
SERVICES_FILE = DATA_DIR / "services.xlsx"
# Log of every saved bill (created automatically on first save).
BILLS_LOG_FILE = DATA_DIR / "bills_log.xlsx"

# ---- Business details printed on the bill -------------------------------
BUSINESS_NAME = "RIDEDOC"
BUSINESS_CONTACT_LINE = "Mob.: 8960007787 ( Prayagraj, U.P. Pin Code : 211003 )"
BUSINESS_WEB_LINE = "www.ridedoc.in | ridedoc247@gmail.com"

# ---- Tax -----------------------------------------------------------------
CGST_RATE = Decimal("0.09")
SGST_RATE = Decimal("0.09")

# ---- Coupons: code -> flat amount (Rs.) off the final bill (after GST) ---
COUPONS: dict[str, Decimal] = {
    "REDUCE500": Decimal("500"),
}

# ---- Invoice numbering: RD-YYYYMMDD-001 ---------------------------------
INVOICE_PREFIX = "RD"

# ---- Vehicle segments ----------------------------------------------------
# Segment codes in the price sheet look like "1.10", "2.20":
#   part before the dot -> vehicle group, part after the dot -> variant.
GROUP_NAMES = {"1": "Hatchback", "2": "Sedan", "3": "MUV/SUV", "4": "Luxury"}
VARIANT_NAMES = {"10": "Entry", "20": "Regular"}

PAYMENT_MODES = ["Cash", "UPI", "Card"]

# India Standard Time (fixed offset; India has no daylight saving).
# Streamlit Cloud servers run on UTC, so always use this for dates/times.
IST = timezone(timedelta(hours=5, minutes=30))


def _asset(stem: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        p = ASSETS_DIR / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def logo_path() -> Path | None:
    """Logo printed top-left on the bill (assets/logo.png), or None."""
    return _asset("logo")


def qr_path() -> Path | None:
    """Payment QR code printed top-right on the bill (assets/qr.png), or None."""
    return _asset("qr")


QR_CAPTION = "Scan to Pay"

# ---- Bill History PIN -----------------------------------------------------
# Default PIN. To keep it out of GitHub, set it in secrets instead:
#   [app]
#   history_pin = "your-pin"
# (.streamlit/secrets.toml locally, App -> Settings -> Secrets on Streamlit Cloud)
DEFAULT_HISTORY_PIN = "8840324500"
PIN_MAX_ATTEMPTS = 5          # wrong tries before a short lock-out
PIN_LOCKOUT_SECONDS = 60


def history_pin() -> str:
    try:
        import streamlit as st
        return str(st.secrets["app"]["history_pin"]).strip()
    except Exception:  # no secrets file / no [app] section
        return DEFAULT_HISTORY_PIN
