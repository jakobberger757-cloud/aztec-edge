"""
Aztec Edge — Fund Manager Watchlist Pipeline
Runs weekly (Monday 3AM EST) via Railway cron.

Flow:
1. Pull all active users + their company watchlists from Supabase
2. Deduplicate companies across users
3. Search Tavily for news on each unique company (last 7 days)
4. Send results to Claude API for categorization + summarization
5. Store articles in Supabase
6. Build personalized HTML digest per user
7. Send via SendGrid/Twilio
"""

import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
import requests

# ---------------------------------------------------------------------------
# CONFIG — set these as environment variables in Railway
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")          # e.g. https://povananrxhmxfnbwrecm.supabase.co
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")           # service_role key (not anon)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "watchlist@aztecedge.com")

# ---------------------------------------------------------------------------
# SUPABASE HELPERS
# ---------------------------------------------------------------------------
def supabase_request(method, table, params=None, data=None):
    """Make a request to Supabase REST API."""
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
    elif method == "PATCH":
        resp = requests.patch(url, headers=headers, params=params, json=data)
    resp.raise_for_status()
    return resp.json()


def get_active_users():
    """Fetch all active users with their companies."""
    users = supabase_request("GET", "aztec_edge_users", params={
        "is_active": "eq.true",
        "select": "id,name,email"
    })
    for user in users:
        companies = supabase_request("GET", "aztec_edge_companies", params={
            "user_id": f"eq.{user['id']}",
            "is_active": "eq.true",
            "select": "id,name,industry,location,search_query"
        })
        user["companies"] = companies
    return users


def store_articles(articles):
    """Store articles in Supabase. Duplicates are rejected by the
    UNIQUE(company_id, url) constraint and caught here silently."""
    if not articles:
        return
    for article in articles:
        try:
            supabase_request("POST", "aztec_edge_articles", data=article)
        except requests.exceptions.HTTPError:
            # UNIQUE constraint violation = duplicate article, safe to skip
            pass


def record_digest(user_id, companies_with_news, total_articles, status="sent"):
    """Record that a digest was sent."""
    supabase_request("POST", "aztec_edge_digests", data={
        "user_id": user_id,
        "companies_with_news": companies_with_news,
        "total_articles": total_articles,
        "email_status": status
    })


# ---------------------------------------------------------------------------
# TAVILY NEWS SEARCH
# ---------------------------------------------------------------------------
def search_company_news(company_name, industry=None, location=None, search_query=None):
    """Search Tavily for recent news about a company.
    Uses the enriched search_query if available (set during add_user enrichment),
    otherwise falls back to building a query from name + industry + location."""
    if search_query:
        # Use the optimized query from enrichment (e.g., '"Linden Capital Partners" healthcare private equity')
        query = search_query + " news"
    else:
        # Fallback: build from raw fields
        query = f'"{company_name}"'
        if industry:
            query += f" {industry}"
        if location:
            query += f" {location}"
        query += " news announcements"

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {TAVILY_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "query": query,
                "search_depth": "advanced",
                "max_results": 10,
                "days": 7,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "exclude_domains": [
                    "facebook.com", "twitter.com", "instagram.com",
                    "reddit.com", "youtube.com", "tiktok.com"
                ]
            },
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        print(f"  [ERROR] Tavily search failed for '{company_name}': {e}")
        return []


# ---------------------------------------------------------------------------
# CLAUDE SUMMARIZATION
# ---------------------------------------------------------------------------
def summarize_company_news(company_name, articles):
    """Use Claude to categorize and summarize articles for a company."""
    if not articles:
        return []

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    articles_text = ""
    for i, article in enumerate(articles, 1):
        articles_text += f"""
Article {i}:
Title: {article.get('title', 'No title')}
URL: {article.get('url', '')}
Content: {article.get('content', 'No content')[:1500]}
Published: {article.get('published_date', 'Unknown')}
---"""

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
                "max_tokens": 2000,
                "messages": [{
                    "role": "user",
                    "content": f"""Analyze these articles about "{company_name}" (a fund manager / financial services firm). You are helping a business development team at a fund administration company identify actionable sales intelligence.

For each article that is ACTUALLY about this specific company (not a different company with a similar name), provide:
1. signal_type: one of [fundraise, leadership_change, deal_activity, hiring, press_release, new_fund, regulatory, new_office, strategy, ma_activity]
2. summary: one clear sentence about the key news
3. title: the article headline
4. url: the article URL
5. source: the publication name
6. published_date: the article's publish date in ISO 8601 format (e.g., "2026-04-07T00:00:00Z"). Use the date from the article metadata. If unknown, use null.
7. relevance: "high" if definitely about this company, "low" if uncertain
8. aztec_angle: one sentence explaining why this news matters for a fund administration BD team. Think about: Does this signal a potential need for new fund admin services? Could this mean they're launching a new fund that needs an administrator? Is there a leadership change that creates an opening for a new relationship? Is there regulatory pressure that increases their need for compliance support? If there's no clear BD angle, say "Monitor — no immediate BD trigger."

CRITICAL: Filter out articles that are NOT about this specific fund manager. For example, if the company is "Halifax Group" (a PE firm), ignore articles about Halifax, Nova Scotia.

RECENCY FILTER: Only include articles published within the last 7 days (since {cutoff_date}). If an article's published date is before this cutoff, or if the article describes events that clearly happened months or years ago, exclude it entirely regardless of relevance. When in doubt about the date, exclude it.

Respond ONLY with a JSON array. No other text. Example:
[
  {{"signal_type": "fundraise", "summary": "Filed Form D for new $500M credit vehicle.", "title": "...", "url": "...", "source": "PE Hub", "published_date": "2026-04-07T00:00:00Z", "relevance": "high", "aztec_angle": "New $500M vehicle will need a fund administrator — strong BD trigger to reach out now before they lock in a provider."}}
]

If no articles are relevant, respond with: []

Articles:
{articles_text}"""
                }]
            },
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        # Clean potential markdown fencing
        text = text.replace("```json", "").replace("```", "").strip()
        results = json.loads(text)
        # Filter to high relevance only
        return [r for r in results if r.get("relevance") != "low"]
    except Exception as e:
        print(f"  [ERROR] Claude summarization failed for '{company_name}': {e}")
        return []


