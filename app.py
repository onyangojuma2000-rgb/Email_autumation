
import csv
import logging
import os
import re
import shutil
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

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30
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


if __name__ == "__main__":

    try:
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

        sys.exit(1)
