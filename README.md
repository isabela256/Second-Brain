# Second Brain 
# On of its Functions Internship Monitor & Application Tracker

A local, always-on system that acts as a background agent for my internship search: it watches
my inbox, searches the web every morning, and keeps a running tracker of everything I've applied
to all running on my own machine, on a locally-hosted model, with no third-party AI service in
the loop.

## The brief I gave myself

This is the original spec I wrote for this project:

> Create a second brain that is constantly running on the main device, which is the computer 
> this source code is going to be in the terminal. The goal of this is for you to be able to
> monitor my workflow and help as a background agent and coworker.
>

## What actually got built


| Brief | Implementation |
|---|---|
| "constantly running on the main device... in the terminal" | A Python agent container (`agent/`) that runs continuously via Docker Compose, with an internal scheduler  no host cron needed, stays up as long as Docker is running. |
| "morning list of 20 Europe / 10 Japan / 10 Australia / 10 Argentina / 10 Ecuador" | `agent/config.py` encodes exact quotas and per-region rules; `agent/jobs/daily_job.py` runs the search every morning at 7:00 AM Ecuador time. |
| "posted in the last week or month... summer 2027... relevant to my major... I have to be a good candidate" | Every candidate listing is scraped from its real URL and judged against my actual CV profile by a locally-run LLM (`agent/ollama_client.py`)  recency, relevance, and fit are all checked before anything is included. |
| "help with a work visa and relocation money, housing, and a food stipend... Ecuador no stipend or visa since that's my home country" | Encoded per-region in `agent/config.py` (`requires_full_support`) — enforced by the same LLM classification step. |
| "monitor their responses, not just an acknowledgment" | `agent/gmail_client.py` scans recent Gmail threads; the model classifies each reply as a generic ack vs. something that needs a real follow-up (interview request, rejection, info request). |
| "to-do list... which internship has a follow-up... track how many applications" | `agent/tracker.py`  a SQLite database of every listing and application, with a `needs_followup` flag. Surfaced in the daily digest email, and queryable live through a chat tool in Open WebUI (`agent/tools/todo_tool.py`). |
| "validate that they're real, working, and current" | `agent/validator.py` — nothing goes in the digest unless its URL was actually fetched and returned real content. No listing is ever invented to hit a quota; a short day is reported honestly. |

## Architecture

The "Second Brain" is the scheduler in `agent/main.py` plus the shared record.
The internship monitor is one job hanging off it (`agent/jobs/daily_job.py`);
future jobs are more files in `agent/jobs/`.

One run of the internship job:

1. **Scan Gmail** for what you've applied to (`gmail_client.scan_applied_companies`)
   — LinkedIn Easy-Apply + ATS confirmations → `detected_applications` table.
2. **Search** — per region, per `country × query`, one **JSearch `/search-v2`**
   call (real dated postings + apply links; replaced SearXNG scraping).
3. **Screen** each posting: URL-dedupe → deterministic recency check (real
   timestamp) → `screen.py` decides is-internship / relevant-to-field in Python
   (a 3B model gets these wrong) → `qwen2.5:3b` judges only season, visa support,
   and candidate fit, locally.
4. **Sync the Google Sheet** (`sheets_client.py`) — new postings appended,
   existing ones refreshed, Status auto-advanced from Gmail. The sheet is the
   record; nothing is deleted.
5. **Notify** — a short "N new, M changed" email with the sheet link, only when
   something changed. (If the sheet can't be reached, it falls back to the full
   HTML digest.)

Components: **Ollama** (`qwen2.5:3b`, local, ~2 GB) · **Google APIs** (Gmail read/
send + Sheets + Drive) · **JSearch** (the only external call) · **SQLite**
(`data/tracker.db`) · **Open WebUI** (chat onto the model). SearXNG is still in
the compose file but unused.

## What it checks for

- **Python screen** (no LLM): must be an internship/co-op/working-student/graduate
  program, and in software / security / IT / data / mobile. Posted in the last 30
  days, checked against the posting's real timestamp.
- **Model gate** (`qwen2.5:3b`): for Summer 2027 (unstated → passes), and **either**
  the posting shows visa/relocation/housing/stipend support **or** the employer is
  a known sponsor — `config.py` `KNOWN_SPONSOR_COMPANIES` (Revolut, QuantCo,
  Optiver, CERN, IMC, DRW, SIG, Citadel, BofA, BlackRock, ASML) plus Japan's
  `sponsors` (Amazon, HENNGE, Microsoft).
