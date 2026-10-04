# Tesla Deal Monitor

Watches new **Model Y Premium** (RWD and AWD) pricing and purchase incentives every 4 hours on GitHub Actions. It opens a GitHub issue when the effective deal improves meaningfully, and publishes a dashboard to GitHub Pages.

## How it works

```
GitHub Actions (every 4h)
  ├─ Tesla inventory API ──► cheapest matching car per variant (price, Tesla discount, fees)
  ├─ tesla.com design page ─► advertised promo APR for the trim (best effort)
  ├─ offers.toml ───────────► incentives you entered by hand (APR, cash, perks)
  │
  ├─ deal math ─► cash price and a standard financing scenario (down payment, term, APR)
  ├─ compare against the last alerted offer ─► GitHub issue if savings ≥ threshold
  ├─ commit data/ (history, latest snapshot, state)
  └─ build web/ + data/ ─► GitHub Pages dashboard
```

**The effective deal** is one number per variant. By default it's the **financed total**: down payment + every monthly payment − the value of any perks. It reflects price cuts, inventory discounts, fee changes, cash incentives, and APR promos in a single figure. To compare on the out-the-door cash price instead, set `alerts.metric = "cash_price"`. Taxes and registration are excluded because they're the same either way.

**When it alerts:** an alert fires when the deal beats the offer from the last alert by at least `min_savings_usd` (default $500). Small improvements add up toward that threshold. If the deal gets worse, the comparison point moves up with it, so a later recovery still counts. The first run only records a baseline.

**Each alert issue includes:**
- What changed (price, Tesla discount, APR, term, incentives added or ended)
- Estimated savings vs. the prior offer, both financed and cash, plus the change in monthly payment
- A prior-vs-now table and the financing terms, quoting Tesla's own wording when the APR came from tesla.com
- The cheapest matching VIN, with a link

GitHub emails new issues to repo watchers, so watching the repo gets you notified.

## One-time setup

1. **Enable Pages:** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. **Get notified:** Watch the repo (at least "Issues"), and check that GitHub notification emails are on.
3. **Set your location** in `config.toml` (`zip`, `region`, `lat`, `lng`). The default is Raleigh, NC.
4. Optionally run it now: Actions → *Monitor Tesla deals* → Run workflow.

## Running where Tesla allows it

**Tesla blocks GitHub's cloud runners.** Every request from a GitHub-hosted runner, even to the tesla.com homepage, gets `HTTP 403 Access Denied`. You can confirm this with the *Diagnose Tesla access* workflow. The monitoring code is fine; it just needs to run from a normal network. Pick one:

**Option A: self-hosted runner (recommended).** Run the job on any always-on Mac, Linux box, or Windows/WSL machine on a home network.
1. Settings → Actions → Runners → **New self-hosted runner**, and follow the install commands on that machine. Run it as a service so it survives reboots.
2. Settings → Secrets and variables → Actions → **Variables** → New variable: `MONITOR_RUNNER` = `self-hosted`.
3. Actions → *Diagnose Tesla access* → Run workflow. You should see `200` for `api_v4`.

Only the Tesla-facing jobs use this runner, and they run on schedule, manual dispatch, or pushes to `main`, never on pull requests. Strangers can't run code on your machine by opening a PR against this public repo. The Pages deploy and tests still run on GitHub's runners.

**Option B: your own proxy.** If you already run an HTTP(S) proxy on a home network, add it as the repository **secret** `TESLA_PROXY` (e.g. `http://user:pass@host:port`). Tesla requests go through it; everything else runs on GitHub as usual.

Until one of these is set up, each run records the failure. After 3 failures in a row, a *monitor-health* issue opens.

## Configuration

| File | What it controls |
|---|---|
| `config.toml` | Location, financing scenario (down payment, term, fallback APR), alert threshold and metric, and the trackers to run |
| `offers.toml` | Incentives entered by hand, each with a date window. An APR entered here overrides the auto-detected one. |

## Adding used inventory (planned)

Trackers are independent. To watch used cars, uncomment the `my-premium-used` block in `config.toml`. The same inventory API serves used cars (`condition = "used"`), and filters like `max_odometer` and `min_year` already work. Used-car APRs can go in `offers.toml` under that tracker's id. History, alerts, and the dashboard pick up new trackers automatically. A future refinement for used cars would be ranking by price adjusted for mileage instead of price alone.

## Limitations

- Tesla's inventory API is undocumented and could change without notice. If it changes or access fails, a **monitor-health** issue opens and closes itself once a run succeeds. If the API responds but no trims match, the dashboard lists the trim names it saw so you can fix `trim_include`.
- APR detection reads Tesla's marketing text. It only accepts a rate whose own sentence names the tracked trim, and it quotes that sentence in alerts so you can check it. When it finds nothing, the deal uses `fallback_apr` or any APR entered in `offers.toml`.
- Promo APRs often require a minimum down payment or credit tier. The scenario in `config.toml` doesn't check whether you'd qualify.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m tracker run --dry-run   # prints alerts instead of opening issues
python -m tracker site            # builds ./site; serve with: python -m http.server -d site
python -m tracker.probe           # which tesla.com requests succeed from this network
```
