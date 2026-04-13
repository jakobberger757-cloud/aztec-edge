# Aztec Edge — Fund Manager Watchlist

Weekly sales intelligence digest for your target fund managers. Monitors public sources for signals like fundraises, leadership changes, deal activity, and regulatory events — then translates each into an actionable BD angle for fund administration teams. Delivered to your inbox every Monday morning.

## How It Works

1. You paste your target company names (up to 100)
2. The system identifies each firm — industry, location, and an optimized search query
3. Every Monday at 3:00 AM EST, the pipeline runs automatically:
   - Searches Tavily for news on each company (last 7 days only)
   - Claude AI filters out irrelevant or stale articles, categorizes by signal type, and writes a one-sentence summary
   - Each signal gets a **🎯 BD angle** explaining why it matters for fund administration sales
   - A personalized HTML digest is emailed to each user — only when there's news worth reporting

## Signal Types

The pipeline categorizes every article into one of 10 signal types:

| Signal | What it means |
|--------|--------------|
| Fundraise | New fund filing, capital raise, fund close |
| New Fund | New vehicle launch or strategy expansion |
| Leadership Change | C-suite departure, new hire, board change |
| Deal Activity | Acquisition, exit, portfolio company transaction |
| M&A Activity | Firm-level merger or acquisition |
| Hiring | Job postings, team expansion |
| Regulatory | SEC filing, DOJ action, compliance event |
| New Office | Geographic expansion |
| Strategy | Strategic shift, market outlook, new business line |
| Press Release | Awards, rankings, general announcements |

## What's in This Repo

| File | Purpose |
|------|---------|
| `pipeline.py` | Main engine — weekly cron that searches, summarizes, and emails |
| `add_user.py` | Add users + company watchlists (interactive or headless) |
| `requirements.txt` | Python dependencies |
| `railway.toml` | Railway deployment config (cron: Monday 8AM UTC / 3AM EST) |
| `Procfile` | Railway process definition |

## Setup

### Prerequisites

You need accounts (all have free tiers) for:
- **Supabase** — database for users, companies, articles
- **Tavily** (tavily.com) — news search API
- **Anthropic** (console.anthropic.com) — Claude API for summarization
- **SendGrid/Twilio** (sendgrid.com) — email delivery

### Step 1: Set Environment Variables

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your-supabase-service-role-key"
export TAVILY_API_KEY="your-tavily-key"
export ANTHROPIC_API_KEY="your-anthropic-key"
export SENDGRID_API_KEY="your-sendgrid-key"
export FROM_EMAIL="your-verified-sender@email.com"
```

> **Important:** `SUPABASE_KEY` must be the **service_role** key (not anon). `FROM_EMAIL` must be a verified sender in SendGrid (Settings → Sender Authentication).

### Step 2: Add Yourself + Your Target List

```bash
pip install requests
python add_user.py
```

**If running locally (terminal):** Prompts you for name, email, and company names. Claude identifies each firm and shows you the match — you confirm, skip, or edit each one. Companies are enriched with industry, location, and an optimized search query for better news coverage.

**If running on Railway (headless):** Uses preset seed data and auto-confirms. Edit the hardcoded values in `main()` before deploying.

### Step 3: Test the Pipeline

```bash
python pipeline.py
```

Runs the full cycle immediately — searches news, filters with AI, and sends you an email. Check your inbox and spam folder.

### Step 4: Deploy to Railway

1. Push this folder to GitHub
2. Go to railway.app → New Project → Deploy from GitHub
3. Add all 6 environment variables in the Variables tab
4. Railway runs the pipeline automatically every Monday at 3:00 AM EST

### Step 5: Add Teammates

Run `add_user.py` again for each person. Each user gets their own personalized digest based on their company list. Companies shared across users are searched only once (deduplicated).

## Architecture

```
Monday 3AM EST (Railway cron)
    │
    ├── [1] Fetch users + watchlists from Supabase
    ├── [2] Deduplicate companies across all users
    ├── [3] Search Tavily for each company (last 7 days)
    ├── [4] Claude AI: filter, categorize, summarize, add BD angle
    ├── [5] Store articles in Supabase
    ├── [6] Build personalized HTML digest per user
    └── [7] Send via SendGrid (skip users with zero news)
```

### Database (Supabase)

| Table | Purpose |
|-------|---------|
| `aztec_edge_users` | User profiles (name, email, delivery prefs) |
| `aztec_edge_companies` | Watchlists with enrichment data (industry, location, search_query) |
| `aztec_edge_articles` | Found articles with signal type, summary, source, publish date |
| `aztec_edge_digests` | Send history (tracking what was delivered) |

Email normalization is enforced at the database level — a trigger auto-lowercases all emails on insert/update to prevent duplicate user issues.

## Cost Estimate

| Component | Cost |
|-----------|------|
| Supabase | Free tier |
| Tavily | Free tier (1,000 searches/month) |
| Claude API | ~$8–15 per weekly run at scale |
| SendGrid | Free tier (100 emails/day) |
| Railway | Free tier for cron jobs |
| **Total** | **~$30–60/month at 50 users** |

## Roadmap

- [x] Core pipeline: search → summarize → email
- [x] Company enrichment (industry, location, search query)
- [x] Recency filter (last 7 days only)
- [x] BD angle on every signal (🎯 Aztec angle)
- [x] Duplicate user prevention (email normalization)
- [x] Interactive + headless mode for add_user.py
- [ ] Event-level deduplication (consolidate multiple articles about the same event)
- [ ] React frontend for self-service signup
- [ ] Competitor Intelligence module (shared company list across all users)
- [ ] File upload (.xlsx/.csv) for importing target lists

## Troubleshooting

- **No email received:** Check SendGrid → Activity Feed. Also check spam. Verify your `FROM_EMAIL` is authenticated in SendGrid.
- **"No news" for all companies:** Smaller firms may not generate weekly news. Test with a well-known name like "Blackstone" first.
- **Duplicate emails:** If you received two emails, you may have duplicate user records. Check `aztec_edge_users` for case-variant email entries.
- **Rate limits:** Tavily free tier is 1,000 searches/month. At 50 unique companies weekly, that's ~200/month — well within limits.
