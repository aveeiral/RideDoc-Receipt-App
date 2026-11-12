"""View saved bills, re-download any bill as PDF, export the whole log."""
import hmac
import time
from datetime import date

import pandas as pd
import streamlit as st

from ridedoc import config, storage
from ridedoc.billing import format_inr
from ridedoc.pdf import build_invoice_pdf
from ridedoc.ui import page_header, show_pdf_preview

S = st.session_state

page_header("Bill History")


def _require_pin() -> None:
    """Show a PIN form and stop the page until the correct PIN is entered (once per browser session)."""
    if S.get("hist_unlocked"):
        return
    locked_until = S.get("hist_locked_until", 0.0)
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("### 🔒 Enter PIN")
        st.caption("Bill History contains customer details and is protected by a PIN.")
        with st.form("hist_pin_form", clear_on_submit=True):
            pin = st.text_input("PIN", type="password", max_chars=20, placeholder="Enter PIN")
            submitted = st.form_submit_button("Unlock", type="primary", width="stretch")
        if submitted:
            now = time.time()
            if now < locked_until:
                st.error(f"Too many wrong attempts. Try again in {int(locked_until - now) + 1} seconds.")
            elif hmac.compare_digest(pin.strip().encode(), config.history_pin().encode()):
                S.hist_unlocked = True
                S.hist_fails = 0
                st.rerun()
            else:
                S.hist_fails = S.get("hist_fails", 0) + 1
                left = config.PIN_MAX_ATTEMPTS - S.hist_fails
                if left <= 0:
                    S.hist_locked_until = now + config.PIN_LOCKOUT_SECONDS
                    S.hist_fails = 0
                    st.error(f"Too many wrong attempts. Locked for {config.PIN_LOCKOUT_SECONDS} seconds.")
                else:
                    st.error(f"Wrong PIN. {left} attempt{'s' if left != 1 else ''} left.")
    st.stop()


def _lock() -> None:
    S.hist_unlocked = False


_require_pin()

try:
    df = storage.load_bills_df()
except Exception as e:  # no connection / corrupt file
    st.error(f"Could not read saved bills: {e}")
    st.stop()

s1, s2, s3 = st.columns([4, 1, 1], vertical_alignment="center")
url = storage.storage_url()
s1.caption(f"Bills are stored in **{storage.storage_name()}**" + (f" · [open sheet]({url})" if url else ""))
if s2.button("🔄 Refresh", width="stretch"):
    storage.clear_cache()
    st.rerun()
s3.button("🔒 Lock", on_click=_lock, width="stretch", help="Hide bill history until the PIN is entered again")

if df.empty:
    st.info("No bills saved yet. Bills appear here after you save them on the **New Bill** page.")
    st.stop()

# ---------------------------------------------------------------- filters --
f1, f2 = st.columns([2, 1])
search = f1.text_input("Search", placeholder="Invoice no., customer name, phone or registration no.")
dates = df["Date"].dropna()
min_d, max_d = (dates.min(), dates.max()) if not dates.empty else (date.today(), date.today())
date_range = f2.date_input("Date range", value=(min_d, max_d), format="DD/MM/YYYY")

view = df.copy()
if search.strip():
    s = search.strip().lower()
    cols = ["Invoice No", "Customer Name", "Phone", "Registration No"]
    mask = pd.Series(False, index=view.index)
    for c in cols:
        mask |= view[c].astype(str).str.lower().str.contains(s, regex=False, na=False)
    view = view[mask]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
    view = view[(view["Date"] >= start) & (view["Date"] <= end)]

view = view.sort_values(["Date", "Invoice No"], ascending=False)

m1, m2, m3 = st.columns(3)
m1.metric("Bills", len(view))
m2.metric("Revenue (incl. GST, after coupons)", f"₹{format_inr(view['Grand Total'].sum())}")
m3.metric("GST collected", f"₹{format_inr(view['CGST'].sum() + view['SGST'].sum())}")

money = st.column_config.NumberColumn(format="₹ %.2f")
st.dataframe(
    view[["Invoice No", "Date", "Time", "Customer Name", "Phone", "Vehicle Model", "Registration No",
          "Vehicle Segment", "Payment Mode", "Sub Total", "CGST", "SGST", "Coupon Code", "Discount",
          "Grand Total"]],
    hide_index=True,
    width="stretch",
    column_config={
        "Date": st.column_config.DateColumn(format="DD-MM-YYYY"),
        "Sub Total": money, "CGST": money, "SGST": money, "Discount": money, "Grand Total": money,
    },
)

st.download_button("⬇️ Download full bills log (Excel)", data=storage.export_excel_bytes(),
                   file_name="ridedoc_bills_log.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore")

# ---------------------------------------------------------- single bill --
st.divider()
st.markdown("#### View a bill")
if view.empty:
    st.info("No bills match the filters.")
    st.stop()

labels = {r["Invoice No"]: f"{r['Invoice No']}  ·  {r['Customer Name']}  ·  ₹{format_inr(r['Grand Total'])}"
          for _, r in view.iterrows()}
invoice = st.selectbox("Invoice", list(labels), format_func=labels.get)
bill = storage.load_bill(invoice)
if bill is None:
    st.error("Bill not found in the log.")
    st.stop()

pdf_bytes = build_invoice_pdf(bill)
b1, b2, _ = st.columns([1.3, 1.3, 4])
b1.download_button("⬇️ Download PDF", data=pdf_bytes, file_name=f"{bill.invoice_no}.pdf",
                   mime="application/pdf", type="primary", on_click="ignore", width="stretch")
preview = b2.toggle("👁️ Preview bill", value=True)

st.dataframe(
    pd.DataFrame([{"Service": i.service, "Rate": float(i.rate), "Qty": float(i.qty),
                   "GST": float(i.gst), "Amount": float(i.total)} for i in bill.items]),
    hide_index=True, width="stretch",
    column_config={"Rate": money, "GST": money, "Amount": money},
)
if preview:
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        show_pdf_preview(pdf_bytes)
