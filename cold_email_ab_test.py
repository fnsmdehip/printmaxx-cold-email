#!/usr/bin/env python3

from __future__ import annotations
"""
Cold Email A/B Test System
Splits leads into A/B groups, generates variant emails, tracks results.
Uses only stdlib. Chi-square significance test at 100+ sends per variant.

CLI: --split [--limit N] | --generate | --status | --results | --log-result ID A|B win|loss
"""

import argparse
import csv
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime
from math import sqrt
from pathlib import Path

BASE = Path(__file__).resolve().parent
INPUT_CSV = BASE / "output" / "cold_emails" / "cold_emails_ready.csv"
ASSIGN_CSV = BASE / "output" / "cold_emails" / "ab_test_assignments.csv"
VARIANT_A = BASE / "output" / "cold_emails" / "ab_variant_A.csv"
VARIANT_B = BASE / "output" / "cold_emails" / "ab_variant_B.csv"
RESULTS_CSV = BASE / "output" / "cold_emails" / "ab_test_results.csv"

ASSIGN_FIELDS = [
    "lead_id", "business_name", "to_email", "website", "city",
    "industry", "website_score", "demo_url", "variant", "assigned_at",
]
VARIANT_FIELDS = [
    "lead_id", "business_name", "to_email", "website", "city",
    "industry", "demo_url", "variant", "subject", "body",
]
RESULT_FIELDS = ["lead_id", "variant", "outcome", "logged_at"]

# ---------------------------------------------------------------------------
# Variant templates
# ---------------------------------------------------------------------------

def subject_a(row: dict) -> str:
    return f"Quick question about {row['business_name']}"

def subject_b(row: dict) -> str:
    city = row.get("city", "your area")
    cat = row.get("industry", "business")
    return f"{city} {cat} - saw your website"

def opening_a(row: dict) -> str:
    """Direct problem statement."""
    return (
        f"Your site at {row['website']} is leaving calls on the table. "
        f"I spotted 3 fixes that could change that."
    )

def opening_b(row: dict) -> str:
    """Compliment-first."""
    return (
        f"Found {row['business_name']} while researching {row.get('industry', 'businesses')} "
        f"in {row.get('city', 'your area')}. Solid presence."
    )

def cta_a() -> str:
    return "Hit reply and I'll send over the specifics."

def cta_b() -> str:
    return "Got 15 min this week? I'll walk you through it: https://cal.com/printmaxx/15"

def body_short_a(row: dict) -> str:
    """3-sentence ultra-short (Variant A)."""
    return (
        f"{opening_a(row)}\n\n"
        f"{cta_a()}"
    )

