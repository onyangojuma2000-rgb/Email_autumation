
import argparse
import csv
import html
import logging
import os
import smtplib
import ssl
import time
from collections import defaultdict
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_env():
    """Load variables from the local .env file."""
    env_file = BASE_DIR / ".env"

    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'")
        )


load_env()

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv(
    "GMAIL_APP_PASSWORD", ""
).replace(" ", "")
REPORT_RECIPIENT = os.getenv("REPORT_RECIPIENT")

SALES_FILE = BASE_DIR / "sales.csv"
INVENTORY_FILE = BASE_DIR / "inventory.csv"

logging.basicConfig(
    filename=BASE_DIR / "sales_report.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def read_daily_sales(report_date=None):
    """Read today's sales and calculate item totals."""

    report_date = report_date or date.today().isoformat()

    item_totals = defaultdict(
        lambda: {"quantity": 0, "revenue": 0.0}
    )

    transaction_ids = set()

    with SALES_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:

        reader = csv.DictReader(file)

        required = {
            "date",
            "transaction_id",
            "item_name",
            "quantity_sold",
            "price",
        }

        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "sales.csv is missing required columns."
            )

        for row in reader:

            if row["date"].strip() != report_date:
                continue

            item = row["item_name"].strip()
            quantity = int(row["quantity_sold"])
            price = float(row["price"])

            if not item or quantity <= 0 or price < 0:
                raise ValueError(f"Invalid sales row: {row}")

            item_totals[item]["quantity"] += quantity
            item_totals[item]["revenue"] += quantity * price

            transaction_ids.add(row["transaction_id"].strip())

    if not item_totals:
        raise ValueError(
            f"No sales found for {report_date}. Check sales.csv."
        )

    total_revenue = sum(
        value["revenue"]
        for value in item_totals.values()
    )

    top_item = max(
        item_totals.items(),
        key=lambda pair: pair[1]["quantity"]
    )

    return {
        "report_date": report_date,
        "total_revenue": total_revenue,
        "transaction_count": len(transaction_ids),
        "top_item": top_item[0],
        "top_item_quantity": top_item[1]["quantity"],
        "item_totals": dict(item_totals),
    }


def read_restock_alerts():
    """Find products at or below their restock level."""

    alerts = []

    with INVENTORY_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:

        reader = csv.DictReader(file)

        required = {
            "item_name",
            "stock_on_hand",
            "restock_level",
        }

        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "inventory.csv is missing required columns."
            )

        for row in reader:

            stock = int(row["stock_on_hand"])
            level = int(row["restock_level"])

            if stock <= level:

                alerts.append({
                    "item_name": row["item_name"].strip(),
                    "stock": stock,
                    "level": level,
                })

    return alerts


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
            key=lambda x: x[1]["quantity"],
            reverse=True
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
        if alerts
        else "No restock alerts."
    )

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;
                 color:#222;max-width:700px;margin:auto">

        <h2 style="color:#174a35">
            Daily Sales Summary
        </h2>

        <p>
            <b>Date:</b> {html.escape(data['report_date'])}
        </p>

        <table style="width:100%;border-collapse:collapse">

            <tr style="background:#eef4f0">
                <th align="left" style="padding:9px">Metric</th>
                <th align="left" style="padding:9px">Result</th>
            </tr>

            <tr>
                <td style="padding:9px">Total revenue</td>
                <td style="padding:9px">
                    <b>KES {data['total_revenue']:,.2f}</b>
                </td>
            </tr>

            <tr>
                <td style="padding:9px">Transactions</td>
                <td style="padding:9px">
                    {data['transaction_count']}
                </td>
            </tr>

            <tr>
                <td style="padding:9px">
                    Top-selling item (by units)
                </td>
                <td style="padding:9px">
                    {html.escape(data['top_item'])}
                    ({data['top_item_quantity']} units)
                </td>
            </tr>

            <tr>
                <td style="padding:9px">Restock check</td>
                <td style="padding:9px">
                    <b>{html.escape(status)}</b>
                </td>
            </tr>

        </table>

        <h3>Sales by item</h3>

        <table style="width:100%;border-collapse:collapse">

            <tr style="background:#eef4f0">
                <th align="left" style="padding:8px">Item</th>
                <th align="left">Units</th>
                <th align="left">Revenue</th>
            </tr>

            {sales_rows}

        </table>

        <h3>Restock alerts</h3>

        <table style="width:100%;border-collapse:collapse">

            <tr style="background:#fff2e5">
                <th align="left" style="padding:8px">Item</th>
                <th align="left">Stock left</th>
                <th align="left">Restock level</th>
                <th align="left">Status</th>
            </tr>

            {restock_rows}

        </table>

        <p style="font-size:12px;color:#666">
            Automatically generated from the shop CSV records.
        </p>

    </body>
    </html>
    """


def send_email(data, alerts):
    """Send the report through Gmail SMTP."""

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not REPORT_RECIPIENT:
        raise RuntimeError(
            "Add Gmail credentials and recipient to your .env file."
        )

    message = EmailMessage()

    message["Subject"] = (
        f"Daily Sales Summary - {data['report_date']}"
    )

    message["From"] = GMAIL_ADDRESS
    message["To"] = REPORT_RECIPIENT

    message.set_content(
        f"""
