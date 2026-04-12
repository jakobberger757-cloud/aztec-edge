"""
Aztec Edge — Add User + Companies (with enrichment)
Run this once to seed your watchlist. Then the pipeline handles the rest.

Flow:
1. Enter your name and email
2. Paste your company names
3. System looks up each company via Claude to identify:
   - Full legal name
   - Industry / asset class
   - Location (HQ)
   - Brief description
   - Optimized search query for news monitoring
4. You review and confirm each match
5. Confirmed companies are saved to Supabase

Usage:
  python add_user.py

Requires:
  SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY
"""

import os
import json
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


def supabase_request(method, table, params=None, data=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    if method == "GET":
        resp = requests.get(url, headers=headers, params=params)
    elif method == "POST":
        resp = requests.post(url, headers=headers, json=data)
    resp.raise_for_status()
    return resp.json()


def add_user(name, email):
    """Create a user, or return existing one."""
    email = email.lower()
    existing = supabase_request("GET", "aztec_edge_users", params={
        "email": f"eq.{email}",
        "select": "id,name,email"
    })
    if existing:
        print(f"User already exists: {existing[0]['name']} ({existing[0]['email']})")
        return existing[0]

    user = supabase_request("POST", "aztec_edge_users", data={
        "name": name,
        "email": email,
        "frequency": "weekly",
        "delivery_day": "monday",
        "delivery_time_utc": "08:00"
    })
    print(f"Created user: {user[0]['name']} ({user[0]['email']})")
    return user[0]


# ---------------------------------------------------------------------------
# COMPANY ENRICHMENT VIA CLAUDE
# ---------------------------------------------------------------------------
BATCH_SIZE = 15  # Process companies in batches to avoid truncation/timeouts


def enrich_batch(company_names_batch, batch_num, total_batches):
    """
    Send a batch of company names to Claude for identification.
    Returns a list of enriched company dicts.
    """
    names_list = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(company_names_batch))

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "messages": [{
                    "role": "user",
                    "content": f"""I have a list of fund manager / financial services company names. For each one, identify the specific company and provide structured data.

Company names:
{names_list}

For EACH company, return:
- name: the full, correct company name (e.g., "Linden Capital Partners" not just "Linden")
- industry: one of [Private Equity, Private Credit, Real Estate, Infrastructure, Venture Capital, Growth Equity, Hedge Fund, Fund of Funds, Multi-Strategy, Other]
- location: headquarters city and state/country (e.g., "Chicago, IL" or "London, UK")
- description: one sentence describing what the firm does, including AUM if known
- search_query: an optimized search string for finding news about this specific firm. Include the full firm name in quotes plus distinguishing terms. Example: '"Linden Capital Partners" healthcare private equity'
- confidence: "high" if you're confident in the identification, "low" if the name is ambiguous or you're unsure

IMPORTANT: If you cannot confidently identify a company, set confidence to "unknown" and return null for industry, location, description, and search_query. Do NOT guess or fabricate details for companies you don't recognize. The user will see these results and can provide corrections manually.

Respond ONLY with a JSON array of exactly {len(company_names_batch)} objects, one per company in the same order as the input. No other text, no markdown fencing.

Example:
[
  {{
    "name": "Linden Capital Partners",
    "industry": "Private Equity",
    "location": "Chicago, IL",
    "description": "Healthcare-focused middle market PE firm with approximately $5B in cumulative commitments.",
    "search_query": "\\"Linden Capital Partners\\" healthcare private equity",
    "confidence": "high"
  }},
  {{
    "name": "Obscure Capital LLC",
    "industry": null,
    "location": null,
    "description": null,
    "search_query": null,
    "confidence": "unknown"
  }}
]"""
                }]
            },
            timeout=120
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"\n  [ERROR] Batch {batch_num} enrichment failed: {e}")
        print(f"  Falling back to basic mode for this batch.")
        return [{"name": n, "industry": None, "location": None,
                 "description": None, "search_query": None,
                 "confidence": "unknown"} for n in company_names_batch]


def enrich_companies(company_names):
    """
    Enrich company names in batches of BATCH_SIZE to avoid
    truncation, timeouts, and malformed JSON on large lists.
    """
    total = len(company_names)
    batches = [company_names[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    total_batches = len(batches)

    all_enriched = []
    for batch_num, batch in enumerate(batches, 1):
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} companies)...")
        results = enrich_batch(batch, batch_num, total_batches)
        all_enriched.extend(results)

    return all_enriched


