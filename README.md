# SolPulse — Auto-updating Solana Ecosystem Dashboard

A comprehensive, **automatically-updating** report on the state of the Solana ecosystem, plus a
self-contained interactive dashboard. Built for the Superteam Canada bounty *"Develop Solana
Ecosystem Auto-Updating Report & Interactive Dashboard."*

**Live dashboard:** _(deploy via GitHub Pages — see below)_
**Machine-readable report:** [`data/latest.json`](data/latest.json) · history in [`data/history.jsonl`](data/history.jsonl)

## What it tracks (per bounty scope)

| Area | Metrics |
|---|---|
| **Network performance** | TPS (recent avg), avg slot time, current epoch + progress, absolute slot, block height, `getHealth` |
| **Validators & decentralization** | active vs delinquent count + %, total active stake, **Nakamoto coefficient**, top-10 stake share, top validators (stake + commission) |
| **Economic indicators** | SOL price + 24h change, market cap, 24h volume, ATH, circulating/total supply, **staking ratio** |
| **Ecosystem** | Solana **TVL**, top DeFi protocols by TVL (+1d change), **stablecoin** market cap on Solana, **DEX 24h volume** |
| **Upcoming upgrades** | Alpenglow, SIMD fee-market proposals, Firedancer client diversity |
| **Anomaly detection** | flags TPS spikes/drops, validator delinquency surges, large SOL/TVL moves vs the previous snapshot |

## Data sources — all free, no API keys

- **Solana JSON-RPC** (`api.mainnet-beta.solana.com`): `getEpochInfo`, `getRecentPerformanceSamples`, `getVoteAccounts`, `getSupply`, `getHealth`
- **DeFiLlama**: chain TVL, per-protocol TVL, DEX volume, stablecoin circulating supply
- **CoinGecko**: SOL price, market cap, volume, ATH

## Run locally

```bash
python collect.py          # writes data/latest.json + appends data/history.jsonl
python -m http.server 8000 # then open http://localhost:8000
```

No dependencies beyond the Python standard library.

## Automation (zero-maintenance)

`.github/workflows/update.yml` runs `collect.py` **every 30 minutes**, commits the refreshed
`data/`, and GitHub Pages serves the updated dashboard. The dashboard fetches `data/latest.json`
client-side, so a page refresh always shows the latest committed snapshot. Anomaly detection runs
on every refresh by diffing against the prior snapshot.

## Deploy (GitHub Pages)

1. Push this folder to a public repo.
2. Settings → Pages → deploy from `main` / root.
3. Enable the Action (it self-commits `data/`; `permissions: contents: write` is set).

## Design notes

- **Interactive & self-contained**: one `index.html`, no build step, no CDN — inline SVG
  sparklines from `history.jsonl`, theme-aware (light/dark), responsive.
- **Resilient**: each data source is fetched defensively; one dead source degrades a single
  field rather than sinking the whole snapshot.
- **Decentralization-first**: surfaces the Nakamoto coefficient and stake concentration, not just
  price — the metrics that actually describe network health.
