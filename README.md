# RideDoc Billing App

Streamlit app to create RideDoc GST bills, preview them, download them as PDF, and keep a log of every bill in Excel.

## Run locally

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## Files you maintain

| File | What it is |
|---|---|
| `data/services.xlsx` or Google Sheet | Price list (Google Sheet when `services_spreadsheet` is set in secrets; refreshes every 5 min or via **Reload prices**). Same layout as the rate card: row 1 = groups, row 2 = segment codes (1.10, 1.20 …), column A = service names, a row with no prices = category heading. Text such as "As per man hour consumption" means the service is not offered at a fixed price for that segment. The app reloads automatically when this file changes. |
| `assets/logo.png` | RideDoc logo (`.png`, `.jpg` or `.jpeg`). Optional – if missing, only the name is printed. |
| `data/bills_log.xlsx` | Created automatically. Sheet **Bills** = one row per bill, sheet **Items** = one row per service line. |

## Settings (`ridedoc/config.py`)

- Business name, mobile/address line, website/email line
- `CGST_RATE` / `SGST_RATE` (9% + 9%)
- `COUPONS` – code → flat amount off the final bill (after GST). Currently `REDUCE500` = ₹500.
- `GROUP_NAMES` / `VARIANT_NAMES` – how segment codes map to Hatchback/Sedan/MUV-SUV/Luxury and Entry/Regular
- Invoice prefix (`RD-YYYYMMDD-001`, numbering restarts each day)

## Where bills are saved

- **Google Sheets (permanent)** – used automatically when `.streamlit/secrets.toml` exists
  (template: `.streamlit/secrets.toml.example`). The app creates the **Bills** and **Items** tabs itself.
- **Local Excel (`data/bills_log.xlsx`)** – fallback when no Google credentials are configured.

The Bill History page shows which one is in use.

### Google Sheets setup (one time)

1. Google Cloud Console → create a project → enable **Google Sheets API** and **Google Drive API**.
2. IAM & Admin → Service Accounts → create one → Keys → Add key → JSON → download.
3. Have two Google Sheets (or one with two purposes): the **price list** (same layout as the Excel rate card) and a blank one for **bills**. **Share** both with the service account's `client_email` as **Editor**.
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`; put both sheet URLs and the JSON values in it.

## Deploying to Streamlit Community Cloud

1. Push this folder to a **private** GitHub repo (`.gitignore` keeps secrets and the local log out of git).
2. On share.streamlit.io → New app → choose the repo, main file `app.py`.
3. App → Settings → **Secrets** → paste the contents of your `secrets.toml`.
