#!/usr/bin/env python3

from __future__ import annotations
"""
Cold Email System 2026 - Intent-Based, AI-Personalized
Sources:
  ALPHA274 - Inbox placement shifted from opens to engagement depth
  ALPHA275 - Intent-based timing 2-4x reply rate
  ALPHA276 - Gmail warns about tracking pixels - disable completely
  ALPHA278 - Step 1 email = 58% of all replies
  ALPHA279 - 80/20 split: AI for research + human for strategy
  ALPHA280 - First name tokens = spam flag. Business context = replies
  ALPHA281 - Too polished = AI detection flag. Casual beats perfect
  ALPHA282 - Warmup timeline increased to 14-21 days
  ALPHA284 - Volume consistency > volume size
  ALPHA285 - Subdomain mandatory for cold email
  ALPHA286 - 4-6 lines sweet spot

Generates cold email sequences following ALL 2026 best practices.

Usage:
    python3 cold_email_2026.py --generate-templates
    python3 cold_email_2026.py --prospect "Company Name" --pain "problem description"
    python3 cold_email_2026.py --batch /path/to/prospects.csv
    python3 cold_email_2026.py --audit /path/to/existing_emails.txt
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "EMAIL" / "cold_email_2026"
OUTPUT_DIR = BASE_DIR / "EMAIL" / "generated"
RULES_FILE = BASE_DIR / "EMAIL" / "cold_email_2026" / "RULES.md"

# 2026 Cold Email Rules (compiled from all alpha entries)
RULES_2026 = {
    "CRITICAL": [
        "NO tracking pixels - Gmail warns users (ALPHA276)",
        "NO first name tokens like {first_name} - spam flag (ALPHA280)",
        "Use subdomain for all cold email - protect main domain (ALPHA285)",
        "Plain text ONLY - no HTML formatting (ALPHA021/ALPHA281)",
        "Send from personal name not company name (ALPHA021)",
        "4-6 lines maximum (ALPHA286)",
        "58% of replies come from email #1 - invest there (ALPHA278)",
        "Warmup 14-21 days before sending (ALPHA282)",
        "Consistent daily volume, never sporadic blasts (ALPHA284)",
    ],
    "PERSONALIZATION": [
        "Business context > name insertion (ALPHA280)",
        "Reference specific company events, not generic compliments",
        "Intent signals: job changes, funding, tech stack changes (ALPHA275)",
        "Casual tone > polished - too polished = AI flag (ALPHA281)",
        "80% AI research, 20% human strategy (ALPHA279)",
    ],
    "STRUCTURE": [
        "Line 1: Why you're reaching out (business context)",
        "Line 2-3: The specific problem you solve",
        "Line 4: Proof (specific number or case study)",
        "Line 5: Soft CTA (question, not demand)",
        "NO signature blocks, NO company logos",
    ],
    "TIMING": [
        "Intent-based timing = 2-4x reply rate (ALPHA275)",
        "Send when prospect is actively researching (ALPHA288)",
        "Tuesday-Thursday 9-11am recipient timezone",
        "Follow up max 2 times, 3-4 days apart",
    ],
}

# The 6-Question Framework (from alpha)
SIX_QUESTION_FRAMEWORK = {
    "questions": [
        "What do you do?",
        "Who do you do it for?",
        "How do you do it?",
        "What problem do you solve?",
        "What proof do you have?",
        "What ROI do they get?",
    ],
    "rule": "Answer all 6 in under 100 words. That's your cold email."
}

# Templates by trigger event
TEMPLATES = {
    "leadership_change": {
        "subject": "quick note about {department}",
        "body": """saw {company} just brought on a new {role}.

whenever leadership changes, teams usually reassess their {area} stack within the first 90 days.

we helped {similar_company} cut their {metric} by {number} after a similar transition.

worth a quick chat to see if there's a fit?

{sender_first_name}""",
        "trigger": "New hire detected via theorg.com or LinkedIn",
        "reply_rate": "8-12%",
    },
    "funding_round": {
        "subject": "congrats on the raise",
        "body": """saw the {round_type} announcement. congrats.

most teams post-funding have {pain_point} as a top priority but limited bandwidth to execute.

we helped {similar_company} go from {before} to {after} in {timeframe} while they focused on hiring.

happy to share how if useful.

{sender_first_name}""",
        "trigger": "Funding announcement via Crunchbase or press",
        "reply_rate": "10-15%",
    },
    "tech_stack_change": {
        "subject": "noticed your {tech} migration",
        "body": """saw your team just moved to {new_tech}.

most companies underestimate the {pain_point} during that transition.

we built a {solution} specifically for teams making that switch. {specific_result} for {similar_company}.

want me to send over the case study?

