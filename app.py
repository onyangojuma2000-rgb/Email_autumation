
import csv
import logging
import os
import re
import shutil
import json
import logging
import os
import re
import smtplib
import ssl
import sys

from collections import defaultdict
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv

# ----------------------------------
# 1. PROJECT CONFIGURATION
# ----------------------------------
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


# ==================================================
# 1. PROJECT CONFIGURATION
# ==================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

INPUT_DIR = BASE_DIR / os.getenv("INPUT_FOLDER", "input")
ARCHIVE_DIR = BASE_DIR / os.getenv("ARCHIVE_FOLDER", "archive")
REPORT_DIR = BASE_DIR / os.getenv("REPORT_FOLDER", "reports")
LOG_DIR = BASE_DIR / "logs"

for folder in (INPUT_DIR, ARCHIVE_DIR, REPORT_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "weekly_report.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

DATE_COLUMN = os.getenv("DATE_COLUMN", "date").strip()
SALES_COLUMN = os.getenv("SALES_COLUMN", "sales").strip()

AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic").strip().lower()

ANTHROPIC_MODEL = os.getenv(
    "ANTHROPIC_MODEL",
    "claude-haiku-4-5-20251001"
)

RECIPIENTS = [
    email.strip()
    for email in os.getenv("REPORT_RECIPIENTS", "").split(",")
    if email.strip()
]


# ----------------------------------
# 2. DATA VALIDATION
# ----------------------------------

def parse_number(value):
    """Convert common business number formats to float."""

    if value is None:
        return None

    cleaned = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("KES", "")
        .replace("KSh", "")
        .replace("$", "")
    )

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_row_date(row, file_path):
    """Read the date from a CSV row or infer it from its filename."""

    raw = (row.get(DATE_COLUMN) or "").strip()

    if raw:
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y/%m/%d"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                pass

        raise ValueError(
            f"Unrecognised date '{raw}' in {file_path.name}."
        )

    match = re.search(
        r"(20\d{2}[-_]?\d{2}[-_]?\d{2})",
        file_path.stem
    )

    if match:
        raw_date = match.group(1).replace("_", "").replace("-", "")
        return datetime.strptime(raw_date, "%Y%m%d").date()

    raise ValueError(
        f"Missing date in {file_path.name}."
    )


# ----------------------------------
# 3. READ AND MERGE CSV FILES
# ----------------------------------

def load_csv_files():

    csv_files = sorted(INPUT_DIR.glob("*.csv"))

    if not csv_files:
        raise RuntimeError(
            f"No CSV files found in: {INPUT_DIR}"
        )

    all_rows = []
    headers = None
    source_files = []

    for path in csv_files:

        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
                newline=""
            ) as handle:

                reader = csv.DictReader(handle)

                if not reader.fieldnames:
                    raise ValueError(
                        "CSV has no header row."
                    )

                current_headers = [
                    header.strip()
                    for header in reader.fieldnames
                ]

                if headers is None:
                    headers = current_headers

                elif set(headers) != set(current_headers):
                    raise ValueError(
                        f"CSV columns do not match in {path.name}."
                    )

                for row in reader:

                    if not any(
                        (value or "").strip()
                        for value in row.values()
                    ):
                        continue

                    row = {
                        (key or "").strip(): (value or "").strip()
                        for key, value in row.items()
                    }

                    row["_report_date"] = (
                        parse_row_date(row, path).isoformat()
                    )

                    row["_source_file"] = path.name

                    all_rows.append(row)

            source_files.append(path)

            logger.info("Loaded %s", path.name)

        except Exception:
            logger.exception(
                "Could not read %s",
                path.name
            )
            raise

    if not all_rows:
        raise RuntimeError(
            "CSV files contain no data rows."
        )

    return headers, all_rows, source_files


# ----------------------------------
# 4. CALCULATE BUSINESS TOTALS
# ----------------------------------

def calculate_summary(headers, rows):

    totals = {}

    for column in headers:

        if column == DATE_COLUMN:
            continue

        values = [
            parse_number(row.get(column))
            for row in rows
        ]

        numeric_values = [
            value
            for value in values
            if value is not None
        ]

        populated_values = [
            row for row in rows
            if (row.get(column) or "").strip()
        ]

        if (
            numeric_values
            and len(numeric_values) == len(populated_values)
        ):
            totals[column] = sum(numeric_values)

    daily_totals = defaultdict(float)

    if SALES_COLUMN in headers:

        for row in rows:

            amount = parse_number(
                row.get(SALES_COLUMN)
            )

            if amount is not None:
                daily_totals[row["_report_date"]] += amount

    return totals, dict(sorted(daily_totals.items()))


