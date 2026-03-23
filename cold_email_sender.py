#!/usr/bin/env python3
"""
PRINTMAXX Cold Email Sender - reads leads + sequences, generates .eml files.

Usage:
    python3 AUTOMATIONS/cold_email_sender.py --preview          # show what would send
    python3 AUTOMATIONS/cold_email_sender.py --generate         # create .eml files
    python3 AUTOMATIONS/cold_email_sender.py --status           # show pipeline stats
    python3 AUTOMATIONS/cold_email_sender.py --sequence seq1    # specific sequence only
    python3 AUTOMATIONS/cold_email_sender.py --leads path.csv   # specific lead file
    python3 AUTOMATIONS/cold_email_sender.py --limit 20         # cap output count
"""

import argparse
import csv
import json
import hashlib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

# --- project root + guardrails ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTOMATIONS = PROJECT_ROOT / "AUTOMATIONS"
OUTREACH = AUTOMATIONS / "outreach"
SEQUENCES_DIR = OUTREACH / "sequences"
READY_DIR = OUTREACH / "ready_to_send"
TRACKER_PATH = OUTREACH / "send_tracker.csv"
LEADS_DIR = AUTOMATIONS / "leads"
HOT_BATCH = OUTREACH / "HOT_BATCH_FEB13_COMPLIANT.csv"

# sender defaults (override with env vars or CLI)
DEFAULT_SENDER_NAME = "Max"
DEFAULT_SENDER_EMAIL = "max@printmaxx.co"
DEFAULT_COMPANY = "PRINTMAXX"

# follow-up schedule
FOLLOWUP_SCHEDULE = {1: 0, 2: 3, 3: 7}  # step: days after initial send


def safe_path(target: Path) -> Path:
    """verify path is within project root."""
    resolved = Path(target).resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        raise ValueError(f"BLOCKED: {resolved} is outside project root {PROJECT_ROOT}")
    return resolved


def load_sequences() -> dict:
    """load all sequence JSON files from sequences dir."""
    sequences = {}
    if not SEQUENCES_DIR.exists():
        print(f"[!] sequences dir not found: {SEQUENCES_DIR}")
        return sequences
    for f in sorted(SEQUENCES_DIR.glob("seq*.json")):
        try:
            data = json.loads(f.read_text())
            sequences[f.stem] = data
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[!] skipping {f.name}: {e}")
    return sequences


def load_leads(lead_path: Path = None) -> list[dict]:
    """load leads from CSV. tries HOT_BATCH first, then falls back to leads dir."""
    leads = []
    paths_to_try = []

    if lead_path:
        paths_to_try.append(safe_path(lead_path))
    else:
        # default: HOT_BATCH first, then scored leads
        if HOT_BATCH.exists():
            paths_to_try.append(HOT_BATCH)
        scored = LEADS_DIR / "SCORED_LEADS.csv"
        if scored.exists():
            paths_to_try.append(scored)
        hot = LEADS_DIR / "HOT_LEADS.csv"
        if hot.exists():
            paths_to_try.append(hot)

    seen_emails = set()
    for p in paths_to_try:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    email = _extract_email(row)
                    if email and email not in seen_emails:
                        seen_emails.add(email)
                        leads.append(_normalize_lead(row, email))
        except Exception as e:
            print(f"[!] error reading {p}: {e}")

    return leads


def _extract_email(row: dict) -> str:
    """pull clean email from row, handling multi-email fields."""
    raw = row.get("email", row.get("email_if_found", "")).strip()
    if not raw:
        return ""
    # take first email if semicolon-separated
    email = raw.split(";")[0].strip()
    if "@" not in email or email.endswith("@email.com"):
        return ""
    return email.lower()


def _normalize_lead(row: dict, email: str) -> dict:
    """normalize lead fields to standard merge tag names."""
    return {
        "email": email,
        "business_name": row.get("company_name", row.get("business_name", "your business")).strip(),
        "first_name": row.get("first_name", "").strip(),
        "city": row.get("city", "your city").strip(),
        "industry": _guess_industry(row),
        "specific_issue": row.get("specific_issue", row.get("signals_detected", "your site needs work")).strip(),
        "website": row.get("website", "").strip(),
    }