# ---------------------------------------------------------------------------
# EMAIL BUILDER
# ---------------------------------------------------------------------------
def build_digest_html(user_name, company_results, total_monitored):
    """Build a clean HTML email digest."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    companies_with_news = len([c for c in company_results if c["articles"]])

    # Signal type colors
    signal_colors = {
        "leadership_change": ("#FFF3E0", "#E65100", "#FFB74D"),
        "fundraise": ("#E8F5E9", "#1B5E20", "#81C784"),
        "deal_activity": ("#E3F2FD", "#0D47A1", "#64B5F6"),
        "hiring": ("#F3E5F5", "#4A148C", "#BA68C8"),
        "press_release": ("#FFF8E1", "#F57F17", "#FFD54F"),
        "new_fund": ("#E0F2F1", "#004D40", "#4DB6AC"),
        "regulatory": ("#FCE4EC", "#880E4F", "#F48FB1"),
        "ma_activity": ("#E8EAF6", "#1A237E", "#7986CB"),
        "new_office": ("#EFEBE9", "#3E2723", "#A1887F"),
        "strategy": ("#F1F8E9", "#33691E", "#AED581"),
    }

    def signal_tag(signal_type):
        bg, text, border = signal_colors.get(signal_type, ("#F5F5F5", "#424242", "#BDBDBD"))
        label = signal_type.replace("_", " ").title()
        return f'<span style="display:inline-block;padding:2px 10px;font-size:11px;font-weight:600;font-family:monospace;letter-spacing:0.5px;text-transform:uppercase;background:{bg};color:{text};border:1px solid {border};border-radius:3px;">{label}</span>'

    # Build company sections
    company_sections = ""
    for result in company_results:
        if not result["articles"]:
            continue
        articles_html = ""
        for article in result["articles"]:
            aztec_angle = article.get("aztec_angle", "")
            angle_html = ""
            if aztec_angle:
                angle_html = f'<div style="font-size:13px;color:#8B6914;font-style:italic;margin-top:4px;">🎯 {aztec_angle}</div>'
            articles_html += f'''
            <div style="margin-bottom:10px;padding-left:12px;border-left:2px solid #C8A84E;">
                <div style="margin-bottom:4px;">
                    {signal_tag(article.get("signal_type", "press_release"))}
                    <span style="font-size:11px;color:#999;font-family:monospace;margin-left:8px;">via {article.get("source", "Unknown")}</span>
                </div>
                <div style="font-size:14px;color:#333;line-height:1.5;">{article.get("summary", "")}</div>
                {angle_html}
                <a href="{article.get("url", "#")}" style="font-size:12px;color:#C8A84E;text-decoration:none;font-family:monospace;">Read more →</a>
            </div>'''

        company_sections += f'''
        <div style="margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #E8E5DD;">
            <div style="font-size:16px;font-weight:700;color:#1A1A1A;margin-bottom:12px;">{result["company_name"]}</div>
            {articles_html}
        </div>'''

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F5F4F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:640px;margin:0 auto;padding:32px 20px;">
    <div style="background:#FAFAF8;border-radius:12px;padding:32px;border:1px solid #E0DDD5;">
        <!-- Header -->
        <div style="border-bottom:2px solid #C8A84E;padding-bottom:16px;margin-bottom:24px;">
            <div style="font-size:11px;color:#C8A84E;font-weight:700;letter-spacing:3px;text-transform:uppercase;font-family:monospace;margin-bottom:4px;">AZTEC EDGE</div>
            <div style="font-size:22px;font-weight:700;color:#1A1A1A;margin-bottom:4px;">Fund Manager Watchlist</div>
            <div style="font-size:13px;color:#888;font-family:monospace;">Week of {today} · {user_name}</div>
        </div>

        <!-- Summary bar -->
        <div style="background:#F0EDE5;border-radius:6px;padding:12px 16px;margin-bottom:24px;font-size:13px;color:#666;line-height:1.5;">
            {companies_with_news} of your {total_monitored} monitored companies had newsworthy activity this week.
            {total_monitored - companies_with_news} companies had no significant news.
        </div>

        <!-- Company sections -->
        {company_sections}

        <!-- Footer -->
        <div style="font-size:11px;color:#AAA;text-align:center;padding-top:16px;border-top:1px solid #E8E5DD;font-family:monospace;">
            AZTEC EDGE · Fund Manager Watchlist · Powered by public sources only
        </div>
    </div>
</div>
</body>
</html>'''

    return html


# ---------------------------------------------------------------------------
# SENDGRID EMAIL
# ---------------------------------------------------------------------------
def send_email(to_email, subject, html_content):
    """Send email via SendGrid/Twilio."""
    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": FROM_EMAIL, "name": "Aztec Edge"},
                "subject": subject,
                "content": [{"type": "text/html", "value": html_content}]
            },
            timeout=30
        )
        if resp.status_code in (200, 201, 202):
            print(f"  [OK] Email sent to {to_email}")
            return True
        else:
            print(f"  [ERROR] SendGrid returned {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"  [ERROR] Failed to send email to {to_email}: {e}")
        return False


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------
def run_pipeline():
    """Main pipeline execution."""
    print("=" * 60)
    print(f"AZTEC EDGE — Pipeline Run: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # 1. Get all active users and their companies
    print("\n[1/5] Fetching users and watchlists...")
    users = get_active_users()
    print(f"  Found {len(users)} active users")

    if not users:
        print("  No active users. Exiting.")
        return

    # 2. Deduplicate companies across all users
    print("\n[2/5] Deduplicating company list...")
    company_map = {}  # normalized_name -> {company_data, user_ids, company_ids}
    for user in users:
        for company in user["companies"]:
            # Normalize name for dedup
            normalized = company["name"].strip().lower()
            key = hashlib.md5(normalized.encode()).hexdigest()
            if key not in company_map:
                company_map[key] = {
                    "name": company["name"],
                    "industry": company.get("industry"),
                    "location": company.get("location"),
                    "search_query": company.get("search_query"),
                    "company_ids": [],
                    "user_ids": []
                }
            company_map[key]["company_ids"].append(company["id"])
            if user["id"] not in company_map[key]["user_ids"]:
                company_map[key]["user_ids"].append(user["id"])

    unique_companies = list(company_map.values())
    total_raw = sum(len(u["companies"]) for u in users)
    print(f"  {total_raw} total → {len(unique_companies)} unique companies")

    # 3. Search for news on each unique company
    print("\n[3/5] Searching for news...")
    for i, company in enumerate(unique_companies, 1):
        print(f"  [{i}/{len(unique_companies)}] Searching: {company['name']}")
        raw_articles = search_company_news(
            company["name"],
            industry=company.get("industry"),
            location=company.get("location"),
            search_query=company.get("search_query")
        )
        company["raw_articles"] = raw_articles
        article_count = len(raw_articles)
        print(f"    Found {article_count} raw results")

    # 4. Summarize with Claude (only companies with results)
    print("\n[4/5] Summarizing with Claude...")
    for company in unique_companies:
        if not company["raw_articles"]:
            company["processed_articles"] = []
            continue
        print(f"  Summarizing: {company['name']} ({len(company['raw_articles'])} articles)")
        processed = summarize_company_news(company["name"], company["raw_articles"])
        company["processed_articles"] = processed
        print(f"    → {len(processed)} relevant articles after filtering")

        # Store in Supabase
        for article in processed:
            for company_id in company["company_ids"]:
                # Use the source article's publish date, not crawl time.
                # found_at defaults to now() in the schema for crawl timestamp.
                source_pub_date = article.get("published_date")
                store_articles([{
                    "company_id": company_id,
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "source": article.get("source", ""),
                    "signal_type": article.get("signal_type", "press_release"),
                    "summary": article.get("summary", ""),
                    "published_at": source_pub_date  # actual article date, not crawl time
                }])

    # 5. Build and send personalized digests
    print("\n[5/5] Building and sending digests...")
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    for user in users:
        user_company_ids = [c["id"] for c in user["companies"]]

        # Gather this user's results
        user_results = []
        for company in unique_companies:
            # Check if any of this company's IDs belong to this user
            if any(cid in user_company_ids for cid in company["company_ids"]):
                user_results.append({
                    "company_name": company["name"],
                    "articles": company["processed_articles"]
                })

        companies_with_news = len([r for r in user_results if r["articles"]])
        total_articles = sum(len(r["articles"]) for r in user_results)

        if companies_with_news == 0:
            print(f"  {user['name']}: No news this week — skipping email")
            record_digest(user["id"], 0, 0, status="skipped_no_news")
            continue

        # Build email
        html = build_digest_html(
            user["name"],
            user_results,
            len(user["companies"])
        )

        subject = f"Fund Manager Watchlist — Week of {today}"
        success = send_email(user["email"], subject, html)

        record_digest(
            user["id"],
            companies_with_news,
            total_articles,
            status="sent" if success else "failed"
        )

    print("\n" + "=" * 60)
    print("Pipeline complete.")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline()