# ----------------------------------
# 5. TREND COMMENT
# ----------------------------------

def rule_based_comment(daily_totals):

    if not daily_totals:
        return (
            "No daily sales trend was calculated. "
            "Check the sales column configuration."
        )

    values = list(daily_totals.values())

    if len(values) == 1:
        return (
            f"Sales totalled {values[0]:,.2f}. "
            "More daily records are needed to identify a trend."
        )

    first = values[0]
    last = values[-1]

    change = last - first

    if first:
        percentage = (change / first) * 100

        direction = (
            "increased" if change > 0
            else "decreased" if change < 0
            else "remained steady"
        )

        return (
            f"Sales {direction} by {abs(percentage):.1f}% "
            "from the first recorded day to the last. "
            f"Average daily sales were {mean(values):,.2f}."
        )

    return (
        f"Average daily sales were {mean(values):,.2f}. "
        "A percentage comparison is unavailable."
    )


def make_ai_comment(daily_totals):

    fallback = rule_based_comment(daily_totals)

    if not daily_totals or AI_PROVIDER != "anthropic":
        return fallback

    api_key = os.getenv(
        "ANTHROPIC_API_KEY",
        ""
    ).strip()

    if not api_key:
        logger.warning(
            "Anthropic API key missing. Using fallback."
        )
        return fallback

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)

        prompt = (
            "Write a concise business trend comment, "
            "maximum 70 words, using only these daily sales totals. "
            "Mention the direction or variation and one practical "
            "observation. Do not invent causes.\n"
            f"Daily totals: {daily_totals}"
        )

        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=120,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        result = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        ).strip()

        return result or fallback

    except Exception:
        logger.exception(
            "AI trend generation failed."
        )

        return fallback


# ----------------------------------
# 6. CREATE CONSOLIDATED CSV
# ----------------------------------

def write_consolidated_csv(headers, rows):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    output_path = REPORT_DIR / (
        f"weekly_consolidated_{timestamp}.csv"
    )

    output_headers = headers + [
        "_report_date",
        "_source_file"
    ]

    with output_path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=output_headers
        )

        writer.writeheader()
        writer.writerows(rows)

    return output_path


# ----------------------------------
# 7. BUILD EMAIL REPORT
# ----------------------------------

def build_email_body(
    row_count,
    file_count,
    totals,
    trend_comment,
    period
):

    lines = [
        "<h2>Weekly Business Report</h2>",
        f"<p><b>Reporting period:</b> {period}</p>",
        (
            f"<p><b>Source files:</b> {file_count} "
            f"| <b>Records:</b> {row_count}</p>"
        ),
        "<h3>Consolidated totals</h3>",
        "<table border='1' cellpadding='6' cellspacing='0'>",
        "<tr><th>Metric</th><th>Total</th></tr>"
    ]

    for name, value in totals.items():

        lines.append(
            f"<tr><td>{name}</td>"
            f"<td>{value:,.2f}</td></tr>"
        )

    lines.extend([
        "</table>",
        "<h3>Trend comment</h3>",
        f"<p>{trend_comment}</p>",
        "<p>The consolidated CSV is attached.</p>"
    ])

    return "\n".join(lines)


# ----------------------------------
# 8. SEND EMAIL THROUGH GMAIL
# ----------------------------------

def send_email(subject, html_body, attachment_path):

    sender = os.getenv(
        "GMAIL_ADDRESS",
        ""
    ).strip()

    app_password = os.getenv(
        "GMAIL_APP_PASSWORD",
        ""
    ).replace(" ", "").strip()

    if not sender or not app_password or not RECIPIENTS:
        raise RuntimeError(
            "Email settings are incomplete. "
            "Check GMAIL_ADDRESS, GMAIL_APP_PASSWORD "
            "and REPORT_RECIPIENTS in .env."
        )

    message = EmailMessage()

    message["From"] = sender
    message["To"] = ", ".join(RECIPIENTS)
    message["Subject"] = subject

    message.set_content(
        "Your weekly business report is attached."
    )

    message.add_alternative(
        html_body,
        subtype="html"
    )

    with attachment_path.open("rb") as file_handle:

        message.add_attachment(
            file_handle.read(),
            maintype="text",
            subtype="csv",
            filename=attachment_path.name
        )
