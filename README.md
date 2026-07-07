# AI Media Buyer v2 — Meta Growth Operator (zero cost, no Claude)

A senior media buyer that lives in Slack.
It runs on **GitHub Actions** — GitHub's own servers, on a schedule. Not Claude, not Cowork, not your laptop. Once it is set up, nothing you use has to be open. It just runs.

Cost: **$0**. GitHub Actions is free, GitHub Pages is free, the Meta API is free, a Slack app is free.

---

## What makes it senior (not a reporting bot)

- **Separates creative from audience.** A low CPA on a retargeting/catalogue ad is warm intent, not a creative win. Those get labelled `AUDIENCE-ASSISTED`, never "winner".
- **Benchmarks per segment, median + mean.** Each ad is judged against its own group: `COLD/VIDEO`, `COLD/IMAGE`, `WARM/CATALOGUE`. Never against blended.
- **Labels:** `SCALE OPPORTUNITY`, `AUDIENCE-ASSISTED`, `UNDERFUNDED` (do not kill), `CREATIVE FATIGUE` (only with evidence), `BAD CREATIVE`, `PERFORMS`, `WATCH`.
- **Funnel diagnosis:** auction vs creative vs landing vs checkout vs AOV.
- **Impact ranking:** the single **Biggest Offender** (most wasted money) and **Biggest Opportunity** each day.
- **Budget mix:** catalogue spend % and cold-only ROAS, so catalogue does not fake your account ROAS.

## Slack output (tags you every time: `@Ahmed Abdelshafi`)

- **#meta-growth-alerts** — daily summary, biggest offender, biggest opportunity, real fatigue.
- **#meta-actions** — a ranked P0/P1/P2 task list. Each card has Action, Reason, Evidence, Expected impact, Status.

Channel IDs are already wired in `config.json` (`C0BFN4UGYKY`, `C0BFN4PLT2A`).

## Dashboard (GitHub Pages)
Exec KPIs, per-segment benchmark pills, offender/opportunity cards, and a creative leaderboard with CPA-vs-segment, type, audience, label, hook/hold and diagnosis.

---

## Setup — one time, ~15 min

### 1. Push this folder to a private GitHub repo
```bash
git init && git add . && git commit -m "AI media buyer v2"
git branch -M main
git remote add origin https://github.com/YOURNAME/ai-media-buyer.git
git push -u origin main
```

### 2. Meta token (free, never-expiring)
Business Settings → Users → **System Users** → Add → assign your ad accounts → **Generate token** with scopes `ads_read`, `read_insights`, `business_management`. Copy it.
The token auto-discovers every active account, so Ourkids, Playmore, Decohome and any new one are covered with no code change.

### 3. Slack bot token (free)
<https://api.slack.com/apps> → Create app → **ourkids-talk** → OAuth & Permissions → add bot scope `chat:write` → Install → copy the `xoxb-…` token → in Slack type `/invite @YourApp` in #meta-growth-alerts and #meta-actions.

### 4. Add 2 GitHub secrets
Repo → Settings → Secrets and variables → Actions:
`META_ACCESS_TOKEN` = your Meta token, `SLACK_BOT_TOKEN` = your `xoxb-…`.

### 5. Dashboard
Settings → Pages → Deploy from branch → `main` / `/docs`. Live at `https://YOURNAME.github.io/ai-media-buyer/`.

### 6. Turn it on
Actions tab → enable workflows → **Run workflow** to test now. After that it runs itself at 06:00 UTC (9AM Cairo in summer) every day.

That is it. Hands-off from here.

---

## Tuning — `config.json`, no code
`winner_min_spend`, `winner_min_purchases`, `roas_target`, `fatigue_freq_increase`, `fatigue_ctr_drop`, and audience/type keyword lists (`WARM_KW`, `CAT_KW` in analyze.py). `accounts.mode`: `auto` (all active) or `manual` (the include list).

## Test locally
```bash
export META_ACCESS_TOKEN=... SLACK_BOT_TOKEN=...
python analyze.py --dry-run     # compute + write data.json, no Slack
python analyze.py --daily       # full run + Slack
```
Standard library only, no pip install. Currencies stay native (EGP/USD), never converted.
