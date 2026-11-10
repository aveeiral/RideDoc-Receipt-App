"""Bill log storage: Google Sheets (permanent, online) or a local Excel file (fallback).

Google Sheets is used automatically when `.streamlit/secrets.toml` (or the Streamlit Cloud
"Secrets" setting) contains a [gcp_service_account] and a [google_sheets] section.
Otherwise bills go to data/bills_log.xlsx.

Both stores have the same two tabs:
  "Bills": one row per bill (header + totals)
  "Items": one row per service line, linked by Invoice No.
"""
from __future__ import annotations

import io
import os
import tempfile
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from . import config
from .billing import GST_RATE, Bill, BillTotals, LineItem, q

BILL_HEADERS = [
    "Invoice No", "Date", "Time", "Customer Name", "Phone", "Vehicle Model",
    "Registration No", "Mileage (km)", "Vehicle Segment", "Payment Mode",
    "Sub Total", "CGST %", "CGST", "SGST %", "SGST", "Total incl. GST",
    "Coupon Code", "Discount", "Grand Total", "No. of Services",
]
ITEM_HEADERS = ["Invoice No", "S.No", "Service", "Rate", "Qty", "Amount", "GST %", "GST", "Total"]
NUMERIC_BILL_COLS = ["Sub Total", "CGST %", "CGST", "SGST %", "SGST", "Total incl. GST",
                     "Discount", "Grand Total", "No. of Services"]
TEXT_BILL_COLS = ["Invoice No", "Time", "Customer Name", "Phone", "Vehicle Model", "Registration No",
                  "Mileage (km)", "Vehicle Segment", "Payment Mode", "Coupon Code"]
DATE_FMT = "%d-%m-%Y"
CACHE_TTL_SECONDS = 60  # limits Google API calls while users click around

_lock = threading.Lock()


class StorageError(Exception):
    pass


# =========================================================== backends ====
class _ExcelBackend:
    name = "local Excel file"

    def __init__(self, path: Path):
        self.path = path
        self.url = None

    def _open(self) -> Workbook:
        if self.path.exists():
            wb = load_workbook(self.path)
            for tab, headers in (("Bills", BILL_HEADERS), ("Items", ITEM_HEADERS)):
                for i, h in enumerate(headers, start=1):  # add any new columns to old logs
                    if wb[tab].cell(1, i).value in (None, ""):
                        wb[tab].cell(1, i, h).font = Font(bold=True, color="FFFFFF")
                        wb[tab].cell(1, i).fill = PatternFill("solid", fgColor="1F5C99")
            return wb
        wb = Workbook()
        wb.active.title = "Bills"
        wb.create_sheet("Items")
        for ws, headers in ((wb["Bills"], BILL_HEADERS), (wb["Items"], ITEM_HEADERS)):
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F5C99")
            ws.freeze_panes = "A2"
        return wb

    def read(self, tab: str) -> list[list]:
        if not self.path.exists():
            return []
        wb = load_workbook(self.path, read_only=True, data_only=True)
        try:
            return [list(r) for r in wb[tab].iter_rows(min_row=2, values_only=True) if r and r[0]]
        finally:
            wb.close()

    def append(self, bill_row: list, item_rows: list[list]) -> None:
        wb = self._open()
        wb["Bills"].append(bill_row)
        wb["Bills"].cell(wb["Bills"].max_row, 5).number_format = "@"  # keep phone as text
        for r in item_rows:
            wb["Items"].append(r)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=self.path.parent)
        os.close(fd)
        try:
            wb.save(tmp)
            try:
                os.replace(tmp, self.path)
            except PermissionError as e:
                raise StorageError("The bills log is open in another program (e.g. Excel). "
                                   "Close it and try again.") from e
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


class _SheetsBackend:
    name = "Google Sheets"

    def __init__(self, spreadsheet: str):
        import gspread

        from . import gsheets

        self._gspread = gspread
        try:
            sh = gsheets.open_spreadsheet(spreadsheet)
        except RuntimeError as e:
            raise StorageError(str(e)) from e
        self.url = sh.url
        self.tabs = {"Bills": self._tab(sh, "Bills", BILL_HEADERS),
                     "Items": self._tab(sh, "Items", ITEM_HEADERS)}

    def _tab(self, sh, title: str, headers: list[str]):
        try:
            ws = sh.worksheet(title)
        except self._gspread.WorksheetNotFound:
            first = sh.sheet1
            if first.title in ("Sheet1", "Sheet 1") and not first.get_all_values():
                first.update_title(title)  # reuse the blank default tab
                ws = first
            else:
                ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        current = ws.row_values(1)
        if current != headers and headers[:len(current)] == current:  # empty or older layout
            if ws.col_count < len(headers):
                ws.add_cols(len(headers) - ws.col_count)
            ws.update([headers], "A1")
            ws.format("1:1", {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                              "backgroundColor": {"red": 0.12, "green": 0.36, "blue": 0.6}})
            ws.freeze(rows=1)
        return ws

    def read(self, tab: str) -> list[list]:
        rows = self.tabs[tab].get_all_values(value_render_option="UNFORMATTED_VALUE")
        return [r for r in rows[1:] if r and r[0] != ""]

    def append(self, bill_row: list, item_rows: list[list]) -> None:
        # Items first: if the second call fails, no half-saved bill shows up in "Bills".
        self.tabs["Items"].append_rows(item_rows, value_input_option="RAW", table_range="A1")
        self.tabs["Bills"].append_row(bill_row, value_input_option="RAW", table_range="A1")


_backend_instance = None
_cache: dict[str, tuple[float, list[list]]] = {}