BUSINESS_NAME = os.getenv(
    "BUSINESS_NAME", "Accounts Department"
)

CURRENCY = os.getenv("CURRENCY", "KES")

INPUT_FILE = BASE_DIR / os.getenv(
    "INPUT_FILE", "input/invoices.xlsx"
)

OUTBOX_FOLDER = BASE_DIR / os.getenv(
    "OUTBOX_FOLDER", "Outbox"
)

LOG_FOLDER = BASE_DIR / os.getenv(
    "LOG_FOLDER", "logs"
)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()

GMAIL_APP_PASSWORD = os.getenv(
    "GMAIL_APP_PASSWORD", ""
).replace(" ", "").strip()

SEND_EMAILS = (
    os.getenv("SEND_EMAILS", "false").strip().lower()
    == "true"
)

SENT_RECORD_FILE = OUTBOX_FOLDER / "sent_reminders.json"


# ==================================================
# 2. CREATE REQUIRED DIRECTORIES
# ==================================================

INPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
OUTBOX_FOLDER.mkdir(parents=True, exist_ok=True)
LOG_FOLDER.mkdir(parents=True, exist_ok=True)


# ==================================================
# 3. LOGGING
# ==================================================

LOG_FILE = LOG_FOLDER / "invoice_reminder.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)


# ==================================================
# 4. CREATE SAMPLE EXCEL WORKBOOK
# ==================================================

def create_sample_workbook():

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"

    headers = [
        "invoice_number",
        "client_name",
        "client_email",
        "invoice_amount",
        "due_date",
        "paid"
    ]

    sheet.append(headers)

    today = date.today()

    sample_invoices = [
        [
            "INV-001",
            "Kamau Stores",
            "your_test_email@gmail.com",
            15000,
            today - timedelta(days=6),
            "No"
        ],
        [
            "INV-002",
            "Wanjiku Traders",
            "your_test_email@gmail.com",
            8500,
            today + timedelta(days=5),
            "No"
        ],
        [
            "INV-003",
            "Otieno Furniture",
            "your_test_email@gmail.com",
            22000,
            today - timedelta(days=10),
            "Yes"
        ],
        [
            "INV-004",
            "Grace Enterprises",
            "your_test_email@gmail.com",
            12000,
            today - timedelta(days=3),
            "No"
        ]
    ]

    for invoice in sample_invoices:
        sheet.append(invoice)

    # Style the heading row
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            fill_type="solid",
            fgColor="1F4E78"
        )

    # Set column widths
    widths = {
        "A": 18,
        "B": 25,
        "C": 35,
        "D": 20,
        "E": 18,
        "F": 12
    }

    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    # Format dates and amounts
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row=row, column=4).number_format = '#,##0.00'
        sheet.cell(row=row, column=5).number_format = 'yyyy-mm-dd'

    workbook.save(INPUT_FILE)
    workbook.close()

    print(f"Sample workbook created: {INPUT_FILE}")
    print("Open it and replace the sample client details.")
    print("Use a real client email only with permission.")


# ==================================================
# 5. NORMALIZE EXCEL HEADERS
# ==================================================

def normalize_header(value):

    if value is None:
        return ""

    value = str(value).strip().lower()

    value = re.sub(r"[^a-z0-9]+", "_", value)

    return value.strip("_")


# ==================================================
# 6. PARSE INVOICE DATES
# ==================================================

def parse_due_date(value):

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if value is None or str(value).strip() == "":
        raise ValueError("Invoice due date is missing")

    date_text = str(value).strip()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d"
    ]

    for date_format in formats:

        try:
            return datetime.strptime(
                date_text, date_format
            ).date()

        except ValueError:
            continue

    raise ValueError(
        f"Unsupported date format: {date_text}"
    )


# ==================================================
# 7. FORMAT INVOICE AMOUNTS
# ==================================================

