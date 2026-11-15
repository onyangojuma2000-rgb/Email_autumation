import argparse
import csv
import html
import logging
import os
import smtplib
import ssl
from collections import defaultdict
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path


# ============================================================
# 1. PROJECT CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

SALES_FILE = BASE_DIR / "sales.csv"
INVENTORY_FILE = BASE_DIR / "inventory.csv"
ENV_FILE = BASE_DIR / ".env"
RUN_LOG_FILE = BASE_DIR / "run_log.txt"
ERROR_LOG_FILE = BASE_DIR / "sales_report.log"


# Detailed diagnostic log. The assignment's required one-line
# execution records are written separately to run_log.txt.
logging.basicConfig(
    filename=ERROR_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8",
)


# ============================================================
# 2. ENVIRONMENT CONFIGURATION
# ============================================================

def load_env():
    """Load key=value settings from the local .env file."""
    if not ENV_FILE.exists():
        return

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def email_settings():
    """Return and validate the Gmail settings."""
    load_env()

    sender = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.getenv("REPORT_RECIPIENT", "").strip()

    if not sender or not password or not recipient:
        raise RuntimeError(
            "Email settings are missing. Copy .env.example to .env "
            "and enter GMAIL_ADDRESS, GMAIL_APP_PASSWORD, "
            "and REPORT_RECIPIENT."
        )

    return sender, password, recipient


# ============================================================
# 3. REQUIRED ONE-LINE EXECUTION LOG
# ============================================================

def write_run_log(status, rows_processed):
    """Append timestamp, status and processed-row count to run_log.txt."""
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{timestamp} | {status} | "
        f"Rows processed: {rows_processed}\n"
    )

    with RUN_LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(line)


# ============================================================
# 4. READ AND VALIDATE TODAY'S SALES
# ============================================================

def read_daily_sales(report_date=None, progress=None):
    """
    Read today's sales and calculate item totals.

    progress is a one-item list used to retain the number of matching
    sales rows processed even if a later row contains invalid data.
    """
    report_date = report_date or date.today().isoformat()
    progress = progress if progress is not None else [0]

    if not SALES_FILE.exists():
        raise FileNotFoundError(f"Sales file not found: {SALES_FILE}")

    item_totals = defaultdict(lambda: {"quantity": 0, "revenue": 0.0})
    transaction_ids = set()

    with SALES_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required = {
            "date", "transaction_id", "item_name",
            "quantity_sold", "price",
        }

        if not required.issubset(reader.fieldnames or []):
            raise ValueError("sales.csv is missing required columns.")

        for row in reader:
            if (row.get("date") or "").strip() != report_date:
                continue

            # Count this matching data row before validating it, so the
            # error log reports how many today's rows were encountered.
            progress[0] += 1

            item = (row.get("item_name") or "").strip()
            transaction_id = (row.get("transaction_id") or "").strip()

            try:
                quantity = int(row["quantity_sold"])
                price = float(row["price"])
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"Invalid quantity or price in sales row: {row}") from exc

            if not item or not transaction_id or quantity <= 0 or price < 0:
                raise ValueError(f"Invalid sales row: {row}")

            item_totals[item]["quantity"] += quantity
            item_totals[item]["revenue"] += quantity * price
            transaction_ids.add(transaction_id)

    if not item_totals:
        raise ValueError(
            f"No sales found for {report_date}. "
            "Check the date values in sales.csv."
        )

    total_revenue = sum(
        values["revenue"] for values in item_totals.values()
    )

    top_item = max(
        item_totals.items(),
        key=lambda pair: pair[1]["quantity"],
    )

    return {
        "report_date": report_date,
        "total_revenue": total_revenue,
        "transaction_count": len(transaction_ids),
        "top_item": top_item[0],
        "top_item_quantity": top_item[1]["quantity"],
        "item_totals": dict(item_totals),
        "rows_processed": progress[0],
    }


# ============================================================
# 5. READ INVENTORY AND FIND RESTOCK ALERTS
# ============================================================

def read_restock_alerts():
    """Find products at or below their restock level."""
    if not INVENTORY_FILE.exists():
        raise FileNotFoundError(f"Inventory file not found: {INVENTORY_FILE}")

    alerts = []

    with INVENTORY_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required = {"item_name", "stock_on_hand", "restock_level"}

        if not required.issubset(reader.fieldnames or []):
            raise ValueError("inventory.csv is missing required columns.")

        for row in reader:
            try:
                stock = int(row["stock_on_hand"])
                level = int(row["restock_level"])
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(f"Invalid inventory row: {row}") from exc

            if stock < 0 or level < 0:
                raise ValueError(f"Negative inventory value: {row}")

            if stock <= level:
                alerts.append({
                    "item_name": (row.get("item_name") or "").strip(),
                    "stock": stock,
                    "level": level,
                })

    return alerts


# ============================================================
# 6. BUILD HTML EMAIL
# ============================================================

