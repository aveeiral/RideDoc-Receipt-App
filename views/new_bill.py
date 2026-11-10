"""Create a new bill: customer details -> services -> coupon -> preview / save / download."""
import re
from decimal import Decimal

import streamlit as st

from ridedoc import config, storage
from ridedoc.billing import GST_RATE, Bill, LineItem, compute_totals, format_inr, format_rate, lookup_coupon, now_ist
from ridedoc.catalog import CatalogError
from ridedoc.pdf import build_invoice_pdf
from ridedoc.ui import get_catalog, page_header, price_list_source, reload_prices, show_pdf_preview

S = st.session_state  # every key used on this page starts with "nb_"


def _reset_bill() -> None:
    for key in [k for k in S.keys() if str(k).startswith("nb_")]:
        del S[key]


def _apply_coupon() -> None:
    code = S.get("nb_coupon_input", "").strip().upper()
    if not code:
        S.nb_coupon_msg = "Please enter a coupon code."
    elif lookup_coupon(code) is None:
        S.nb_coupon_msg = f"'{code}' is not a valid coupon code."
    else:
        S.nb_coupon = code
        S.nb_coupon_msg = None


def _add_custom_row() -> None:
    S.nb_custom_next = S.get("nb_custom_next", 0) + 1
    S.nb_custom_ids = S.get("nb_custom_ids", []) + [S.nb_custom_next]


def _remove_custom_row(cid: int) -> None:
    S.nb_custom_ids = [i for i in S.get("nb_custom_ids", []) if i != cid]
    for k in (f"nb_cname_{cid}", f"nb_crate_{cid}", f"nb_cqty_{cid}"):
        S.pop(k, None)


def _remove_coupon() -> None:
    S.nb_coupon = None
    S.nb_coupon_input = ""


page_header("New Bill")

try:
    catalog = get_catalog()
except CatalogError as e:
    st.error(f"Could not read the price list: {e}")
    st.button("🔄 Try again", on_click=reload_prices)
    st.stop()

saved: Bill | None = S.get("nb_saved_bill")
locked = saved is not None

# ---------------------------------------------------------------- details --
st.markdown("#### 1. Customer & Vehicle Details")
c1, c2, c3 = st.columns(3)
name = c1.text_input("Customer Name *", key="nb_name", disabled=locked)
phone = c2.text_input("Phone No. *", key="nb_phone", max_chars=10, placeholder="10-digit mobile", disabled=locked)
bill_date = c3.date_input("Bill Date", value=now_ist().date(), key="nb_date", format="DD/MM/YYYY", disabled=locked)

c4, c5, c6 = st.columns(3)
model = c4.text_input("Vehicle Model", key="nb_model", placeholder="e.g. Maruti Swift", disabled=locked)
reg_no = c5.text_input("Registration No.", key="nb_reg", placeholder="e.g. UP70 AB 1234", disabled=locked)
mileage = c6.text_input("Mileage (km)", key="nb_mileage", disabled=locked)

c7, c8, c9 = st.columns(3)
group = c7.selectbox("Vehicle Category *", catalog.groups, key="nb_group", disabled=locked)
variant = c8.radio("Variant *", catalog.variants_for(group), key="nb_variant", horizontal=True, disabled=locked)
payment = c9.selectbox("Payment Mode", config.PAYMENT_MODES, key="nb_payment", disabled=locked)
segment = catalog.segment(group, variant)
st.caption(f"Rates applied for: **{segment.label}**")

# --------------------------------------------------------------- services --
h1, h2 = st.columns([5, 1], vertical_alignment="bottom")
h1.markdown("#### 2. Services")
h1.caption(f"Prices from {price_list_source()}")
h2.button("🔄 Reload prices", on_click=reload_prices, disabled=locked, width="stretch",
          help="Fetch the latest prices now (otherwise they refresh automatically every few minutes).")
price_map = {svc.name: price for svc, price in catalog.priced_services(segment.code)}

# Drop services that have no fixed price in a newly chosen segment.
if "nb_services" in S:
    S.nb_services = [n for n in S.nb_services if n in price_map]

selected = st.multiselect(
    "Select services",
    options=list(price_map),
    format_func=lambda n: f"{n}  —  ₹{format_inr(price_map[n])}",
    key="nb_services",
    placeholder="Search and select services…",
    disabled=locked,
)
unpriced = catalog.unpriced_services(segment.code)
if unpriced:
    st.caption("Not available at a fixed rate for this segment: "
               + "; ".join(f"{svc.name} ({note})" for svc, note in unpriced))

items: list[LineItem] = []
custom_errors: list[str] = []
custom_ids: list[int] = S.setdefault("nb_custom_ids", [])
WIDTHS = [0.5, 4.2, 1.5, 1.3, 1.5, 1.6, 0.5]

if selected or custom_ids:
    head = st.columns(WIDTHS)
    labels = ["**#**", "**Service**", "**Rate (₹)**", "**Quantity**",
              f"**GST @ {format_rate(GST_RATE)} (₹)**", "**Amount (₹)**", ""]
    for col, label in zip(head, labels):
        col.markdown(label)

n = 0
# Services from the price list: rate is fixed by the sheet.
for svc_name in selected:
    n += 1
    cols = st.columns(WIDTHS, vertical_alignment="center")
    rate = price_map[svc_name]
    cols[0].write(n)
    cols[1].write(svc_name)
    cols[2].write(format_inr(rate))
    qty = cols[3].number_input("Quantity", min_value=1, max_value=999, step=1, value=1,
                               key=f"nb_qty_{svc_name}", label_visibility="collapsed", disabled=locked)
    item = LineItem(service=svc_name, rate=rate, qty=int(qty))
    items.append(item)
    cols[4].write(format_inr(item.gst))
    cols[5].write(format_inr(item.total))