def format_amount(value):

    if value is None or str(value).strip() == "":
        raise ValueError("Invoice amount is missing")

    amount_text = str(value).strip()

    amount_text = (
        amount_text
        .replace(",", "")
        .replace("KES", "")
        .replace("KSh", "")
        .replace(" ", "")
    )

    try:
        amount = Decimal(amount_text)

    except InvalidOperation:
        raise ValueError(
            f"Invalid invoice amount: {value}"
        )

    if not amount.is_finite() or amount < 0:
        raise ValueError(
            f"Invalid invoice amount: {value}"
        )

    return f"{CURRENCY} {amount:,.2f}"


# ==================================================
# 8. CHECK PAYMENT STATUS
# ==================================================

def is_unpaid(value):

    status = str(value).strip().lower()

    if status in ["no", "n", "false", "0", "unpaid"]:
        return True

    if status in ["yes", "y", "true", "1", "paid"]:
        return False

    raise ValueError(
        f"Invalid payment status: {value}"
    )


# ==================================================
# 9. VALIDATE EMAIL ADDRESS
# ==================================================

def validate_email(email):

    email = str(email).strip()

    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    if not re.match(pattern, email):
        raise ValueError(
            f"Invalid client email address: {email}"
        )

    return email


# ==================================================
# 10. CREATE EMAIL SUBJECT AND BODY
# ==================================================

def create_email_content(
    invoice_number,
    client_name,
    invoice_amount,
    due_date
):

    subject = (
        f"Friendly Reminder: Outstanding Invoice "
        f"{invoice_number}"
    )

    body = f"""
Dear {client_name},

I hope you are doing well.

This is a friendly reminder that invoice
{invoice_number}, amounting to {invoice_amount},
was due on {due_date.strftime('%d %B %Y')}.

Our records indicate that the payment is
still outstanding.

Kindly let us know when we can expect payment.

If you have already made the payment, please
disregard this reminder and accept our apologies.

Thank you for your continued business.

Best regards,

{BUSINESS_NAME}
""".strip()

    return subject, body


# ==================================================
# 11. CREATE TEXT EMAIL DRAFT
# ==================================================

def create_email_draft(
    client_email,
    subject,
    body
):

    return (
        f"To: {client_email}\n"
        f"Subject: {subject}\n\n"
        f"{body}\n"
    )


# ==================================================
# 12. GENERATE SAFE FILE NAME
# ==================================================

def safe_filename(value):

    filename = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        str(value).strip()
    )

    return filename.strip("_") or "invoice"


# ==================================================
# 13. LOAD SENT INVOICE RECORDS
# ==================================================

def load_sent_records():

    if not SENT_RECORD_FILE.exists():
        return {}

    try:

        with SENT_RECORD_FILE.open(
            "r",
            encoding="utf-8"
        ) as file:

            records = json.load(file)

        if not isinstance(records, dict):
            raise ValueError(
                "Sent reminder record must be a JSON object"
            )

        return records

    except (json.JSONDecodeError, OSError, ValueError) as error:

        raise RuntimeError(
            f"Cannot read sent reminder history: {error}. "
            "Do not delete or overwrite this file until "
            "its contents have been checked."
        ) from error


# ==================================================
# 14. SAVE SENT INVOICE RECORDS
# ==================================================

def save_sent_records(records):

    temporary_file = SENT_RECORD_FILE.with_suffix(".tmp")

    with temporary_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            records,
            file,
            indent=4
        )

    temporary_file.replace(SENT_RECORD_FILE)


# ==================================================
# 15. SEND EMAIL USING GMAIL
# ==================================================

def send_reminder_email(
    client_email,
    client_name,
    invoice_number,
    invoice_amount,
    due_date
):

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise ValueError(
            "Gmail settings are incomplete. "
            "Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "in .env"
        )

    client_email = validate_email(client_email)

    subject, body = create_email_content(
        invoice_number,
        client_name,
        invoice_amount,
        due_date
    )

    message = EmailMessage()

    message["From"] = GMAIL_ADDRESS
    message["To"] = client_email
    message["Subject"] = subject

    message.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30
<<<<<<< HEAD
    ) as smtp:

        smtp.login(
            sender,
            app_password
        )

        smtp.send_message(message)

    logger.info(
        "Report email sent to %s",
        ", ".join(RECIPIENTS)
    )


# ----------------------------------
# 9. ARCHIVE PROCESSED CSV FILES
# ----------------------------------