{sender_first_name}""",
        "trigger": "Tech stack change via BuiltWith or job postings",
        "reply_rate": "6-10%",
    },
    "glassdoor_spike": {
        "subject": "been there - {topic}",
        "body": """noticed {company} has been getting some candid feedback lately.

when internal culture shifts happen, {department} teams usually need extra support to keep {metric} stable.

we helped {similar_company} maintain {result} through a similar period.

not sure if it's relevant but happy to share what worked.

{sender_first_name}""",
        "trigger": "Glassdoor rating drop >0.5 in 30 days",
        "reply_rate": "5-8%",
    },
    "job_posting_filled": {
        "subject": "quick question about {role_category}",
        "body": """noticed you recently filled the {role} position. new hires usually need {tool_category} set up in the first 30 days.

we help companies like {similar_company} get their new {role_type} productive {number}x faster with {solution}.

{specific_proof}.

would it save you time to see how it works?

{sender_first_name}""",
        "trigger": "Job posting removed after 30+ days (filled)",
        "reply_rate": "7-10%",
    },
    "competitor_layoff": {
        "subject": "saw the news about {competitor}",
        "body": """with {competitor} downsizing, their customers are probably looking for alternatives.

we can help you capture that demand. specifically:
- {benefit_1}
- {benefit_2}

already seeing {number} of their customers reaching out. happy to share the playbook.

{sender_first_name}""",
        "trigger": "Competitor layoff announcement",
        "reply_rate": "8-12%",
    },
    "generic_cold": {
        "subject": "{specific_observation} at {company}",
        "body": """noticed {company} is {specific_observation}.

most {industry} companies dealing with this end up {common_mistake}.

we helped {similar_company} {specific_result} instead.

worth 15 min to see if it applies to you?

{sender_first_name}""",
        "trigger": "General outbound with business context",
        "reply_rate": "3-5%",
    },
    "local_biz": {
        "subject": "your {business_type} website",
        "body": """checked out {company}'s website. looks like you're {observation}.

i help local {business_type} businesses {value_prop}.

just finished a project for {similar_biz} - they saw {specific_result} in the first {timeframe}.

want me to send over what we did?

{sender_first_name}""",
        "trigger": "Local business website audit",
        "reply_rate": "4-8%",
    },
}

# Follow-up templates (max 2)
FOLLOWUPS = {
    "followup_1": {
        "subject": "re: {original_subject}",
        "body": """following up on this.

{one_line_new_value}.

still interested in chatting?

{sender_first_name}""",
        "days_after": 3,
    },
    "followup_2": {
        "subject": "re: {original_subject}",
        "body": """last note on this.

{social_proof_line}.

if timing isn't right, no worries. just wanted to make sure you saw it.

