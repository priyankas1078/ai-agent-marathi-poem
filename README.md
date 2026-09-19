# Pune News Marathi Poem Site

A 100%-free, serverless daily agent that:

1. Scrapes the top 4–5 local news stories from Sakal's Pune edition (esakal.com).
2. Asks Google Gemini 1.5 Flash (free tier) to turn them into a 16-line
   (4 stanzas × 4 lines), rhythmic, rhyming Marathi poem.
3. Prepends the poem plus its source headlines to `poems.json` in the repo.
4. Publishes the updated `poems.json` to a free static website hosted on
   GitHub Pages, which renders it as a scrolling timeline (newest first).
5. Runs every morning at 08:00 IST for free using GitHub Actions cron —
   no server, no database, no paid hosting, $0 forever (within free-tier
   limits).

There is no email/chat delivery step — the "delivery mechanism" is simply
that the website always shows the latest poem at the top.

## How it works

```
scraper.py          -> fetch_pune_news()        top 4-5 Pune headlines (RSS -> HTML -> HTML fallback)
poem_generator.py   -> generate_marathi_poem()   16-line Marathi poem via Gemini 1.5 Flash
storage.py          -> save_poem_entry()         prepends {date, poem, headlines} to poems.json
main.py             -> run_pipeline()            orchestrates the above + logs
.github/workflows/daily_poem.yml                 runs main.py, then commits + pushes poems.json
index.html / style.css / app.js                  static frontend that fetches poems.json and renders it
```

Each day the workflow:

1. Runs `python main.py`, which scrapes news, generates the poem, and
   rewrites `poems.json` on the runner's local checkout (newest entry first).
2. Commits that updated `poems.json` and pushes it back to `main` using the
   workflow's automatically-provided `GITHUB_TOKEN` — no extra secret needed
   for this part.
3. GitHub Pages, which serves directly from `main`, picks up the new
   `poems.json` within a minute or two, and the live site shows the new
   poem at the top without you doing anything.

If scraping or poem generation fails, `main.py` exits non-zero and the
workflow run shows as **failed** in the Actions tab (nothing gets committed,
so a bad run never corrupts `poems.json`). Enable GitHub's own "Actions"
email notifications (see Troubleshooting) if you want to be told about
failures.

---

## 1. Get a free Google Gemini API key