Daily sales: {data['report_date']}

Revenue: KES {data['total_revenue']:,.2f}

Transactions: {data['transaction_count']}

Top item: {data['top_item']}
({data['top_item_quantity']} units)

Restock alerts: {len(alerts)}
        """
    )

    message.add_alternative(
        build_html(data, alerts),
        subtype="html"
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30
    ) as server:

        server.login(
            GMAIL_ADDRESS,
            GMAIL_APP_PASSWORD
        )

        server.send_message(message)


def run_report(send=False):
    """Calculate and optionally send the report."""

    data = read_daily_sales()
    alerts = read_restock_alerts()

    print(f"Report date: {data['report_date']}")

    print(
        f"Total revenue: KES "
        f"{data['total_revenue']:,.2f}"
    )

    print(
        f"Transactions: {data['transaction_count']}"
    )

    print(
        f"Top-selling item: {data['top_item']} "
        f"({data['top_item_quantity']} units)"
    )

    print(
        f"Restock check: {len(alerts)} item(s) "
        "need restocking"
    )

    for alert in alerts:
        print(
            f" - {alert['item_name']}: "
            f"{alert['stock']} left; "
            f"restock level {alert['level']}"
        )

    if send:
        send_email(data, alerts)

        logging.info(
            "Email sent successfully for %s",
            data["report_date"]
        )

        print(f"Email sent to {REPORT_RECIPIENT}.")

    return data, alerts


def daily_job():
    """Run the daily scheduled email."""

    try:
        run_report(send=True)

    except Exception:
        logging.exception("Daily report failed")
        print(
            "Email failed. See sales_report.log for details."
        )


def main():

    parser = argparse.ArgumentParser(
        description="Email a daily retail sales summary."
    )

    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "--preview",
        action="store_true",
        help="Calculate and display without emailing."
    )

    group.add_argument(
        "--run-now",
        action="store_true",
        help="Send the email immediately."
    )

    args = parser.parse_args()

    if args.preview:
        run_report()

    elif args.run_now:
        run_report(send=True)

    else:
        print(
            "Daily email scheduled for 9:00 PM "
            "computer local time."
        )

        print(
            "Keep this program running; "
            "the computer must be awake and online."
        )

        last_run = None

        while True:

            now = datetime.now()

            if (
                now.hour == 18
                and now.minute == 0
                and last_run != now.date()
            ):

                daily_job()
                last_run = now.date()

            time.sleep(15)


if __name__ == "__main__":
    main()