def archive_sources(source_files):

    destination = ARCHIVE_DIR / (
        datetime.now().strftime("%Y-%m-%d_%H%M%S")
    )

    destination.mkdir(
        parents=True,
        exist_ok=True
    )

    for path in source_files:

        shutil.move(
            str(path),
            str(destination / path.name)
        )

    logger.info(
        "Archived %d source files.",
        len(source_files)
    )


# ----------------------------------
# 10. MAIN AUTOMATION
# ----------------------------------

def run_report():

    logger.info("Weekly report started.")

    headers, rows, source_files = load_csv_files()

    totals, daily_totals = calculate_summary(
        headers,
        rows
    )

    trend_comment = make_ai_comment(
        daily_totals
    )

    report_path = write_consolidated_csv(
        headers,
        rows
    )

    dates = sorted(
        row["_report_date"]
        for row in rows
    )

    period = f"{dates[0]} to {dates[-1]}"

    email_body = build_email_body(
        len(rows),
        len(source_files),
        totals,
        trend_comment,
        period
    )

    subject = f"Weekly Business Report | {period}"

    send_email(
        subject,
        email_body,
        report_path
    )

    # Archive only after successful email delivery.
    archive_sources(source_files)

    logger.info(
        "Weekly report completed successfully."
    )

    print(
        f"SUCCESS: Report emailed. File: {report_path}"
    )

=======
    ) as server:

        server.login(
            GMAIL_ADDRESS,
            GMAIL_APP_PASSWORD
        )

        server.send_message(message)

    logging.info(
        "Email successfully sent: %s to %s",
        invoice_number,
        client_email
    )


# ==================================================
# 16. PROCESS EXCEL INVOICES
# ==================================================