1. Go to **Google AI Studio**: https://aistudio.google.com/app/apikey
2. Sign in with your Google account.
3. Click **"Create API key"** (choose "Create API key in new project" if you
   don't have a Google Cloud project yet).
4. Copy the generated key — it looks like `AIza...`. You will not be able to
   see it again in full, so save it somewhere safe for now (you'll paste it
   into a GitHub secret in step 2).
5. Gemini 1.5 Flash has a generous free tier (requests/day and
   requests/minute limits) that is more than enough for one poem per day —
   you will not be charged anything as long as you stay on the free tier and
   don't attach a billing account that enables paid usage.

---

## 2. Set up the GitHub repository

### 2.1 Create the repository — it must be **Public**

> **Important:** GitHub Pages on a free personal account only publishes
> sites from **public** repositories. Private-repo Pages requires GitHub
> Pro/Team/Enterprise. Since the only things this repo needs to keep secret
> (the Gemini API key) live in encrypted **GitHub Actions secrets** — never
> in a committed file — it's safe to make the repository public; nobody can
> read your API key from it.

1. On GitHub, click **New repository**.
2. Name it (e.g. `pune-news-poem-bot`), set visibility to **Public**, and
   create it.
3. (If you already created it as Private, you can switch it later at
   **Settings -> General -> Danger Zone -> Change repository visibility ->
   Change to public**.)

### 2.2 Push these files

From this project folder:

```bash
git init
git add .
git commit -m "Initial commit: Pune news -> Marathi poem static site"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

`.env` is already excluded via `.gitignore` — **never commit your real
`.env` file or API keys**. `poems.json` (starting as an empty `[]`) *should*
be committed — it's the site's data file and is safe to be public, since it
only ever contains poems and public news headlines.

### 2.3 Add the repository secret

1. In your GitHub repo, go to **Settings -> Secrets and variables ->
   Actions -> New repository secret**.
2. Add:

   | Secret name      | Value                                       |
   |--------------------|------------------------------------------------|
   | `GEMINI_API_KEY`   | The API key from Google AI Studio (step 1)     |

   That's the only secret required — the git push step in the workflow uses
   the built-in `GITHUB_TOKEN` that Actions provides automatically to every
   run, so there's nothing extra to configure for publishing.
3. Save it. GitHub encrypts this and only exposes it to workflow runs in
   this repository — it's never visible in logs, even though the repo is
   public.

### 2.4 Enable the workflow

The workflow file at `.github/workflows/daily_poem.yml` is picked up
automatically once pushed. It will:

- Run automatically every day at **08:00 IST (02:30 UTC)**.
- Can also be triggered manually any time via **Actions -> Daily Pune News
  Marathi Poem -> Run workflow** (this uses the `workflow_dispatch` trigger).
- Commit and push the updated `poems.json` back to `main` after each
  successful run (this requires `permissions: contents: write`, already set
  in the workflow file — no action needed from you).

> GitHub may disable scheduled workflows on a repository after ~60 days of
> repo inactivity (no commits). If the site stops updating after a long
> period of no code changes, open the **Actions** tab and click
> **"Re-enable workflow"**, or push any small commit — every automated
> `poems.json` commit also resets this clock, so once daily runs are
> flowing normally this shouldn't come up.

### 2.5 Enable GitHub Pages

1. In your GitHub repo, go to **Settings -> Pages** (left sidebar, under
   "Code and automation").
2. Under **"Build and deployment"**, set **Source** to **"Deploy from a
   branch"**.
3. Under **"Branch"**, select **`main`** and folder **`/ (root)`**, then
   click **Save**.
4. GitHub will show a banner "Your site is live at
   `https://<your-username>.github.io/<your-repo>/`" — it can take 1-2
   minutes after the first save (and after each new push) for the site to
   build and become reachable.
5. Bookmark that URL — that's your public poem timeline.

---

## 3. Test run locally before deploying

1. Install Python 3.11+ and the dependencies:

   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   # macOS/Linux:
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your real value:

   ```bash
   cp .env.example .env
   ```

   ```
   GEMINI_API_KEY=AIza...
   ```

3. Test each stage independently (recommended, so you can isolate failures):

   ```bash
   # Stage 1: just the scraper
   python scraper.py

   # Stage 2: just the poem generator (requires GEMINI_API_KEY in the environment)
   python poem_generator.py

   # Stage 3: just storage.py (writes a couple of test entries to poems.test.json,
   # doesn't touch your real poems.json)
   python storage.py
   ```

4. Run the full pipeline end-to-end:

   ```bash
   python main.py
   ```

   You should see timestamped log lines for each of the three steps, and a
   new entry should appear at the top of `poems.json` in this folder.

5. Preview the site locally by serving the folder over HTTP (opening
   `index.html` directly via `file://` won't work because `fetch()` can't
   load local files under that scheme):

   ```bash
   python -m http.server 8000
   ```

   Then open http://localhost:8000 in your browser — you should see the
   entry you just generated at the top of the timeline.

6. Once the local run succeeds, trigger the GitHub Actions workflow manually
   (**Actions tab -> Daily Pune News Marathi Poem -> Run workflow**) and
   confirm: the run turns green, a new commit appears on `main` adding a
   `poems.json` entry, and the change shows up on your live Pages URL a
   minute or two later.

---

## Troubleshooting

- **Pages site shows a 404 / never went live**: Double-check the repo is
  **Public** (step 2.1) — Pages silently fails to deploy on private repos
  under the free plan. Also confirm **Settings -> Pages -> Source** is set
  to "Deploy from a branch" / `main` / `/ (root)`.
- **Site loads but shows "No poems published yet" forever**: Open
  `https://<your-username>.github.io/<your-repo>/poems.json` directly in
  the browser — if it's `[]`, the workflow hasn't successfully committed a
  poem yet; check the Actions tab for a failed run. If the URL 404s, Pages
  hasn't finished deploying yet (wait a minute) or the branch/folder
  settings in step 2.5 are wrong.
- **Workflow run is green but no new commit appears**: Check the "Commit
  and push updated poems.json" step's log — if it printed "No changes to
  poems.json — nothing to commit", `main.py` ran successfully but
  `save_poem_entry` didn't produce a diff (shouldn't normally happen since
  each entry gets a fresh UUID and timestamp).
- **Workflow fails at the push step**: Confirm `permissions: contents:
  write` is present at the top of `.github/workflows/daily_poem.yml` (it is
  by default in this project) — without it, `GITHUB_TOKEN` is read-only and
  `git push` is rejected.
- **`NewsScrapeError`**: esakal.com likely changed its page layout or RSS
  path. `scraper.py` already tries three strategies (RSS, primary HTML
  selectors, generic HTML fallback) — check the Action logs to see which
  strategies were attempted, and update the selectors in
  `PRIMARY_SELECTOR_SETS` or `RSS_FEED_CANDIDATES` in `scraper.py`.
- **`PoemGenerationError`**: Gemini kept returning a poem that wasn't
  exactly 16 lines after 4 attempts. This is rare given the strict system
  prompt, but you can raise `MAX_GENERATION_ATTEMPTS` in
  `poem_generator.py` if it happens often.
- **Want to be emailed when a run fails**: GitHub sends failure
  notifications through its own notification system, not through this
  project. Go to your GitHub profile -> **Settings -> Notifications** and
  make sure email notifications are enabled for **"Actions"** (specifically
  for failed workflow runs on repositories you own).
- **Workflow didn't run at 08:00 IST**: GitHub's scheduled cron jobs can be
  delayed by a few minutes during high load — this is normal and on
  GitHub's side, not a bug in this project.

---

## Cost summary

| Component              | Cost                                            |
|-------------------------|--------------------------------------------------|
| GitHub Actions (public repo, free tier) | $0 (well within free minutes for one ~1 min job/day) |
| Google Gemini 1.5 Flash API (free tier)  | $0 (one request/day, far under free-tier limits)      |
| GitHub Pages hosting                     | $0 (free for public repositories)                     |
| Storage (`poems.json` in the repo)       | $0 (plain text in git, negligible size for 100 days)  |

**Total: $0/month**, sustainable for the full 100-day run (and beyond) as
long as you stay within each provider's free-tier limits.