{sender_first_name}""",
        "days_after": 7,
    },
}


def audit_email(email_text):
    """Audit an existing cold email against 2026 rules."""
    issues = []

    # Check length
    lines = [l for l in email_text.strip().split('\n') if l.strip()]
    if len(lines) > 8:
        issues.append(f"TOO LONG: {len(lines)} lines (max 6). Cut to 4-6 lines.")

    # Check for HTML
    if '<' in email_text and '>' in email_text:
        issues.append("HTML DETECTED: Use plain text only. Gmail flags HTML cold emails.")

    # Check for name tokens
    if '{first_name}' in email_text.lower() or 'hi {name}' in email_text.lower():
        issues.append("NAME TOKEN: {first_name} is now a spam flag. Use business context instead.")

    # Check for tracking pixel indicators
    tracking_words = ['tracking', 'pixel', 'open rate', 'click tracking']
    for w in tracking_words:
        if w in email_text.lower():
            issues.append(f"TRACKING: '{w}' detected. Remove all tracking - Gmail warns users.")

    # Check for AI-sounding language
    ai_words = ['leverage', 'utilize', 'comprehensive', 'robust', 'innovative', 'seamless',
                 'game-changer', 'cutting-edge', 'empower', 'delve', 'landscape', 'paradigm']
    for w in ai_words:
        if w in email_text.lower():
            issues.append(f"AI FLAG: '{w}' sounds AI-generated. Use casual language instead.")

    # Check for corporate sign-off
    corporate = ['best regards', 'kind regards', 'sincerely', 'warm regards', 'looking forward']
    for c in corporate:
        if c in email_text.lower():
            issues.append(f"CORPORATE: '{c}' is too formal. Just use your first name.")

    # Check for sycophancy
    syco = ['i hope this finds you well', 'hope you\'re doing well', 'hope this email']

    for s in syco:
        if s in email_text.lower():
            issues.append(f"FILLER: '{s}' wastes their time. Start with why you're reaching out.")

    # Check for excessive politeness
    if email_text.count('!') > 1:
        issues.append("EXCLAMATION MARKS: Max 1 per email. Too many = insincere.")

    score = max(0, 100 - (len(issues) * 15))

    return {
        "score": score,
        "issues": issues,
        "verdict": "PASS" if score >= 70 else "NEEDS WORK" if score >= 40 else "REWRITE",
        "line_count": len(lines),
    }


def generate_prospect_email(company, pain, trigger_type="generic_cold", **kwargs):
    """Generate a personalized cold email for a prospect."""
    template = TEMPLATES.get(trigger_type, TEMPLATES["generic_cold"])

    # Fill template
    email = template["body"]
    email = email.replace("{company}", company)

    for key, value in kwargs.items():
        email = email.replace(f"{{{key}}}", str(value))

    # Replace any unfilled variables with sensible defaults
    email = re.sub(r'\{[^}]+\}', '[CUSTOMIZE]', email)

    subject = template["subject"]
    subject = subject.replace("{company}", company)
    for key, value in kwargs.items():
        subject = subject.replace(f"{{{key}}}", str(value))
    subject = re.sub(r'\{[^}]+\}', '[CUSTOMIZE]', subject)

    return {
        "subject": subject,
        "body": email,
        "trigger_type": trigger_type,
        "expected_reply_rate": template["reply_rate"],
    }


def batch_generate(csv_path):
    """Generate emails for a batch of prospects from CSV."""
    results = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = row.get('company', row.get('Company', ''))
            pain = row.get('pain', row.get('Pain', ''))
            trigger = row.get('trigger_type', 'generic_cold')

            email = generate_prospect_email(company, pain, trigger, **row)
            results.append({
                "company": company,
                **email
            })

    return results


def write_templates_to_disk():
    """Write all templates and rules to disk."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    # Write rules
    rules_content = "# Cold Email Rules 2026\n\n"
    rules_content += f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}\n"
    rules_content += "**Sources:** ALPHA274-288, compiled from Instantly.ai, Saleshandy, Mailshake, Clay, Warmup Inbox, TrulyInbox\n\n"

    for category, rules in RULES_2026.items():
        rules_content += f"## {category}\n\n"
        for rule in rules:
            rules_content += f"- {rule}\n"
        rules_content += "\n"

    rules_content += "## 6-Question Framework\n\n"
    for i, q in enumerate(SIX_QUESTION_FRAMEWORK["questions"]):
        rules_content += f"{i+1}. {q}\n"
    rules_content += f"\n**Rule:** {SIX_QUESTION_FRAMEWORK['rule']}\n"

    with open(RULES_FILE, 'w', encoding='utf-8') as f:
        f.write(rules_content)

    # Write each template
    for name, template in TEMPLATES.items():
        filepath = TEMPLATES_DIR / f"template_{name}.md"
        content = f"# {name.replace('_', ' ').title()}\n\n"
        content += f"**Trigger:** {template['trigger']}\n"
        content += f"**Expected Reply Rate:** {template['reply_rate']}\n\n"
        content += f"## Subject Line\n\n`{template['subject']}`\n\n"
        content += f"## Body\n\n```\n{template['body']}\n```\n\n"
        content += "## Follow-up 1 (3 days later)\n\n"
        content += f"```\n{FOLLOWUPS['followup_1']['body']}\n```\n\n"
        content += "## Follow-up 2 (7 days later)\n\n"
        content += f"```\n{FOLLOWUPS['followup_2']['body']}\n```\n"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    # Write deliverability checklist
    checklist = TEMPLATES_DIR / "DELIVERABILITY_CHECKLIST.md"
    checklist_content = """# Cold Email Deliverability Checklist 2026

## Pre-Launch (Do ONCE)

- [ ] Buy subdomain for cold email (outreach.yourdomain.com) - ALPHA285
- [ ] Set up SPF record on subdomain
- [ ] Set up DKIM on subdomain
- [ ] Set up DMARC on subdomain
- [ ] Create Google Workspace on subdomain ($6/mo)
- [ ] Disable all tracking pixels - ALPHA276
- [ ] Set up warmup tool (MailForge $3/inbox or DeliverOn $49/inbox) - ALPHA336/337
- [ ] Wait 14-21 days for warmup - ALPHA282
- [ ] Configure daily send limit: 30 emails/day per inbox

## Per Campaign

- [ ] Plain text only, no HTML - ALPHA281
- [ ] Send from personal name - ALPHA021
- [ ] 4-6 lines max - ALPHA286
- [ ] No {first_name} tokens - ALPHA280
- [ ] Business context personalization - ALPHA280
- [ ] Casual tone (not polished) - ALPHA281
- [ ] Soft CTA (question not demand)
- [ ] First email is strongest - 58% of replies come from it - ALPHA278
- [ ] Max 2 follow-ups, 3-4 days apart
- [ ] Consistent daily volume - ALPHA284

## Infrastructure

- [ ] Warmup tool running continuously (even while sending)
- [ ] 500+ peer warmup network - ALPHA283
- [ ] Subdomain separate from main domain - ALPHA285
- [ ] Intent data source (Clay, Bombora, or manual) - ALPHA288
- [ ] Bounce rate under 3%
- [ ] Reply rate tracked (target 10%+) - ALPHA003
"""
    with open(checklist, 'w', encoding='utf-8') as f:
        f.write(checklist_content)

    return TEMPLATES_DIR