def process_invoices():

    if not INPUT_FILE.exists():

        create_sample_workbook()

        print("\nUpdate the sample workbook and run:")
        print("python app.py")

        return

    if SEND_EMAILS and (
        not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD
    ):

        raise ValueError(
            "Email settings are incomplete. "
            "Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "in .env"
        )

    sent_records = load_sent_records()

    workbook = load_workbook(
        INPUT_FILE,
        read_only=True,
        data_only=True
    )

    try:

        sheet = workbook.active

        rows = sheet.iter_rows(values_only=True)

        headers = [
            normalize_header(value)
            for value in next(rows, [])
        ]

        required_columns = [
            "invoice_number",
            "client_name",
            "client_email",
            "invoice_amount",
            "due_date",
            "paid"
        ]

        missing_columns = [
            column
            for column in required_columns
            if column not in headers
        ]

        if missing_columns:

            raise ValueError(
                "Missing Excel columns: "
                + ", ".join(missing_columns)
            )

        positions = {
            column: headers.index(column)
            for column in required_columns
        }

        today = date.today()

        total_invoices = 0
        overdue_count = 0
        sent_count = 0
        already_sent_count = 0
        skipped_count = 0
        failed_count = 0

        seen_invoice_numbers = set()

        print("\nINVOICE REMINDER REPORT")
        print("=" * 50)
        print(f"Report date: {today}")
        print(
            f"Email sending: "
            f"{'ENABLED' if SEND_EMAILS else 'DISABLED'}"
        )
        print("=" * 50)

        for row_number, row in enumerate(rows, start=2):

            if not row or all(
                value is None or str(value).strip() == ""
                for value in row
            ):
                continue

            total_invoices += 1

            try:

                invoice_number = row[
                    positions["invoice_number"]
                ]

                client_name = row[
                    positions["client_name"]
                ]

                client_email = row[
                    positions["client_email"]
                ]

                invoice_amount = row[
                    positions["invoice_amount"]
                ]

                due_date_value = row[
                    positions["due_date"]
                ]

                paid_status = row[
                    positions["paid"]
                ]

                if not invoice_number:
                    raise ValueError(
                        "Invoice number is missing"
                    )

                invoice_number = str(
                    invoice_number
                ).strip()

                if invoice_number in seen_invoice_numbers:
                    raise ValueError(
                        f"Duplicate invoice number: {invoice_number}"
                    )

                seen_invoice_numbers.add(invoice_number)

                if not client_name:
                    raise ValueError(
                        "Client name is missing"
                    )

                client_name = str(client_name).strip()

                due_date = parse_due_date(
                    due_date_value
                )

                unpaid = is_unpaid(paid_status)

                if not unpaid:

                    print(
                        f"Skipped {invoice_number}: already paid"
                    )

                    continue

                if due_date >= today:

                    print(
                        f"Skipped {invoice_number}: not overdue"
                    )

                    continue

                formatted_amount = format_amount(
                    invoice_amount
                )

                overdue_count += 1

                # Check duplicate email history
                if invoice_number in sent_records:

                    already_sent_count += 1

                    print(
                        f"Already sent: {invoice_number}"
                    )

                    continue

                # Create email content
                subject, body = create_email_content(
                    invoice_number,
                    client_name,
                    formatted_amount,
                    due_date
                )

                # Save a text draft
                filename = (
                    f"{safe_filename(invoice_number)}_"
                    f"{safe_filename(client_name)}.txt"
                )

                output_file = OUTBOX_FOLDER / filename

                email_draft = create_email_draft(
                    client_email or "[Add client email]",
                    subject,
                    body
                )

                output_file.write_text(
                    email_draft,
                    encoding="utf-8"
                )

                print(
                    f"Draft created: {invoice_number}"
                )

                # Send email only if enabled
                if SEND_EMAILS:

                    try:

                        send_reminder_email(
                            client_email,
                            client_name,
                            invoice_number,
                            formatted_amount,
                            due_date
                        )

                        # Record successful delivery only
                        sent_records[invoice_number] = {
                            "client_name": client_name,
                            "client_email": client_email,
                            "invoice_amount": formatted_amount,
                            "due_date": due_date.isoformat(),
                            "sent_at": datetime.now().isoformat(
                                timespec="seconds"
                            )
                        }

                        save_sent_records(sent_records)

                        sent_count += 1

                        print(
                            f"Email sent: {invoice_number}"
                        )

                    except Exception as email_error:

                        failed_count += 1

                        logging.exception(
                            "Email failed for invoice %s",
                            invoice_number
                        )

                        print(
                            f"Email failed for {invoice_number}: "
                            f"{email_error}"
                        )

                else:

                    print(
                        f"Email sending disabled: {invoice_number}"
                    )

            except (ValueError, TypeError, IndexError) as error:

                skipped_count += 1

                logging.warning(
                    "Row %s skipped: %s",
                    row_number,
                    error
                )

                print(
                    f"Skipped row {row_number}: {error}"
                )

            except OSError as error:

                failed_count += 1

                logging.exception(
                    "File operation failed on row %s",
                    row_number
                )

                print(
                    f"File error on row {row_number}: {error}"
                )

        print("\n" + "=" * 50)
        print("PROCESSING COMPLETED")
        print("=" * 50)

        print(f"Total invoices checked: {total_invoices}")
        print(f"Overdue unpaid invoices: {overdue_count}")
        print(f"New email drafts created: {overdue_count - already_sent_count - failed_count if not SEND_EMAILS else overdue_count - already_sent_count}")
        print(f"Emails successfully sent: {sent_count}")
        print(f"Already sent previously: {already_sent_count}")
        print(f"Rows skipped: {skipped_count}")
        print(f"Email/file failures: {failed_count}")

        print(f"\nDrafts saved in: {OUTBOX_FOLDER}")
        print(f"Log file: {LOG_FILE}")

        logging.info(
            "Run completed. Checked=%s, Overdue=%s, Sent=%s, "
            "Already sent=%s, Skipped=%s, Failed=%s",
            total_invoices,
            overdue_count,
            sent_count,
            already_sent_count,
            skipped_count,
            failed_count
        )

    finally:
        workbook.close()


# ==================================================
# 17. MAIN PROGRAM
# ==================================================
>>>>>>> 2c47a18 (invoice-automatic-reminder-system)

if __name__ == "__main__":

    try:
<<<<<<< HEAD
        run_report()

    except Exception as exc:

        logger.exception(
            "Weekly report failed: %s",
            exc
        )

        print(
            f"ERROR: Report not completed: {exc}",
            file=sys.stderr
        )
=======

        process_invoices()

    except Exception as error:

        logging.exception(
            "Invoice reminder application failed"
        )

        print(f"\nERROR: {error}")
>>>>>>> 2c47a18 (invoice-automatic-reminder-system)

        sys.exit(1)
