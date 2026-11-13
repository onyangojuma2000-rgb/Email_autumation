
# Daily Retail Sales Email Report

Reads the shop's CSV files and emails an HTML summary every day at **6:00 PM**.

## Includes
- Total revenue and unique transaction count
- Top-selling item by units sold
- Restock warnings for items at or below their restock level

## Setup
1. Copy `.env.example` to `.env` and enter your Gmail address, Gmail App Password and recipient email.
2. Update `sales.csv` during the day. Required columns: `date, transaction_id, item_name, quantity_sold, price`.
3. Update `inventory.csv` with each item's current stock and restock level.
4. Run `python daily_sales_report.py --preview` to check calculations.
5. Run `python daily_sales_report.py --run-now` to send a test email.
6. Run `python daily_sales_report.py` to keep the 6:00 PM schedule active.

Use a Google App Password (not your normal Gmail password) with Google 2-Step Verification. Never commit `.env`. The scheduled program needs the computer awake, online and running; it uses the computer's local time (set Windows to East Africa Time).

The sample sales are dated **2026-11-12**. Change the dates to today's date when testing on another day. Transaction count means unique transaction IDs.

## Client pricing (Kenya)
Charge **KES 6,000 one-off** for setup, testing and handover. A monthly retainer is not necessary for a simple CSV-based tool; offer optional maintenance at **KES 1,000/month**.

## GitHub
Push the project files, but not `.env`. The `.gitignore` file excludes it.