def build_html(data, alerts):
    """Build the formatted HTML email."""
    sales_rows = "".join(
        f"""
        <tr>
            <td>{html.escape(name)}</td>
            <td>{values['quantity']}</td>
            <td>KES {values['revenue']:,.2f}</td>
        </tr>
        """
        for name, values in sorted(
            data["item_totals"].items(),
            key=lambda pair: pair[1]["quantity"],
            reverse=True,
        )
    )

    restock_rows = "".join(
        f"""
        <tr>
            <td>{html.escape(alert['item_name'])}</td>
            <td>{alert['stock']}</td>
            <td>{alert['level']}</td>
            <td><b>RESTOCK</b></td>
        </tr>
        """
        for alert in alerts
    ) or """
        <tr>
            <td colspan="4">No items need restocking.</td>
        </tr>
    """

    status = (
        f"{len(alerts)} item(s) need restocking."
        if alerts else "No restock alerts."
    )

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#222;max-width:700px;margin:auto">
        <h2 style="color:#174a35">Daily Sales Summary</h2>
        <p><b>Date:</b> {html.escape(data['report_date'])}</p>

        <table style="width:100%;border-collapse:collapse" border="1" cellpadding="8">
            <tr style="background:#eef4f0">
                <th align="left">Metric</th><th align="left">Result</th>
            </tr>
            <tr><td>Total revenue</td><td><b>KES {data['total_revenue']:,.2f}</b></td></tr>
            <tr><td>Transactions</td><td>{data['transaction_count']}</td></tr>
            <tr>
                <td>Top-selling item (by units)</td>
                <td>{html.escape(data['top_item'])} ({data['top_item_quantity']} units)</td>
            </tr>
            <tr><td>Restock check</td><td><b>{html.escape(status)}</b></td></tr>
        </table>

        <h3>Sales by item</h3>
        <table style="width:100%;border-collapse:collapse" border="1" cellpadding="8">
            <tr style="background:#eef4f0">
                <th align="left">Item</th><th align="left">Units</th><th align="left">Revenue</th>
            </tr>
            {sales_rows}
        </table>

        <h3>Restock alerts</h3>
        <table style="width:100%;border-collapse:collapse" border="1" cellpadding="8">
            <tr style="background:#fff2e5">
                <th align="left">Item</th><th align="left">Stock left</th>
                <th align="left">Restock level</th><th align="left">Status</th>
            </tr>
            {restock_rows}
        </table>

        <p style="font-size:12px;color:#666">
            Automatically generated from the shop CSV records.
        </p>
    </body>
    </html>
    """


# ============================================================
# 7. SEND EMAIL
# ============================================================

def send_email(data, alerts):
    """Send the report through Gmail SMTP over SSL."""
    sender, password, recipient = email_settings()

    message = EmailMessage()
    message["Subject"] = f"Daily Sales Summary - {data['report_date']}"
    message["From"] = sender
    message["To"] = recipient

    message.set_content(
        f"""Daily sales: {data['report_date']}

Revenue: KES {data['total_revenue']:,.2f}
Transactions: {data['transaction_count']}
Top item: {data['top_item']} ({data['top_item_quantity']} units)
Restock alerts: {len(alerts)}
Sales rows processed: {data['rows_processed']}
"""
    )

    message.add_alternative(build_html(data, alerts), subtype="html")

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30,
    ) as server:
        server.login(sender, password)
        server.send_message(message)

    return recipient


# ============================================================
# 8. RUN REPORT
# ============================================================

def run_report(send=False, progress=None):
    """Calculate the report and optionally send it."""
    progress = progress if progress is not None else [0]

    data = read_daily_sales(progress=progress)
    alerts = read_restock_alerts()

    print(f"Report date: {data['report_date']}")
    print(f"Total revenue: KES {data['total_revenue']:,.2f}")
    print(f"Transactions: {data['transaction_count']}")
    print(
        f"Top-selling item: {data['top_item']} "
        f"({data['top_item_quantity']} units)"
    )
    print(f"Restock check: {len(alerts)} item(s) need restocking")
    print(f"Sales rows processed: {data['rows_processed']}")

    for alert in alerts:
        print(
            f" - {alert['item_name']}: {alert['stock']} left; "
            f"restock level {alert['level']}"
        )

    if send:
        recipient = send_email(data, alerts)
        print(f"Email sent to {recipient}.")
    else:
        print("Preview only: no email was sent.")

    return data, alerts


# ============================================================
# 9. EXECUTION WRAPPER AND REQUIRED LOGGING
# ============================================================

def execute_job(send_email_report):
    """
    Execute one report attempt and write exactly one run_log.txt line.

    SUCCESS means report generation succeeded and, when requested,
    the email was successfully handed to Gmail SMTP.
    """
    progress = [0]

    try:
        run_report(send=send_email_report, progress=progress)
        write_run_log("SUCCESS", progress[0])
        logging.info(
            "Report completed successfully. Rows processed: %s",
            progress[0],
        )
        print("Execution result: SUCCESS. Logged to run_log.txt.")
        return True

    except Exception as error:
        write_run_log("ERROR", progress[0])
        logging.exception(
            "Report execution failed. Rows processed before failure: %s",
            progress[0],
        )
        print(f"Execution result: ERROR - {error}")
        print("See sales_report.log for diagnostic details.")
        return False


# ============================================================
# 10. COMMAND-LINE ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Daily retail sales summary and email automation."
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--preview",
        action="store_true",
        help="Build and display the report without emailing; logs the run.",
    )
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Generate and email the report immediately; logs the run.",
    )

    args = parser.parse_args()

    if args.preview:
        execute_job(send_email_report=False)
    elif args.run_now:
        execute_job(send_email_report=True)
    else:
        parser.print_help()
        print(
            "\nSchedule this command with Windows Task Scheduler "
            "for 6:00 PM: daily_sales_report.py --run-now"
        )


if __name__ == "__main__":
    main()