- **Soft caveats** (shown, never a reject): "check you meet the requirements",
  "visa support not stated — verify before applying", "already applied to X —
  confirm this is a different role/location" (applications differ by location).
- **Australia is weak** — Nov–Feb season, work-rights requirements. One query.
- **Ecuador:** same bar minus the visa/support requirement (home country).
- Nothing is invented to hit a quota. A short day is a short day.

Edit `agent/config.py` any time to change the profile, regions, quotas, queries, or requirements.

## The sheet

On first run the agent creates **"Second Brain — Internships"** in your Google
Drive (or set `SHEET_ID` in `.env` to use an existing one) and logs the link.
One row per posting:

`Date found | Region | Company | Role | Location | Posted | Apply link | Support | Caveats | Status | Last seen | Last update | Notes`

- **Status** starts `New`. You move it by hand (`Applied`, `Interview`,
  `Rejected`, `Skip`). The agent only auto-advances a row still at `New`/blank:
  → `Applied?` when a confirmation email for that company shows up,
  → `Reply: <kind>` when a real reply lands. Once you've set a status the agent
  leaves it alone.
- **Notes** is yours; the agent never touches it.
- Rows are never deleted. `Last seen` tells you if a posting is still live.

## One-time setup


### 1. Docker

Make sure Docker and Docker Compose are installed and running.

### 2. JSearch API key (the job source)

Sign up at <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>, subscribe to the free
**Basic** plan (~200 requests/month), and copy your key. Then:

```bash
cp .env.example .env
# edit .env and set RAPIDAPI_KEY=... (and GMAIL_TO=your real address)
```

Without a key the daily run logs an error and finds nothing. The digest runs
**every other day** (`day="1-31/2"` in `agent/main.py`) — one run uses ~13 of the
~200 monthly calls, so daily would exhaust the free tier mid-month.

### 3. Google auth (Gmail + Sheets + Drive)

Download an OAuth client (Desktop app) from Google Cloud Console to
`secrets/credentials.json`, then on your host:

```bash
pip install google-auth-oauthlib google-api-python-client
python agent/gmail_auth_setup.py   # opens a browser, writes secrets/token.json
```

This grants Gmail read/send, Sheets, and `drive.file` (the agent can only see
files it creates). **Re-run it whenever `config.py` `GOOGLE_SCOPES` changes** —
delete `secrets/token.json` first.

### 4. Your CV profile

Copy `secrets/cv_profile.example.txt` to `secrets/cv_profile.txt` and replace it
with your real summary (education, skills, experience, work authorization, home
country). Without it, `candidate_is_qualified` is judged against a placeholder.

### 5. Start the stack


## Known limitations

- **JSearch coverage is uneven.** Germany / Netherlands / UK have real internship
  inventory; Japan, Australia, Argentina, and especially Ecuador return very few
  postings, and `country=` filtering is loose (a `country=ar` search often returns
  US roles, which then fail the visa/season checks).
- **Model choice.** `qwen2.5:3b` is the default (~2 GB, pulled on first run) —
  much steadier than `llama3.2` at structured JSON verdicts, and it fits in the
  ~7 GB this machine has. `qwen2.5:7b` was tried and is not viable here: CPU-only
  inference blew past the request timeout and pushed the box into swap. Bump
  `OLLAMA_MODEL` only if you add RAM or a GPU. Still a small model — spot-check the
  digest.
- The already-applied filter is company-level and email-driven (~85% coverage) —
  it misses applications that never send a confirmation email.

## Data

- `data/tracker.db` — SQLite database of listings seen, applications logged, and
  companies detected as already-applied from Gmail. Gitignored (personal data).
- `secrets/` — Gmail credentials/token, CV profile. Gitignored, never commit this.

## Logging an application

There's no separate UI for this yet — the fastest way to log one is directly:

```bash
docker compose exec agent python -c "
import tracker; tracker.init_db()
tracker.add_application(url='https://...', region='europe', company='...', role_title='...')
"
```

## Stopping

```bash
docker compose down
```

(Add `-v` only if you also want to wipe the Ollama model cache and Open WebUI
data volumes — this does **not** touch `data/` or `secrets/`, which are bind
mounts.)