def body_long_b(row: dict) -> str:
    """Full 6-sentence with specifics (Variant B)."""
    demo = row.get("demo_url", "")
    demo_line = f"\n\nI built a quick demo showing the improvements: {demo}" if demo else ""
    return (
        f"{opening_b(row)}\n\n"
        f"I noticed your site could convert more visitors into booked appointments. "
        f"Things like mobile speed, clear CTAs, and trust signals make a measurable difference. "
        f"Most {row.get('industry', 'business')} sites I audit are missing at least two of those."
        f"{demo_line}\n\n"
        f"{cta_b()}"
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def deterministic_variant(lead_id: str) -> str:
    """Hash-based 50/50 split so the same lead always lands in the same bucket."""
    h = hashlib.md5(lead_id.encode()).hexdigest()
    return "A" if int(h, 16) % 2 == 0 else "B"

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def chi_square_test(a_wins: int, a_total: int, b_wins: int, b_total: int) -> dict:
    """
    2x2 chi-square test for independence.
    Returns chi2 statistic, p-value approximation, and significance flag.
    """
    n = a_total + b_total
    if n == 0:
        return {"chi2": 0, "p_approx": 1.0, "significant": False, "reason": "no data"}

    # observed: [[a_wins, a_losses], [b_wins, b_losses]]
    a_loss = a_total - a_wins
    b_loss = b_total - b_wins
    obs = [[a_wins, a_loss], [b_wins, b_loss]]
    row_sums = [a_total, b_total]
    col_sums = [a_wins + b_wins, a_loss + b_loss]

    chi2 = 0.0
    for i in range(2):
        for j in range(2):
            expected = (row_sums[i] * col_sums[j]) / n if n else 0
            if expected == 0:
                continue
            chi2 += (obs[i][j] - expected) ** 2 / expected

    # 1-df chi-square p-value approximation using the survival function
    # P(X > chi2) for X ~ chi2(1). Use the Wilson-Hilferty normal approx.
    if chi2 <= 0:
        p = 1.0
    else:
        # For 1 degree of freedom: p = 2 * (1 - Phi(sqrt(chi2)))
        z = sqrt(chi2)
        # Rational approximation for 1 - Phi(z) (Abramowitz & Stegun 26.2.17)
        t = 1.0 / (1.0 + 0.2316419 * z)
        poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
        from math import exp, pi
        phi_tail = poly * exp(-z * z / 2) / sqrt(2 * pi)
        p = 2 * phi_tail

    sig = p < 0.05 and min(a_total, b_total) >= 100
    reason = ""
    if min(a_total, b_total) < 100:
        reason = f"need 100+ sends per variant (A={a_total}, B={b_total})"
    elif not sig:
        reason = f"p={p:.4f} >= 0.05"
    return {"chi2": round(chi2, 4), "p_approx": round(p, 6), "significant": sig, "reason": reason}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_split(limit: int | None = None):
    leads = read_csv(INPUT_CSV)
    if not leads:
        print(f"ERROR: No leads found at {INPUT_CSV}")
        sys.exit(1)

    existing = {r["lead_id"] for r in read_csv(ASSIGN_CSV)}
    now = datetime.now().isoformat(timespec="seconds")
    new_rows = []

    for i, row in enumerate(leads):
        if limit and len(new_rows) >= limit:
            break
        lid = f"L{i+1:05d}"
        if lid in existing:
            continue
        variant = deterministic_variant(lid)
        new_rows.append({
            "lead_id": lid,
            "business_name": row.get("business_name", ""),
            "to_email": row.get("to_email", ""),
            "website": row.get("website", ""),
            "city": row.get("city", ""),
            "industry": row.get("industry", ""),
            "website_score": row.get("website_score", ""),
            "demo_url": row.get("demo_url", ""),
            "variant": variant,
            "assigned_at": now,
        })

    all_rows = read_csv(ASSIGN_CSV) + new_rows
    write_csv(ASSIGN_CSV, all_rows, ASSIGN_FIELDS)

    counts = Counter(r["variant"] for r in new_rows)
    total_counts = Counter(r["variant"] for r in all_rows)
    print(f"Split complete.")
    print(f"  New assignments: {len(new_rows)} (A={counts['A']}, B={counts['B']})")
    print(f"  Total assigned:  {len(all_rows)} (A={total_counts['A']}, B={total_counts['B']})")
    print(f"  Output: {ASSIGN_CSV}")

def cmd_generate():
    assignments = read_csv(ASSIGN_CSV)
    if not assignments:
        print("ERROR: No assignments found. Run --split first.")
        sys.exit(1)

    a_rows, b_rows = [], []
    for row in assignments:
        base = {
            "lead_id": row["lead_id"],
            "business_name": row["business_name"],
            "to_email": row["to_email"],
            "website": row["website"],
            "city": row["city"],
            "industry": row["industry"],
            "demo_url": row["demo_url"],
        }
        if row["variant"] == "A":
            base["variant"] = "A"
            base["subject"] = subject_a(row)
            base["body"] = body_short_a(row)
            a_rows.append(base)
        else:
            base["variant"] = "B"
            base["subject"] = subject_b(row)
            base["body"] = body_long_b(row)
            b_rows.append(base)

    write_csv(VARIANT_A, a_rows, VARIANT_FIELDS)
    write_csv(VARIANT_B, b_rows, VARIANT_FIELDS)
    print(f"Generated variant emails.")
    print(f"  Variant A: {len(a_rows)} emails -> {VARIANT_A}")
    print(f"  Variant B: {len(b_rows)} emails -> {VARIANT_B}")
    print()
    print("--- Variant A sample (ultra-short, direct problem, reply CTA) ---")
    if a_rows:
        s = a_rows[0]
        print(f"  Subject: {s['subject']}")
        print(f"  Body:\n    " + s["body"].replace("\n", "\n    "))
    print()
    print("--- Variant B sample (compliment-first, specific, book-call CTA) ---")
    if b_rows:
        s = b_rows[0]
        print(f"  Subject: {s['subject']}")
        print(f"  Body:\n    " + s["body"].replace("\n", "\n    "))

def cmd_status():
    assignments = read_csv(ASSIGN_CSV)
    results = read_csv(RESULTS_CSV)

    if not assignments:
        print("No assignments yet. Run --split first.")
        return

    counts = Counter(r["variant"] for r in assignments)
    cities = Counter(r["city"] for r in assignments)
    industries = Counter(r["industry"] for r in assignments)

    result_counts = Counter()
    for r in results:
        result_counts[(r["variant"], r["outcome"])] += 1

    print(f"=== A/B Test Status ===")
    print(f"Total assigned: {len(assignments)}")
    print(f"  Variant A: {counts['A']}")
    print(f"  Variant B: {counts['B']}")
    print(f"  Split ratio: {counts['A']/(counts['A']+counts['B'])*100:.1f}% / {counts['B']/(counts['A']+counts['B'])*100:.1f}%")
    print()
    print(f"Results logged: {len(results)}")
    for v in ("A", "B"):
        wins = result_counts.get((v, "win"), 0)
        losses = result_counts.get((v, "loss"), 0)
        total = wins + losses
        rate = f"{wins/total*100:.1f}%" if total else "n/a"
        print(f"  Variant {v}: {wins} wins / {total} sent ({rate} conversion)")
    print()
    print(f"Top 5 cities:")
    for city, cnt in cities.most_common(5):
        print(f"  {city}: {cnt}")
    print(f"Industries:")
    for ind, cnt in industries.most_common(10):
        print(f"  {ind}: {cnt}")

    va_exists = VARIANT_A.exists()
    vb_exists = VARIANT_B.exists()
    print()
    print(f"Variant A emails generated: {'yes' if va_exists else 'no'}")
    print(f"Variant B emails generated: {'yes' if vb_exists else 'no'}")

def cmd_results():
    results = read_csv(RESULTS_CSV)
    if not results:
        print("No results logged yet. Use --log-result LEAD_ID A|B win|loss")
        return

    a_wins = sum(1 for r in results if r["variant"] == "A" and r["outcome"] == "win")
    a_total = sum(1 for r in results if r["variant"] == "A")
    b_wins = sum(1 for r in results if r["variant"] == "B" and r["outcome"] == "win")
    b_total = sum(1 for r in results if r["variant"] == "B")

    a_rate = a_wins / a_total * 100 if a_total else 0
    b_rate = b_wins / b_total * 100 if b_total else 0

    print(f"=== A/B Test Results ===")
    print(f"Variant A (ultra-short, direct, reply CTA):")
    print(f"  Sent: {a_total}  Wins: {a_wins}  Rate: {a_rate:.1f}%")
    print(f"Variant B (compliment-first, specific, book-call CTA):")
    print(f"  Sent: {b_total}  Wins: {b_wins}  Rate: {b_rate:.1f}%")
    print()

    if a_rate > b_rate:
        print(f"  Leader: Variant A (+{a_rate - b_rate:.1f}pp)")
    elif b_rate > a_rate:
        print(f"  Leader: Variant B (+{b_rate - a_rate:.1f}pp)")
    else:
        print(f"  Tied.")

    test = chi_square_test(a_wins, a_total, b_wins, b_total)
    print()
    print(f"Statistical significance (chi-square, 1 df):")
    print(f"  chi2 = {test['chi2']}, p = {test['p_approx']}")
    if test["significant"]:
        print(f"  SIGNIFICANT at p < 0.05. Winner is statistically reliable.")
    else:
        reason = test["reason"] if test["reason"] else "not significant"
        print(f"  NOT significant: {reason}")
        needed = max(0, 100 - min(a_total, b_total))
        if needed > 0:
            print(f"  Need {needed} more sends per variant to reach minimum sample size.")

def cmd_log_result(lead_id: str, variant: str, outcome: str):
    variant = variant.upper()
    outcome = outcome.lower()
    if variant not in ("A", "B"):
        print(f"ERROR: variant must be A or B, got '{variant}'")
        sys.exit(1)
    if outcome not in ("win", "loss"):
        print(f"ERROR: outcome must be win or loss, got '{outcome}'")
        sys.exit(1)

    # Verify lead exists in assignments
    assignments = read_csv(ASSIGN_CSV)
    match = [r for r in assignments if r["lead_id"] == lead_id]
    if not match:
        print(f"WARNING: lead_id '{lead_id}' not found in assignments. Logging anyway.")
    elif match[0]["variant"] != variant:
        print(f"WARNING: lead_id '{lead_id}' is assigned to variant {match[0]['variant']}, not {variant}.")

    existing = read_csv(RESULTS_CSV)
    existing.append({
        "lead_id": lead_id,
        "variant": variant,
        "outcome": outcome,
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_csv(RESULTS_CSV, existing, RESULT_FIELDS)
    print(f"Logged: {lead_id} variant={variant} outcome={outcome}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Cold Email A/B Test System")
    p.add_argument("--split", action="store_true", help="Assign leads to A/B groups")
    p.add_argument("--limit", type=int, default=None, help="Max leads to split (with --split)")
    p.add_argument("--generate", action="store_true", help="Generate variant emails")
    p.add_argument("--status", action="store_true", help="Show split stats")
    p.add_argument("--results", action="store_true", help="Show conversion comparison")
    p.add_argument("--log-result", nargs=3, metavar=("LEAD_ID", "VARIANT", "OUTCOME"),
                   help="Log outcome: LEAD_ID A|B win|loss")

    args = p.parse_args()
    ran = False

    if args.split:
        cmd_split(args.limit)
        ran = True
    if args.generate:
        cmd_generate()
        ran = True
    if args.status:
        cmd_status()
        ran = True
    if args.results:
        cmd_results()
        ran = True
    if args.log_result:
        cmd_log_result(*args.log_result)
        ran = True

    if not ran:
        p.print_help()

if __name__ == "__main__":
    main()