# ---------------------------------------------------------------------------
# INTERACTIVE CONFIRMATION
# ---------------------------------------------------------------------------
def confirm_companies(enriched):
    """
    Show the user each enriched company and let them confirm, skip, or edit.
    Returns list of confirmed companies ready for Supabase.
    """
    confirmed = []
    print(f"\n{'=' * 60}")
    print(f"REVIEW YOUR COMPANIES ({len(enriched)} total)")
    print(f"{'=' * 60}")
    print("For each company: [Enter] = confirm, [s] = skip, [e] = edit name\n")

    for i, company in enumerate(enriched, 1):
        confidence_marker = {
            "high": "✓",
            "low": "?",
            "unknown": "✗"
        }.get(company.get("confidence", "unknown"), "?")

        print(f"  [{i}/{len(enriched)}] {confidence_marker} {company['name']}")
        if company.get("industry"):
            print(f"           Industry:  {company['industry']}")
        if company.get("location"):
            print(f"           Location:  {company['location']}")
        if company.get("description"):
            print(f"           About:     {company['description']}")
        if company.get("search_query"):
            print(f"           Query:     {company['search_query']}")
        if company.get("confidence") == "low":
            print(f"           ⚠ LOW CONFIDENCE — verify this is the right firm")
        if company.get("confidence") == "unknown":
            print(f"           ⚠ COULD NOT IDENTIFY — consider editing or skipping")

        choice = input("           → ").strip().lower()

        if choice == "s":
            print(f"           Skipped.\n")
            continue
        elif choice == "e":
            new_name = input("           New name: ").strip()
            if new_name:
                company["name"] = new_name
                company["search_query"] = f'"{new_name}"'
                print(f"           Updated to: {new_name}\n")
            confirmed.append(company)
        else:
            confirmed.append(company)
            print(f"           Confirmed.\n")

    return confirmed


def save_companies(user_id, companies):
    """Save confirmed companies to Supabase with all enrichment fields."""
    added = 0
    for company in companies:
        try:
            supabase_request("POST", "aztec_edge_companies", data={
                "user_id": user_id,
                "name": company["name"],
                "industry": company.get("industry"),
                "location": company.get("location"),
                "description": company.get("description"),
                "search_query": company.get("search_query"),
            })
            added += 1
            print(f"  ✓ {company['name']}")
        except Exception as e:
            print(f"  [SKIP] {company['name']}: {e}")
    return added


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    # Check required env vars
    missing = []
    if not SUPABASE_URL: missing.append("SUPABASE_URL")
    if not SUPABASE_KEY: missing.append("SUPABASE_KEY")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")

    if missing:
        print("ERROR: Missing environment variables:")
        for var in missing:
            print(f"  export {var}=your-value-here")
        return

    print("=" * 60)
    print("AZTEC EDGE — Add User + Watchlist")
    print("=" * 60)

    interactive = os.isatty(0)

    if interactive:
        name = input("\nYour name: ").strip()
        email = input("Your email: ").strip()

        if not name or not email:
            print("Name and email required.")
            return

        print("\nPaste your company names (one per line).")
        print("When done, type 'DONE' on a new line and press Enter.\n")

        raw_names = []
        while True:
            line = input()
            if line.strip().upper() == "DONE":
                break
            if line.strip():
                raw_names.append(line.strip())

        if not raw_names:
            print("No companies entered.")
            return
    else:
        print("\n[Headless mode] Using hardcoded seed data.")
        name = "Jakob"
        email = "Jakobberger757@gmail.com"
        raw_names = [
            "Blackstone",
            "KKR",
            "Cortland",
            "GenNx360",
            "Ares Management",
            "Apollo Global Management"
        ]

    user = add_user(name, email)

    # Deduplicate input
    raw_names = list(dict.fromkeys(raw_names))  # preserves order
    print(f"\n{len(raw_names)} unique companies entered.")

    # Enrich via Claude
    print("\nIdentifying companies (this takes 15-30 seconds)...")
    enriched = enrich_companies(raw_names)

    if not enriched:
        print("Enrichment returned no results. Exiting.")
        return

    if interactive:
        confirmed = confirm_companies(enriched)
    else:
        confirmed = enriched

    if not confirmed:
        print("\nNo companies confirmed. Exiting.")
        return

    # Save to Supabase
    print(f"\nSaving {len(confirmed)} companies to watchlist...")
    added = save_companies(user["id"], confirmed)

    print(f"\n{'=' * 60}")
    print(f"Done! {added} companies added to {name}'s watchlist.")
    print(f"Each company has been enriched with industry, location,")
    print(f"and an optimized search query for news monitoring.")
    print(f"The pipeline will pick these up on the next Monday run.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