def _guess_industry(row: dict) -> str:
    """infer industry from category or business name."""
    cat = row.get("category", row.get("industry", "")).lower().strip()
    if cat:
        return cat
    name = row.get("business_name", row.get("company_name", "")).lower()
    for keyword in ["dental", "dentist", "plumb", "restaurant", "lawyer", "attorney"]:
        if keyword in name:
            return keyword.replace("attorney", "lawyer")
    return "local business"


def generate_lead_hash(email: str, sequence_id: str, step: int) -> str:
    """unique hash per lead+sequence+step combo to prevent dupes."""
    raw = f"{email}:{sequence_id}:{step}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def load_tracker() -> set:
    """load already-sent hashes from tracker CSV."""
    sent = set()
    if TRACKER_PATH.exists():
        with open(TRACKER_PATH, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sent.add(row.get("hash", ""))
    return sent


def save_tracker_row(row: dict):
    """append a row to send tracker CSV."""
    safe_path(TRACKER_PATH)
    write_header = not TRACKER_PATH.exists()
    with open(TRACKER_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["hash", "email", "sequence", "step", "subject", "scheduled_date", "generated_at"])
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def personalize(template: str, lead: dict, sender_name: str = DEFAULT_SENDER_NAME) -> str:
    """replace merge tags in template with lead data."""
    text = template
    replacements = {
        "{business_name}": lead.get("business_name", "your business"),
        "{city}": lead.get("city", "your city"),
        "{industry}": lead.get("industry", "your industry"),
        "{specific_issue}": lead.get("specific_issue", "a few issues on your site"),
        "{first_name}": lead.get("first_name", ""),
        "{sender_name}": sender_name,
    }
    for tag, value in replacements.items():
        text = text.replace(tag, value)
    return text


def build_eml(to_email: str, from_email: str, subject: str, body: str, scheduled_date: str) -> str:
    """build RFC 2822 .eml file content."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject
    msg["Date"] = scheduled_date
    msg["X-Mailer"] = "PRINTMAXX Cold Email Sender"
    msg["X-Scheduled-Send"] = scheduled_date
    # CAN-SPAM footer
    footer = (
        "\n\n---\n"
        f"{DEFAULT_COMPANY}\n"
        "Reply STOP to unsubscribe from future emails.\n"
        "This is a one-time outreach. You will not be added to any mailing list."
    )
    full_body = body + footer
    msg.set_payload(full_body, "utf-8")
    return msg.as_string()


def generate_emails(leads: list[dict], sequences: dict, limit: int = 0,
                    seq_filter: str = None, sender_name: str = DEFAULT_SENDER_NAME,
                    sender_email: str = DEFAULT_SENDER_EMAIL) -> list[dict]:
    """generate email jobs (not yet written to disk)."""
    sent_hashes = load_tracker()
    jobs = []
    today = datetime.now()

    seq_items = sequences.items()
    if seq_filter:
        seq_items = [(k, v) for k, v in seq_items if seq_filter in k]

    for seq_name, seq_data in seq_items:
        seq_id = seq_data.get("sequence_id", seq_name)
        for lead in leads:
            for email_tmpl in seq_data.get("emails", []):
                step = email_tmpl["step"]
                h = generate_lead_hash(lead["email"], seq_id, step)
                if h in sent_hashes:
                    continue

                delay = FOLLOWUP_SCHEDULE.get(step, email_tmpl.get("delay_days", 0))
                send_date = today + timedelta(days=delay)

                subject = personalize(email_tmpl["subject"], lead, sender_name)
                body = personalize(email_tmpl["body"], lead, sender_name)

                jobs.append({
                    "hash": h,
                    "email": lead["email"],
                    "business_name": lead["business_name"],
                    "sequence": seq_id,
                    "step": step,
                    "subject": subject,
                    "body": body,
                    "scheduled_date": send_date.strftime("%Y-%m-%d"),
                    "send_date_obj": send_date,
                    "sender_email": sender_email,
                })

                if limit and len(jobs) >= limit:
                    return jobs

    return jobs


def cmd_preview(args):
    """show what would send without generating files."""
    sequences = load_sequences()
    leads = load_leads(Path(args.leads) if args.leads else None)
    jobs = generate_emails(leads, sequences, limit=args.limit or 15, seq_filter=args.sequence)

    print(f"\n--- PREVIEW: {len(jobs)} emails would generate ---\n")
    print(f"leads loaded: {len(leads)}")
    print(f"sequences loaded: {len(sequences)}")
    print()

    for j in jobs[:15]:
        print(f"  TO: {j['email']}")
        print(f"  BIZ: {j['business_name']}")
        print(f"  SEQ: {j['sequence']} step {j['step']}")
        print(f"  SUBJ: {j['subject']}")
        print(f"  DATE: {j['scheduled_date']}")
        print(f"  BODY: {j['body'][:120]}...")
        print()

    remaining = len(jobs) - 15
    if remaining > 0:
        print(f"  ... and {remaining} more emails")


def cmd_generate(args):
    """generate .eml files and update tracker."""
    safe_path(READY_DIR)
    READY_DIR.mkdir(parents=True, exist_ok=True)

    sequences = load_sequences()
    leads = load_leads(Path(args.leads) if args.leads else None)
    jobs = generate_emails(leads, sequences, limit=args.limit or 0, seq_filter=args.sequence)

    if not jobs:
        print("[i] no new emails to generate. all leads already tracked or no leads found.")
        return

    generated = 0
    for j in jobs:
        filename = f"{j['scheduled_date']}_{j['sequence']}_step{j['step']}_{j['hash']}.eml"
        filepath = safe_path(READY_DIR / filename)

        eml_content = build_eml(
            to_email=j["email"],
            from_email=j["sender_email"],
            subject=j["subject"],
            body=j["body"],
            scheduled_date=j["scheduled_date"],
        )

        filepath.write_text(eml_content)
        save_tracker_row({
            "hash": j["hash"],
            "email": j["email"],
            "sequence": j["sequence"],
            "step": j["step"],
            "subject": j["subject"],
            "scheduled_date": j["scheduled_date"],
            "generated_at": datetime.now().isoformat(),
        })
        generated += 1

    print(f"\n[+] generated {generated} .eml files in {READY_DIR}")
    print(f"[+] tracker updated: {TRACKER_PATH}")


def cmd_status(args):
    """show pipeline status."""
    sequences = load_sequences()
    leads = load_leads(Path(args.leads) if args.leads else None)
    sent_hashes = load_tracker()

    eml_count = len(list(READY_DIR.glob("*.eml"))) if READY_DIR.exists() else 0

    print("\n--- COLD OUTBOUND PIPELINE STATUS ---\n")
    print(f"  sequences loaded:    {len(sequences)}")
    for name, seq in sequences.items():
        print(f"    {name}: {seq.get('service', '?')} ({len(seq.get('emails', []))} emails)")
    print(f"\n  leads loaded:        {len(leads)}")
    print(f"  already tracked:     {len(sent_hashes)}")
    print(f"  .eml files ready:    {eml_count}")
    print(f"  tracker file:        {TRACKER_PATH}")
    print(f"  ready_to_send dir:   {READY_DIR}")

    # potential new emails
    total_potential = len(leads) * sum(len(s.get("emails", [])) for s in sequences.values())
    new_potential = total_potential - len(sent_hashes)
    print(f"\n  total possible:      {total_potential}")
    print(f"  new to generate:     {max(0, new_potential)}")

    # breakdown by scheduled date if tracker exists
    if TRACKER_PATH.exists():
        date_counts = {}
        with open(TRACKER_PATH, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = row.get("scheduled_date", "unknown")
                date_counts[d] = date_counts.get(d, 0) + 1
        if date_counts:
            print("\n  schedule breakdown:")
            for d in sorted(date_counts.keys()):
                print(f"    {d}: {date_counts[d]} emails")


def main():
    parser = argparse.ArgumentParser(description="PRINTMAXX Cold Email Sender")
    parser.add_argument("--preview", action="store_true", help="show what would send")
    parser.add_argument("--generate", action="store_true", help="create .eml files")
    parser.add_argument("--status", action="store_true", help="show pipeline stats")
    parser.add_argument("--leads", type=str, default=None, help="path to specific lead CSV")
    parser.add_argument("--sequence", type=str, default=None, help="filter to specific sequence (e.g. seq1)")
    parser.add_argument("--limit", type=int, default=0, help="max emails to generate")
    parser.add_argument("--sender-name", type=str, default=DEFAULT_SENDER_NAME)
    parser.add_argument("--sender-email", type=str, default=DEFAULT_SENDER_EMAIL)

    args = parser.parse_args()

    if args.status:
        cmd_status(args)
    elif args.preview:
        cmd_preview(args)
    elif args.generate:
        cmd_generate(args)
    else:
        parser.print_help()
        print("\nuse --preview, --generate, or --status")


if __name__ == "__main__":
    main()
