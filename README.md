# Night Watch — World Cup wake-up planner

A single-page planner for following a team through the 2026 World Cup knockouts
without wrecking your sleep. Pick a team and it shows the run of fixtures ahead,
each one's win probability, the projected opponents, and the local kick-off time
with a sleep verdict (all times Singapore, SGT). Built for one purpose: deciding
which 3 a.m. games are actually worth getting up for.

**Live:** https://martinvagle.github.io/wc-night-watch/

The page is one self-contained HTML file. It reads a small `wc-data.json` for
live ratings and results; with no data file it still works from built-in priors.

## How the numbers work

- **Match win chances** come from team ratings on an Elo scale: the win
  probability is `1 / (1 + 10^((Rb - Ra) / 400))`. No draw term — each fixture is
  scored as win vs. not-win, which is what the group-stage fork ("win = 1st,
  draw or loss = 2nd") needs.
- **Ratings** start from built-in priors and, when live market data is present,
  are calibrated so the simulated tournament title odds match Polymarket's
  outright-winner market. Only genuine contenders (market chance ≥ 2%) are
  calibrated; longshots keep their priors so they can't distort the table.
- **Title odds** come from a Monte Carlo run over the actual bracket
  (`wc_sync.py`), reusing the same ratings.
- **Projected opponents** deep in the bracket are the favourite of each opposing
  region. They get fuzzier the further out you look, so treat semi/final names as
  placeholders that sharpen as real results land.

You can tap a match's Won / Lost as it finishes; the path, odds, and sleep list
redraw and save in your browser.

## Files

| File | Purpose |
|------|---------|
| `index.html` | The planner. Self-contained; set `DATA_URL` near the top to point at the data file. |
| `wc-data.json` | Live data the page loads (ratings, group seeds, third-place allocation, results). Kept fresh by the Action. |
| `wc_sync.py` | Rebuilds `wc-data.json`: reads market odds, calibrates ratings, simulates the bracket. |
| `requirements.txt` | Python dependency (`requests`). |
| `.github/workflows/wc-sync.yml` | Scheduled GitHub Action that runs the sync and commits the result. |

## Run the sync locally

```bash
pip install -r requirements.txt
python wc_sync.py                 # writes ./wc-data.json (live market + priors)
python wc_sync.py --dry-run       # print, don't write
python wc_sync.py --no-network    # rebuild from built-in priors only
python wc_sync.py --no-polymarket # skip market calibration
```

Polymarket needs no key. Two optional sources enrich the data if you provide
tokens as environment variables:

- `FOOTBALL_DATA_TOKEN` — results and standings from [football-data.org](https://www.football-data.org/)
- `ODDS_API_KEY` — per-match moneylines from [the-odds-api.com](https://the-odds-api.com/)

With no tokens set, those paths are skipped and the script falls back to priors,
so it never breaks.

## Deploy on GitHub Pages

1. **Repo** → push these files to a public repo (Pages is free on public repos).
2. **Action write access** → Settings → Actions → General → Workflow permissions
   → *Read and write permissions*. Without this the sync can't commit back.
3. **Pages** → Settings → Pages → Source: *Deploy from a branch* → `main`, folder
   `/ (root)`. The site goes live at `https://<user>.github.io/<repo>/`.
4. **First run** → Actions tab → `wc-sync` → *Run workflow*. After that the cron
   refreshes `wc-data.json` every 3 hours.

To host the page elsewhere and keep only the data on GitHub, set `DATA_URL` in
`index.html` to the raw URL of `wc-data.json` — it serves with open CORS.

## Notes

- GitHub pauses scheduled Actions after ~60 days with no commits to the repo (it
  emails first).
- During the knockout rounds, tighten the cron in `wc-sync.yml` from
  `0 */3 * * *` to `*/30 * * * *` for 30-minute refreshes.
- The Polymarket title-odds parser is verified against the live Gamma API. The
  football-data and the-odds-api paths were written without live access and may
  need a small tweak the first time you wire in their tokens; each logs what it
  finds.

This is a personal planner. Market prices are used only as probability signals;
nothing here is betting advice.
