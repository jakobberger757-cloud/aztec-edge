# Aztec Edge — Fund Manager Watchlist

Weekly intelligence digest for your target fund managers. Searches public sources, summarizes with AI, and emails you every Monday morning.

## What's in this folder

| File | Purpose |
|------|---------|
| `pipeline.py` | Main engine — runs weekly, searches news, summarizes, emails |
| `add_user.py` | One-time setup — add yourself and your company list |
| `requirements.txt` | Python dependencies |
| `railway.toml` | Railway deployment config (cron: Monday 8AM UTC / 3AM EST) |
| `Procfile` | Railway process definition |

## Setup (15 minutes)

### Step 1: Get your API keys ready

You need 4 keys. Set them as environment variables:

```bash
export SUPABASE_URL="https://povananrxhmxfnbwrecm.supabase.co"
export SUPABASE_KEY="your-supabase-service-role-key"
export TAVILY_API_KEY="your-tavily-key"
export ANTHROPIC_API_KEY="your-anthropic-key"
export SENDGRID_API_KEY="your-sendgrid-key"
export FROM_EMAIL="your-verified-sender@email.com"
```

**Where to find each key:**
- **Supabase**: Project Settings → API → service_role key (NOT the anon key)
- **Tavily**: tavily.com → Dashboard → API Key
- **Anthropic**: console.anthropic.com → API Keys
- **SendGrid**: app.sendgrid.com → Settings → API Keys
- **FROM_EMAIL**: Must be a verified sender in SendGrid (Settings → Sender Authentication)

### Step 2: Add yourself to the watchlist

```bash
pip install requests
python add_user.py
```

It will prompt you for your name, email, and company list. Paste your companies one per line, then type `DONE`.

### Step 3: Test the pipeline locally

```bash
python pipeline.py
```

This runs the full pipeline immediately — searches news, summarizes, and sends you an email. Check your inbox (and spam folder).

### Step 4: Deploy to Railway

1. Push this folder to a GitHub repo
2. Go to railway.app → New Project → Deploy from GitHub
3. Select the repo
4. Go to Variables tab and add all 6 environment variables
5. Railway will automatically run the pipeline every Monday at 3AM EST

### Step 5: Adding more users

Run `add_user.py` again for each teammate. They'll get their own personalized digest based on their company list.

## Cost estimate

| Component | Cost |
|-----------|------|
| Supabase | Free tier |
| Tavily | Free tier (1,000 searches/month) |
| Claude API | ~$8-15 per weekly run at scale |
| SendGrid | Free tier (100 emails/day) |
| Railway | Free tier for cron jobs |
| **Total** | **~$30-60/month at 50 users** |

## Troubleshooting

- **No email received**: Check SendGrid → Activity Feed. Also check spam.
- **"No news" for all companies**: Your companies may be too small for news coverage. Try testing with a well-known firm like "Blackstone" first.
- **Rate limits**: Tavily free tier is 1,000 searches/month. At 50 unique companies weekly, that's ~200/month — well within limits.
