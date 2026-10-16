# My Mosque Pro — daily jamaat times collector

Every morning this checks every mosque's timetable, reads the jamaat times,
checks they make sense for that mosque's location and date, and saves them to
your database. The app reads them from there.

**How each website is read — "recipes".** The first time a mosque's website is
seen, Claude looks at it once and writes down where its jamaat times are (which
table and columns, which boxes on the page, which monthly PDF, or its Mawaqit
screen). From then on the collector follows that recipe with plain code every
day — no AI, no cost. A recipe is only accepted if its times pass the
sunrise/sunset checks *and* match Claude reading the same page directly. If a
site is redesigned and the recipe stops fitting, that day's times are read by AI
instead and the recipe is learned again the next morning.

```
GitHub Actions (free, runs daily at ~4am)
  └─ collector  ──reads──▶  mosque websites, timetable PDFs/photos, Masjidbox/Mawaqit pages
        │                     (robots.txt respected — blocked sites are never fetched)
        ├─ Claude API: reads the timetable (only when it has changed)
        ├─ validation: order of prayers, sunrise / midday / sunset for that mosque
        ▼
  Supabase database (free tier)  ◀──reads (read-only key)──  My Mosque Pro app
```

## What you need (about 30 minutes, once)

| Account | What for | Cost |
|---|---|---|
| **Supabase** (supabase.com) | The database | Free tier |
| **GitHub** (github.com) | Runs the collector every day | Free |
| **Anthropic API** (console.anthropic.com) | Reads timetables | Pay as you go — a few £ a month |
| **Google Places API**, optional | Finds websites for mosques that have none on file | Google's monthly free usage should cover it (check current pricing) |
| **Brave Search API**, optional | Alternative to Google Places | Free/low-cost tier |

## Setup, step by step

1. **Create the database.** In Supabase: *New project* (region: London). Open
   *SQL Editor → New query*, paste all of `supabase/schema.sql`, press *Run*.
2. **Get your keys.** Supabase → *Project Settings → API*: copy the **Project URL**,
   the **anon public** key (for the app) and the **service_role** key (secret — only for GitHub).
3. **Get a Claude API key.** console.anthropic.com → *API Keys → Create key*. Add
   £10 of credit and set a monthly spend limit (e.g. £15) under *Billing → Limits*.
4. **Put this folder on GitHub.** github.com → *New repository* (private) →
   upload these files (keep the folder structure, including `.github/workflows`).
5. **Add the secrets.** In the repository: *Settings → Secrets and variables →
   Actions → New repository secret*, add:
   - `SUPABASE_URL` — the Project URL
   - `SUPABASE_SERVICE_KEY` — the service_role key
   - `ANTHROPIC_API_KEY` — the Claude key
   - `GOOGLE_PLACES_KEY` — optional: Google Cloud → enable **Places API (New)** → create an API key (restrict it to that API)
   - `BRAVE_SEARCH_KEY` — optional, used only if there's no Google key
6. **Load the mosques.** GitHub → *Actions* → **Load mosques (run once)** →
   *Run workflow*. This loads all 2,529 mosques, and every mosque website
   (1,397) as a source waiting for its recipe. The same run also tries three
   well-known mosque websites without saving anything — open the
   **try-sites** step to see the recipes it learned.
7. **First run.** GitHub → *Actions → Collect jamaat times → Run workflow*.
   Each run learns recipes for 150 more websites before collecting, so all
   1,397 are learned within about 10 days (raise "Websites to learn" on a manual
   run to go faster). The run page shows a summary: recipes learned, mosques
   updated, which failed and why.
8. **Connect the app** using `app/jamaat-client.js` with the Project URL and the
   **anon** key (never the service key).

From then on it runs by itself every day, and every Monday it looks up 150
mosques that have no website on file.

**Try one website first (optional).** On your computer, with only the Claude key set:
```
ANTHROPIC_API_KEY=… python -m collector.learn --try https://www.some-mosque.org/
```
It prints the recipe it learned, the timetable page, and the first days of times.