def main():
    parser = argparse.ArgumentParser(description="Cold Email System 2026")
    parser.add_argument("--generate-templates", action="store_true", help="Write all templates to disk")
    parser.add_argument("--prospect", type=str, help="Company name for email generation")
    parser.add_argument("--pain", type=str, help="Pain point to address")
    parser.add_argument("--trigger", type=str, default="generic_cold", help="Trigger type")
    parser.add_argument("--batch", type=str, help="CSV file of prospects")
    parser.add_argument("--audit", type=str, help="Audit an existing email")
    parser.add_argument("--audit-text", type=str, help="Audit email text directly")
    args = parser.parse_args()

    if args.generate_templates:
        output = write_templates_to_disk()
        print(f"\nTemplates written to: {output}")
        print(f"Files created:")
        for f in sorted(output.glob("*")):
            print(f"  {f.name}")
        print(f"\nRules: {RULES_FILE}")
        print(f"Templates: {len(TEMPLATES)} trigger-based templates")
        print(f"Follow-ups: {len(FOLLOWUPS)} follow-up templates")

    elif args.audit:
        with open(args.audit, 'r', encoding='utf-8') as f:
            text = f.read()
        result = audit_email(text)
        print(f"\nEMAIL AUDIT RESULTS")
        print(f"{'='*40}")
        print(f"Score: {result['score']}/100")
        print(f"Verdict: {result['verdict']}")
        print(f"Lines: {result['line_count']}")
        if result['issues']:
            print(f"\nIssues ({len(result['issues'])}):")
            for issue in result['issues']:
                print(f"  - {issue}")
        else:
            print("\nNo issues found. Email passes 2026 best practices.")

    elif args.audit_text:
        result = audit_email(args.audit_text)
        print(f"\nScore: {result['score']}/100 | Verdict: {result['verdict']}")
        for issue in result['issues']:
            print(f"  - {issue}")

    elif args.prospect:
        email = generate_prospect_email(args.prospect, args.pain or "", args.trigger)
        print(f"\n{'='*40}")
        print(f"SUBJECT: {email['subject']}")
        print(f"{'='*40}")
        print(email['body'])
        print(f"{'='*40}")
        print(f"Expected reply rate: {email['expected_reply_rate']}")

    elif args.batch:
        results = batch_generate(args.batch)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = OUTPUT_DIR / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["company", "subject", "body", "trigger_type", "expected_reply_rate"])
            writer.writeheader()
            writer.writerows(results)
        print(f"\nBatch emails written to: {output_file}")
        print(f"Total emails generated: {len(results)}")

    else:
        # Demo mode
        print("\nCold Email System 2026 - Demo Mode")
        print("="*40)

        # Generate all templates
        write_templates_to_disk()
        print(f"\nTemplates written to: {TEMPLATES_DIR}")

        # Demo audit
        sample = """Hi {first_name},

I hope this email finds you well! I wanted to reach out because our innovative, cutting-edge platform can help leverage your team's productivity.

We offer a comprehensive suite of tools that seamlessly integrates with your existing workflow. Our robust solution has helped 100+ companies achieve unprecedented results.

I'd love to schedule a quick call to discuss how we can empower your organization.

Best regards,
John Smith
VP of Sales
Acme Corp
john@acme.com
555-0123"""

        print("\n--- AUDITING SAMPLE EMAIL ---")
        result = audit_email(sample)
        print(f"Score: {result['score']}/100")
        print(f"Verdict: {result['verdict']}")
        for issue in result['issues']:
            print(f"  FAIL: {issue}")

        # Show correct version
        print("\n--- CORRECT VERSION ---")
        correct = generate_prospect_email("Acme Corp", "slow onboarding", "generic_cold",
            specific_observation="hiring 3 engineers this month",
            industry="SaaS",
            similar_company="Linear",
            specific_result="cut onboarding from 3 weeks to 4 days",
            common_mistake="losing their first 90 days to setup",
            sender_first_name="john")
        print(f"Subject: {correct['subject']}")
        print(correct['body'])


if __name__ == "__main__":
    main()