def _backend():
    from . import gsheets

    global _backend_instance
    if _backend_instance is None:
        with _lock:
            if _backend_instance is None:
                sheet = gsheets.setting("spreadsheet") if gsheets.is_configured() else None
                if sheet:
                    try:
                        _backend_instance = _SheetsBackend(sheet)
                    except StorageError:
                        raise
                    except Exception as e:
                        raise StorageError(f"Could not connect to Google Sheets: {e}") from e
                else:
                    _backend_instance = _ExcelBackend(config.BILLS_LOG_FILE)
    return _backend_instance


def storage_name() -> str:
    return _backend().name


def storage_url() -> str | None:
    return _backend().url


def clear_cache() -> None:
    _cache.clear()


def _rows(tab: str, fresh: bool = False) -> list[list]:
    hit = _cache.get(tab)
    if not fresh and hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    rows = _backend().read(tab)
    _cache[tab] = (time.time(), rows)
    return rows


# ============================================================ helpers ====
def _pad(row: list, n: int) -> list:
    return list(row) + [None] * (n - len(row))


def _dec(v) -> Decimal:
    return q(Decimal(str(v if v not in (None, "") else 0)))


def _to_date(v) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):  # spreadsheet serial date
        return date(1899, 12, 30) + timedelta(days=int(v))
    for fmt in (DATE_FMT, "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _next_number(existing: list, d: date) -> str:
    prefix = f"{config.INVOICE_PREFIX}-{d:%Y%m%d}-"
    seqs = [int(x[len(prefix):]) for x in map(_str, existing)
            if x.startswith(prefix) and x[len(prefix):].isdigit()]
    return f"{prefix}{(max(seqs) + 1) if seqs else 1:03d}"


# ========================================================= public API ====
def next_invoice_no(d: date) -> str:
    """Provisional number for previews. The final one is assigned in save_bill."""
    return _next_number([r[0] for r in _rows("Bills")], d)


def save_bill(bill: Bill) -> Bill:
    """Assign the final invoice number, store the bill, and return the saved bill."""
    t = bill.totals
    with _lock:
        existing = [r[0] for r in _rows("Bills", fresh=True)]
        bill = replace(bill, invoice_no=_next_number(existing, bill.bill_date))
        bill_row = [
            bill.invoice_no, bill.bill_date.strftime(DATE_FMT), bill.bill_time, bill.customer_name,
            bill.phone, bill.vehicle_model, bill.reg_no, bill.mileage, bill.segment, bill.payment_mode,
            float(t.subtotal), float(t.cgst_rate * 100), float(t.cgst),
            float(t.sgst_rate * 100), float(t.sgst), float(t.gross_total),
            t.coupon_code or "", float(t.discount), float(t.grand_total), len(bill.items),
        ]
        item_rows = [[bill.invoice_no, n, i.service, float(i.rate), i.qty, float(i.amount),
                      float(i.gst_rate * 100), float(i.gst), float(i.total)]
                     for n, i in enumerate(bill.items, start=1)]
        try:
            _backend().append(bill_row, item_rows)
        except StorageError:
            raise
        except Exception as e:
            raise StorageError(f"Could not save the bill: {e}") from e
        finally:
            clear_cache()
    return bill


def load_bills_df() -> pd.DataFrame:
    rows = [_pad(r, len(BILL_HEADERS))[:len(BILL_HEADERS)] for r in _rows("Bills")]
    df = pd.DataFrame(rows, columns=BILL_HEADERS)
    if df.empty:
        return df
    for c in NUMERIC_BILL_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    for c in TEXT_BILL_COLS:
        df[c] = df[c].map(_str)
    df["Date"] = df["Date"].map(_to_date)
    return df


def load_bill(invoice_no: str) -> Bill | None:
    record = next((dict(zip(BILL_HEADERS, _pad(r, len(BILL_HEADERS))))
                   for r in _rows("Bills") if _str(r[0]) == invoice_no), None)
    if record is None:
        return None
    items = [LineItem(service=_str(r[2]), rate=_dec(r[3]), qty=int(float(r[4] or 0)),
                      gst_rate=(Decimal(str(r[6])) / 100) if r[6] not in (None, "") else GST_RATE)
             for r in (_pad(x, len(ITEM_HEADERS)) for x in _rows("Items"))
             if _str(r[0]) == invoice_no]
    totals = BillTotals(
        subtotal=_dec(record["Sub Total"]),
        cgst_rate=Decimal(str(record["CGST %"] or 0)) / 100,
        cgst=_dec(record["CGST"]),
        sgst_rate=Decimal(str(record["SGST %"] or 0)) / 100,
        sgst=_dec(record["SGST"]),
        gross_total=_dec(record["Total incl. GST"]),
        coupon_code=_str(record["Coupon Code"]) or None,
        discount=_dec(record["Discount"]),
        grand_total=_dec(record["Grand Total"]),
    )
    return Bill(
        invoice_no=invoice_no,
        bill_date=_to_date(record["Date"]) or date.today(),
        bill_time=_str(record["Time"]),
        customer_name=_str(record["Customer Name"]),
        phone=_str(record["Phone"]),
        vehicle_model=_str(record["Vehicle Model"]),
        reg_no=_str(record["Registration No"]),
        mileage=_str(record["Mileage (km)"]),
        segment=_str(record["Vehicle Segment"]),
        payment_mode=_str(record["Payment Mode"]),
        items=items,
        totals=totals,
    )


def export_excel_bytes() -> bytes:
    """Whole log (both tabs) as an .xlsx download, whichever store is in use."""
    wb = Workbook()
    wb.active.title = "Bills"
    wb.create_sheet("Items")
    for tab, headers in (("Bills", BILL_HEADERS), ("Items", ITEM_HEADERS)):
        ws = wb[tab]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in _rows(tab):
            ws.append([("" if v is None else v) for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
