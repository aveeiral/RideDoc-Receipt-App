"""Shared Streamlit helpers."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from . import config, gsheets
from .catalog import Catalog, load_catalog, load_catalog_from_sheet
from .pdf import render_pdf_images

PRICE_REFRESH_SECONDS = 300  # Google Sheet price edits show up within 5 minutes


@st.cache_data(show_spinner=False)
def _excel_catalog(path: str, mtime: float) -> Catalog:
    return load_catalog(Path(path))


@st.cache_data(show_spinner="Loading price list…", ttl=PRICE_REFRESH_SECONDS)
def _sheet_catalog(spreadsheet: str, tab: str | None) -> Catalog:
    return load_catalog_from_sheet(spreadsheet, tab)


def _price_sheet() -> str | None:
    return gsheets.setting("services_spreadsheet") if gsheets.is_configured() else None


def price_list_source() -> str:
    return "Google Sheets" if _price_sheet() else "local Excel file (data/services.xlsx)"


def get_catalog() -> Catalog:
    """Price list from Google Sheets if configured, else the local Excel file."""
    sheet = _price_sheet()
    if sheet:
        return _sheet_catalog(sheet, gsheets.setting("services_tab"))
    p = config.SERVICES_FILE
    return _excel_catalog(str(p), p.stat().st_mtime if p.exists() else 0.0)


def reload_prices() -> None:
    _sheet_catalog.clear()
    _excel_catalog.clear()


def page_header(subtitle: str) -> None:
    logo = config.logo_path()
    if logo:
        left, right = st.columns([1, 4], vertical_alignment="center")
        left.image(str(logo), width=160)
        box = right
    else:
        box = st.container()
    box.markdown(
        f"<h1 style='margin-bottom:0;letter-spacing:2px'>{config.BUSINESS_NAME}</h1>"
        f"<div style='font-weight:600'>{config.BUSINESS_CONTACT_LINE}</div>"
        f"<div style='font-weight:600'>{config.BUSINESS_WEB_LINE}</div>",
        unsafe_allow_html=True,
    )
    st.subheader(subtitle)


@st.cache_data(show_spinner=False, max_entries=20)
def _preview_images(pdf_bytes: bytes):
    return render_pdf_images(pdf_bytes)


def show_pdf_preview(pdf_bytes: bytes) -> None:
    for img in _preview_images(pdf_bytes):
        with st.container(border=True):
            st.image(img, width="stretch")
