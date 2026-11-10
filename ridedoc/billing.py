"""Bill data model, GST/coupon calculation and Indian number formatting."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from . import config

ZERO = Decimal("0")


def q(value: Decimal) -> Decimal:
    """Round to paise."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))  # handles int, float, numpy numbers
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def now_ist() -> datetime:
    return datetime.now(config.IST)


GST_RATE = config.CGST_RATE + config.SGST_RATE  # 18%, same for every service


@dataclass
class LineItem:
    service: str
    rate: Decimal
    qty: int
    gst_rate: Decimal = GST_RATE
    # Set when the user typed the final amount (incl. GST) for a custom service:
    # the line total is then exactly this value and GST/taxable are derived from it.
    fixed_total: Decimal | None = None

    @property
    def amount(self) -> Decimal:
        """Taxable value (before GST)."""
        if self.fixed_total is not None:
            return q(self.fixed_total / (1 + self.gst_rate))
        return q(self.rate * self.qty)

    @property
    def gst(self) -> Decimal:
        if self.fixed_total is not None:
            return q(self.fixed_total) - self.amount
        return q(self.amount * self.gst_rate)

    @property
    def total(self) -> Decimal:
        """Line amount including GST."""
        return self.amount + self.gst


def rate_from_total(total: Decimal, qty: int, gst_rate: Decimal = GST_RATE) -> Decimal:
    """Per-unit rate (before GST) that gives `total` incl. GST for `qty` units."""
    return q(Decimal(total) / (1 + gst_rate) / max(qty, 1))


@dataclass
class BillTotals:
    subtotal: Decimal          # taxable value (before GST)
    cgst_rate: Decimal
    cgst: Decimal
    sgst_rate: Decimal
    sgst: Decimal
    gross_total: Decimal       # sum of line totals incl. GST
    coupon_code: str | None
    discount: Decimal
    grand_total: Decimal       # gross_total - discount


@dataclass
class Bill:
    invoice_no: str
    bill_date: date
    bill_time: str
    customer_name: str
    phone: str
    vehicle_model: str
    reg_no: str
    mileage: str
    segment: str
    payment_mode: str
    items: list[LineItem] = field(default_factory=list)
    totals: BillTotals | None = None


def lookup_coupon(code: str) -> Decimal | None:
    return config.COUPONS.get(code.strip().upper())


def compute_totals(items: list[LineItem], coupon_code: str | None = None) -> BillTotals:
    # GST is calculated per service line, then summed. CGST/SGST are kept
    # (half each) only for the records in the bill log.
    subtotal = q(sum((i.amount for i in items), ZERO))
    gst = q(sum((i.gst for i in items), ZERO))
    cgst = q(gst / 2)
    sgst = gst - cgst
    gross = subtotal + gst
    discount = ZERO
    if coupon_code:
        value = lookup_coupon(coupon_code)
        if value is not None:
            discount = q(min(value, gross))  # never below zero
    return BillTotals(
        subtotal=subtotal,
        cgst_rate=config.CGST_RATE,
        cgst=cgst,
        sgst_rate=config.SGST_RATE,
        sgst=sgst,
        gross_total=gross,
        coupon_code=coupon_code.upper() if coupon_code and discount > 0 else None,
        discount=discount,
        grand_total=gross - discount,
    )


def format_inr(value: Decimal) -> str:
    """1425000 -> '14,25,000.00' (Indian digit grouping)."""
    value = q(value)
    sign = "-" if value < 0 else ""
    int_part, dec_part = f"{abs(value):.2f}".split(".")
    if len(int_part) > 3:
        head, last3 = int_part[:-3], int_part[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        int_part = ",".join(groups) + "," + last3
    return f"{sign}{int_part}.{dec_part}"


def format_rate(rate: Decimal) -> str:
    """Decimal('0.09') -> '9%'."""
    return f"{(Decimal(rate) * 100).normalize():f}%"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")


def _three(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def _int_words(n: int) -> str:
    if n == 0:
        return "Zero"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    parts = []
    if crore:
        parts.append(f"{_int_words(crore)} Crore")
    if lakh:
        parts.append(f"{_two(lakh)} Lakh")
    if thousand:
        parts.append(f"{_two(thousand)} Thousand")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(value: Decimal) -> str:
    value = q(value)
    rupees = int(value)
    paise = int((value - rupees) * 100)
    text = f"Rupees {_int_words(rupees)}"
    if paise:
        text += f" and {_two(paise)} Paise"
    return text + " Only"
