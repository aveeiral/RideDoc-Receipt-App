"""Reads the service price list from the Excel sheet.

Expected layout (same as the RideDoc rate card):

    Row with groups :  Vehicle segment | Group 1 (merged) |      | Group 2 ...
    Row with codes  :                  | 1.10             | 1.20 | 2.10 ...
    Category row    :  Standard Services   (no prices)
    Service rows    :  Diagnostic Scanning | 675.00 | 675.00 | ...

Prices may be real numbers or numbers stored as text ("1,425.00").
Any other text (e.g. "As per man hour consumption") means the service has no
fixed price for that segment, so it is not offered for that segment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

from . import config

_CODE_RE = re.compile(r"^\d+\.\d+$")


@dataclass(frozen=True)
class Segment:
    code: str      # "1.10"
    group: str     # "Hatchback"
    variant: str   # "Entry"

    @property
    def label(self) -> str:
        return f"{self.group} - {self.variant} ({self.code})"


@dataclass
class Service:
    name: str
    category: str
    prices: dict[str, Decimal | None] = field(default_factory=dict)  # code -> price
    notes: dict[str, str] = field(default_factory=dict)              # code -> text


@dataclass
class Catalog:
    segments: list[Segment]
    services: list[Service]

    @property
    def groups(self) -> list[str]:
        seen: list[str] = []
        for s in self.segments:
            if s.group not in seen:
                seen.append(s.group)
        return seen

    def variants_for(self, group: str) -> list[str]:
        return [s.variant for s in self.segments if s.group == group]

    def segment(self, group: str, variant: str) -> Segment:
        for s in self.segments:
            if s.group == group and s.variant == variant:
                return s
        raise KeyError(f"No segment for {group} / {variant}")

    def priced_services(self, code: str) -> list[tuple[Service, Decimal]]:
        return [(s, s.prices[code]) for s in self.services if s.prices.get(code) is not None]

    def unpriced_services(self, code: str) -> list[tuple[Service, str]]:
        return [(s, s.notes.get(code, "no price")) for s in self.services if s.prices.get(code) is None]


class CatalogError(Exception):
    pass


def _code_of(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None  # codes are always like 1.10, never whole numbers
    if isinstance(value, float):
        text = f"{value:.2f}"
    else:
        text = str(value).strip()
    if not _CODE_RE.match(text):
        return None
    return f"{Decimal(text):.2f}"


def _parse_price(value) -> tuple[Decimal | None, str | None]:
    """Return (price, note). Blank -> (None, None); text -> (None, text)."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value)), None
    text = str(value).strip()
    if not text:
        return None, None
    cleaned = text.replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    try:
        return Decimal(cleaned), None
    except InvalidOperation:
        return None, " ".join(text.split())


def load_catalog(path: Path = config.SERVICES_FILE) -> Catalog:
    """Read the price list from a local Excel file (first sheet)."""
    if not path.exists():
        raise CatalogError(f"Price list not found: {path}")
    wb = load_workbook(path, data_only=True, read_only=False)
    try:
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()
    return parse_rows(rows)


def load_catalog_from_sheet(spreadsheet: str, tab: str | None = None) -> Catalog:
    """Read the price list from Google Sheets (same layout as the Excel rate card)."""
    from . import gsheets

    try:
        sh = gsheets.open_spreadsheet(spreadsheet)
        if tab:
            ws = sh.worksheet(tab)
        else:  # first tab that isn't one of the bill-log tabs
            ws = next((w for w in sh.worksheets() if w.title not in ("Bills", "Items")), None)
            if ws is None:
                raise CatalogError("The price list Google Sheet has no rate tab.")
        rows = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    except CatalogError:
        raise
    except Exception as e:
        raise CatalogError(f"Could not read the price list from Google Sheets: {e}") from e
    return parse_rows(rows)


def parse_rows(rows: list[list]) -> Catalog:
    # Locate the header row containing the segment codes (1.10, 1.20, ...).
    header_idx = None
    for i, row in enumerate(rows[:15]):
        if sum(1 for v in row[1:] if _code_of(v)) >= 1:
            header_idx = i
            break
    if header_idx is None:
        raise CatalogError("Could not find the segment code row (e.g. 1.10, 1.20) in the price list.")

    code_cols: dict[int, str] = {}
    for col, v in enumerate(rows[header_idx]):
        if col == 0:
            continue
        code = _code_of(v)
        if code:
            code_cols[col] = code

    # Group labels from the row above (merged cells -> forward fill).
    group_labels: dict[int, str] = {}
    if header_idx > 0:
        current = None
        for col, v in enumerate(rows[header_idx - 1]):
            if col == 0:
                continue
            if v not in (None, ""):
                current = str(v).strip()
            group_labels[col] = current

    segments: list[Segment] = []
    for col, code in code_cols.items():
        major, minor = code.split(".")
        group = config.GROUP_NAMES.get(major) or group_labels.get(col) or f"Group {major}"
        variant = config.VARIANT_NAMES.get(minor, f"Variant {minor}")
        segments.append(Segment(code=code, group=group, variant=variant))

    services: list[Service] = []
    category = "Services"
    for row in rows[header_idx + 1:]:
        name = row[0] if row else None
        if name is None or not str(name).strip():
            continue
        name = " ".join(str(name).split())
        cells = {code: (row[col] if col < len(row) else None) for col, code in code_cols.items()}
        if all(v is None or str(v).strip() == "" for v in cells.values()):
            category = name  # category heading row
            continue
        svc = Service(name=name, category=category)
        for code, raw in cells.items():
            price, note = _parse_price(raw)
            svc.prices[code] = price
            if note:
                svc.notes[code] = note
        services.append(svc)

    # Make names unique (needed for the dropdown) if a name repeats across categories.
    counts: dict[str, int] = {}
    for s in services:
        counts[s.name] = counts.get(s.name, 0) + 1
    for s in services:
        if counts[s.name] > 1:
            s.name = f"{s.name} ({s.category})"

    if not services:
        raise CatalogError("No services found in the price list.")
    return Catalog(segments=segments, services=services)