# Custom services: name and rate typed by the user.
for cid in custom_ids:
    n += 1
    cols = st.columns(WIDTHS, vertical_alignment="center")
    cols[0].write(n)
    cname = cols[1].text_input("Custom service name", key=f"nb_cname_{cid}", placeholder="Custom service name",
                               label_visibility="collapsed", disabled=locked).strip()
    crate = cols[2].number_input("Rate", min_value=0.0, max_value=10_000_000.0, step=50.0, value=None,
                                 format="%.2f", placeholder="Rate", key=f"nb_crate_{cid}",
                                 label_visibility="collapsed", disabled=locked)
    cqty = cols[3].number_input("Quantity", min_value=1, max_value=999, step=1, value=1,
                                key=f"nb_cqty_{cid}", label_visibility="collapsed", disabled=locked)
    cols[6].button("🗑️", key=f"nb_cdel_{cid}", on_click=_remove_custom_row, args=(cid,),
                   help="Remove this row", disabled=locked)
    if crate is not None and crate > 0:
        item = LineItem(service=cname or "", rate=Decimal(str(round(crate, 2))), qty=int(cqty))
        cols[4].write(format_inr(item.gst))
        cols[5].write(format_inr(item.total))
        if cname:
            items.append(item)
        else:
            custom_errors.append(f"Row {n}: enter a name for the custom service.")
    else:
        cols[4].write("—")
        cols[5].write("—")
        if cname:
            custom_errors.append(f"Row {n}: enter a rate for “{cname}”.")

st.button("➕ Add custom service", on_click=_add_custom_row, disabled=locked,
          help="Adds a blank row where you can type any service name and rate.")
if not selected and not custom_ids:
    st.info("Select services from the list above, or add a custom service.")

# ------------------------------------------------------- coupon + totals --
left, right = st.columns([1, 1], gap="large")
with left:
    st.markdown("#### 3. Coupon")
    applied = S.get("nb_coupon")
    if applied:
        st.success(f"Coupon **{applied}** applied: ₹{format_inr(lookup_coupon(applied))} off the final bill.")
        st.button("Remove coupon", on_click=_remove_coupon, disabled=locked)
    else:
        cc1, cc2 = st.columns([3, 1], vertical_alignment="bottom")
        cc1.text_input("Coupon code", key="nb_coupon_input", placeholder="Enter code", disabled=locked)
        cc2.button("Apply", on_click=_apply_coupon, disabled=locked, width="stretch")
        if S.get("nb_coupon_msg"):
            st.error(S.nb_coupon_msg)

totals = compute_totals(items, S.get("nb_coupon"))
with right:
    st.markdown("#### 4. Bill Summary")
    rows = [
        ("**Sub Total (incl. GST)**", f"**{format_inr(totals.gross_total)}**"),
    ]
    if totals.discount > 0:
        rows.append((f"Coupon ({totals.coupon_code})", f"− {format_inr(totals.discount)}"))
    table = "| | ₹ |\n|:--|--:|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)
    st.markdown(table)
    st.markdown(f"### Grand Total: ₹{format_inr(totals.grand_total)}")

# ---------------------------------------------------------------- actions --
st.divider()


def _validate() -> list[str]:
    errors = []
    if not name.strip():
        errors.append("Customer name is required.")
    if not re.fullmatch(r"\d{10}", phone.strip()):
        errors.append("Phone number must be exactly 10 digits.")
    errors.extend(custom_errors)
    if not items and not custom_errors:
        errors.append("Add at least one service.")
    return errors


if locked:
    bill = saved
else:
    now = now_ist()
    try:
        provisional_no = storage.next_invoice_no(bill_date)
    except Exception as e:
        provisional_no = f"{config.INVOICE_PREFIX}-{bill_date:%Y%m%d}-???"
        st.warning(f"Bill storage is not reachable right now: {e}")
    bill = Bill(
        invoice_no=provisional_no,
        bill_date=bill_date,
        bill_time=now.strftime("%I:%M %p"),
        customer_name=name.strip(),
        phone=phone.strip(),
        vehicle_model=model.strip(),
        reg_no=reg_no.strip().upper(),
        mileage=mileage.strip(),
        segment=segment.label,
        payment_mode=payment,
        items=items,
        totals=totals,
    )

a1, a2, a3, _ = st.columns([1.3, 1.3, 1.3, 3])
show_preview = a1.toggle("👁️ Preview bill", key="nb_preview", disabled=not bill.items)

if locked:
    st.success(f"Bill **{saved.invoice_no}** saved to {storage.storage_name()}.")
    a2.download_button(
        "⬇️ Download PDF",
        data=build_invoice_pdf(saved),
        file_name=f"{saved.invoice_no}.pdf",
        mime="application/pdf",
        type="primary",
        on_click="ignore",
        width="stretch",
    )
    a3.button("🧾 Start new bill", on_click=_reset_bill, width="stretch")
else:
    if a2.button("💾 Save bill", type="primary", width="stretch"):
        errors = _validate()
        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                with st.spinner("Saving bill…"):
                    S.nb_saved_bill = storage.save_bill(bill)
                st.rerun()
            except storage.StorageError as e:
                st.error(f"Bill NOT saved. {e}")
    a3.caption("Save the bill to get the final invoice number and download the PDF.")

if show_preview and bill.items:
    if not locked:
        st.caption(f"Preview: invoice number **{bill.invoice_no}** is provisional until the bill is saved.")
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        show_pdf_preview(build_invoice_pdf(bill))