## What a recipe can be

| Kind | What it means | AI needed daily? |
|---|---|---|
| `table` | a dated timetable table on a page, or inside a monthly PDF (the new PDF link is found each month) | No |
| `fields` | a "today's prayer times" box | No |
| `mawaqit` | the mosque's Mawaqit screen | No |
| `page_ai` | times on the page but in no regular layout | Only when the page changes |
| `file_ai` | a scanned PDF or a photo of the timetable | Only when a new one is posted |
| `none` | no jamaat times found on the site (checked again monthly) | — |

Not covered: sites that only show times after running JavaScript in a browser
(the summary says "may need a browser"), and Masjidbox screens. Those mosques
keep showing calculated start times until a committee member or the community
adds jamaat times in the app.

## Growing coverage

- **Mosques without a website on file:** `collector.places` (weekly) asks
  Google Places for the mosque (matched by postcode), opens its website, and if
  the site has jamaat times saves that timetable page. Only the Google place ID
  is stored — Google's terms don't allow keeping other Places data such as the
  website field. The timetable page that's saved is an address on the mosque's
  own site, found by reading that site.
- Sources that never give valid jamaat times switch themselves off after two
  weeks of failures.
- **By hand:** add a row to `sources` in Supabase (*Table editor → sources →
  Insert*): the mosque's id and the timetable page URL. For mosques that post a
  new PDF each month at a predictable address, fill `url_pattern`, e.g.
  `https://masjid.org/wp-content/uploads/{YYYY}/{MM}/Timetable-{MONTH}-{YYYY}.pdf`.

## Rules it follows

- **robots.txt is obeyed.** A site that disallows bots, or refuses access, is
  marked `blocked` and never fetched again.
- **Polite:** one request every 3 seconds per website, identifies itself as
  `MyMosqueProBot`, a few requests per mosque per day.
- **Every time keeps its source.** The app shows "From finsburyparkmosque.org" etc.
- **Bad readings never reach the app.** Each day is checked against sunrise,
  midday and sunset for that mosque; a timetable where more than 30% of days fail
  is rejected entirely (usually means start times were read instead of jamaat).
- **Times expire.** Only today onwards is shown; nothing out of date appears.

Before launch: change the bot's contact address in `collector/config.py`
(`USER_AGENT`) to a page on your own website explaining the bot, and check the
terms of use of any large platform you collect from (e.g. Masjidbox, Mawaqit).

## Checking on it

- **GitHub → Actions**: each day's summary.
- **Supabase → Table editor → `collector_runs`**: history of every run.
- **`sources`** table: `recipe_status` (`ok`, `broken` = relearning tomorrow,
  `none` = nothing found, empty = not learned yet) and `last_status` per source — `ok`, `unchanged`,
  `no_times` (page has start times only), `invalid` (failed checks), `blocked`, `error`.

## Costs

- **Learning:** about 2–4 small Claude calls per website, once. For all 1,397
  websites, about £3–8 in total with the default model (Claude Haiku 5.5).
- **Daily:** recipe sites cost nothing. AI reads only happen for `page_ai` /
  `file_ai` sites when their timetable changes, and on the day a recipe breaks.
- Caps: `MAX_AI_CALLS` (400 per daily run) and `MAX_AI_CALLS_LEARN` (500 per
  learning run). Set a monthly spend limit in the Anthropic console too.

## Files

- `supabase/schema.sql` — database tables and security rules
- `collector/run.py` — the daily collector
- `collector/learn.py` — learns each website's recipe
- `collector/recipes.py` — follows recipes (tables, today boxes, Mawaqit, monthly PDFs)
- `collector/places.py` — Google Places lookup for mosques with no website
- `collector/discover.py` — the same using Brave Search instead
- `collector/validate.py`, `suncalc.py` — sanity checks
- `collector/load_seed.py`, `seed/` — the 2,529 mosques and first sources
- `app/jamaat-client.js` — how the app reads the times
- `tests/` — `python -m pytest -q`
