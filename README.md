
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
# Nairobi Daily Sales Email Automation

A Python automation tool for small shops and retail businesses. It reads daily sales and inventory records from CSV files, calculates sales performance, identifies low-stock items, and emails the business owner a daily HTML report.

The report runs automatically every day at **6:00 PM** using Windows Task Scheduler.

## Features

- Calculates daily revenue in KES.
- Counts completed transactions.
- Identifies the top-selling item by quantity.
- Detects inventory items requiring restocking.
- Sends professional HTML email reports through Gmail.
- Runs automatically on a daily schedule.
- Records every execution in `run_log.txt`.
- Stores detailed errors in `sales_report.log`.
- Includes preview mode for testing without sending emails.

## Project Structure

```text
nairobi_daily_sales_automation/
├── daily_sales_report.py
├── sales.csv
├── inventory.csv
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── run_log.txt
```

## Client Setup (Windows) — 3 Steps

### Step 1: Install and configure

- Install Python 3.
- Download and extract the project folder.
- Copy `.env.example` and rename the copy to `.env`.
- Enter the business Gmail address, Gmail App Password, and report recipient in `.env`.

**No additional pip packages are required.** The application uses Python's standard library.

### Step 2: Schedule the daily report

Open Windows Task Scheduler and select **Create Basic Task**.

Configure the task as follows:

| Setting | Configuration |
|---|---|
| Name | Daily Sales Email Report |
| Trigger | Daily |
| Start time | 6:00 PM |
| Action | Start a Program |
| Program/script | Full path to `python.exe` |
| Add arguments | `daily_sales_report.py --run-now` |
| Start in | Full path to the project folder |

To find your Python executable, run:

```powershell
python -c "import sys; print(sys.executable)"
```

Save the task. Ensure the computer is switched on, awake, and connected to the internet at the scheduled time.

### Step 3: Test and verify

Open PowerShell in the project folder and run:

```powershell
python daily_sales_report.py --preview
python daily_sales_report.py --preview
Get-Content .\run_log.txt
```

Confirm that both runs are recorded successfully. Then use Task Scheduler's **Run** action to test the actual email delivery.

Check the recipient's inbox and confirm that a new execution record appears in `run_log.txt`.

## Manual Commands

Preview the report without sending an email:

```powershell
python daily_sales_report.py --preview
```

Generate and send the actual email report:

```powershell
python daily_sales_report.py --run-now
```

View execution records:

```powershell
Get-Content .\run_log.txt
```

## Execution Log

Every execution appends one record to `run_log.txt`.

Example:

```text
2026-11-14 18:00:00 | SUCCESS | Rows processed: 12
2026-11-14 18:05:00 | SUCCESS | Rows processed: 12
```

- **SUCCESS:** Report generation completed successfully. For email runs, sending also succeeded.
- **ERROR:** The execution failed. Check `sales_report.log` for diagnostic details.
- **Rows processed:** Number of today's matching sales records encountered in `sales.csv`.

The timestamps and counts above are examples.

## CSV Data Requirements

**sales.csv**

Required columns:

```csv
date,transaction_id,item_name,quantity_sold,price
2026-11-14,TXN001,Maize Flour 2kg,2,180
```

**inventory.csv**

Required columns:

```csv
item_name,stock_on_hand,restock_level
Maize Flour 2kg,8,10
```

Sales dates must use `YYYY-MM-DD`. The application reports records matching the computer's current local date.

## Security

- Keep `.env` private.
- Use a Gmail App Password instead of your normal Gmail password.
- Never upload credentials to GitHub.
- Review operational logs before sharing them publicly.

## Technology

- Python 3
- CSV data processing
- Gmail SMTP with SSL
- HTML email reporting
- Windows Task Scheduler

## GitHub Deployment

To upload the project to GitHub:

```powershell
git init
git add .
git status
git commit -m "Add daily sales email automation"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Before committing, confirm that `.env` is excluded from Git tracking.

## Business Value

This tool helps small retail businesses reduce manual reporting, monitor daily sales performance, identify stock shortages early, and receive consistent sales updates without manually running reports every evening.
