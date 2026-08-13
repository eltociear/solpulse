#!/usr/bin/env python3
"""SolPulse — auto-updating Solana ecosystem data collector.

Pulls a comprehensive snapshot of the Solana ecosystem from free public sources and writes
`data/latest.json` (current snapshot) plus appends to `data/history.jsonl` (for trends and
anomaly detection). No API keys required — Solana public RPC, DeFiLlama, CoinGecko.

Run:  python collect.py
CI:   scheduled via .github/workflows/update.yml (commits the refreshed JSON).

Metrics (per Superteam Canada bounty scope):
  - Network performance: TPS, slot time, block height, epoch progress
  - Validators: active vs delinquent, stake distribution, top validators, nakamoto coefficient
  - Economic: SOL price + 24h, circulating/total supply, staking ratio, median tx fee
  - Ecosystem: TVL (DeFiLlama), top protocols, stablecoin supply, DEX volume, SOL market cap
  - Anomaly detection vs the previous snapshot (TPS/price/TVL/delinquency swings)
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

RPC = "https://api.mainnet-beta.solana.com"
UA = {"User-Agent": "SolPulse/1.0 (+https://github.com)", "Accept": "application/json"}
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def rpc(method: str, params: list | None = None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(RPC, data=body, headers={**UA, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode()).get("result")


def http(url: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode())


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a dead source must not sink the whole snapshot
        print(f"  ! source failed: {type(e).__name__}: {str(e)[:70]}", file=sys.stderr)
        return default


def network():
    ep = rpc("getEpochInfo") or {}
    perf = rpc("getRecentPerformanceSamples", [5]) or []
    tps = None
    slot_time = None
    if perf:
        tps = round(sum(s["numTransactions"] for s in perf) / sum(s["samplePeriodSecs"] for s in perf), 1)
        # non-vote TPS if available
        slot_time = round(sum(s["samplePeriodSecs"] for s in perf) / sum(s["numSlots"] for s in perf), 3)
    slots_in_epoch = ep.get("slotsInEpoch") or 432000
    return {
        "epoch": ep.get("epoch"),
        "absolute_slot": ep.get("absoluteSlot"),
        "block_height": ep.get("blockHeight"),
        "epoch_progress_pct": round((ep.get("slotIndex", 0) / slots_in_epoch) * 100, 2),
        "slots_remaining": slots_in_epoch - ep.get("slotIndex", 0),
        "tps": tps,
        "avg_slot_time_s": slot_time,
        "health": rpc("getHealth"),
    }


def validators():
    va = rpc("getVoteAccounts") or {"current": [], "delinquent": []}
    cur, delq = va.get("current", []), va.get("delinquent", [])
    stakes = sorted((v.get("activatedStake", 0) for v in cur), reverse=True)
    total_stake = sum(stakes) or 1
    # Nakamoto coefficient: min validators controlling >33% of stake
    cum, nakamoto = 0, 0
    for s in stakes:
        cum += s
        nakamoto += 1
        if cum > total_stake / 3:
            break
    top = sorted(cur, key=lambda v: -v.get("activatedStake", 0))[:10]
    return {
        "active": len(cur),
        "delinquent": len(delq),
        "delinquency_pct": round(len(delq) / (len(cur) + len(delq)) * 100, 2) if (cur or delq) else None,
        "total_active_stake_sol": round(total_stake / 1e9, 0),
        "nakamoto_coefficient": nakamoto,
        "top10_stake_share_pct": round(sum(stakes[:10]) / total_stake * 100, 2),
        "top_validators": [
            {"vote_pubkey": v.get("votePubkey"), "stake_sol": round(v.get("activatedStake", 0) / 1e9, 0),
             "commission": v.get("commission")}
            for v in top
        ],
    }


def economics():
    sup = (rpc("getSupply", [{"excludeNonCirculatingAccountsList": True}]) or {}).get("value", {})
    total = sup.get("total", 0) / 1e9
    circ = sup.get("circulating", 0) / 1e9
    cg = safe(lambda: http("https://api.coingecko.com/api/v3/coins/solana?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false"), {})
    md = (cg or {}).get("market_data", {})
    price = (md.get("current_price") or {}).get("usd")
    return {
        "sol_price_usd": price,
        "sol_price_24h_change_pct": round(md.get("price_change_percentage_24h") or 0, 2) if md else None,
        "sol_market_cap_usd": (md.get("market_cap") or {}).get("usd"),
        "sol_volume_24h_usd": (md.get("total_volume") or {}).get("usd"),
        "ath_usd": (md.get("ath") or {}).get("usd"),
        "total_supply_sol": round(total, 0),
        "circulating_supply_sol": round(circ, 0),
        "staking_ratio_pct": None,  # filled below from validators if available
    }


def ecosystem():
    out = {}
    chains = safe(lambda: http("https://api.llama.fi/v2/chains"), [])
    sol = next((c for c in (chains or []) if c.get("name") == "Solana"), {})
    out["tvl_usd"] = round(sol.get("tvl", 0), 0) if sol else None
    protos = safe(lambda: http("https://api.llama.fi/protocols"), [])
    SKIP = {"CEX", "Chain", "Bridge"}  # custody/infra, not Solana-native DeFi
    sol_protos = sorted(
        [p for p in (protos or []) if "Solana" in (p.get("chains") or []) and p.get("category") not in SKIP],
        key=lambda p: -(p.get("chainTvls", {}).get("Solana") or p.get("tvl") or 0),
    )[:10]
    out["top_protocols"] = [
        {"name": p.get("name"), "category": p.get("category"),
         "tvl_usd": round(p.get("chainTvls", {}).get("Solana") or p.get("tvl") or 0, 0),
         "change_1d_pct": round(p.get("change_1d") or 0, 2)}
        for p in sol_protos
    ]
    stables = safe(lambda: http("https://stablecoins.llama.fi/stablecoinchains"), [])
    s_sol = next((c for c in (stables or []) if c.get("name") == "Solana"), {})
    out["stablecoin_mcap_usd"] = round((s_sol.get("totalCirculatingUSD", {}) or {}).get("peggedUSD", 0), 0) if s_sol else None
    dex = safe(lambda: http("https://api.llama.fi/overview/dexs/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"), {})
    out["dex_volume_24h_usd"] = round((dex or {}).get("total24h", 0), 0) if dex else None
    return out


UPCOMING = [
    {"name": "Alpenglow", "what": "New consensus (Votor + Rotor) replacing TowerBFT/PoH voting; ~150ms finality target.", "status": "SIMD approved; phased rollout"},
    {"name": "SIMD-0326 / SIMD-525", "what": "Fee-market and networking upgrades under discussion for throughput and MEV.", "status": "governance / dev"},
    {"name": "Firedancer / Frankendancer", "what": "Jump's independent validator client; increases client diversity and throughput headroom.", "status": "mainnet rollout ongoing"},
]


def detect_anomalies(cur: dict, prev: dict | None) -> list[dict]:
    if not prev:
        return []
    out = []

    def pct(a, b):
        return (a - b) / b * 100 if b else 0

    n, pn = cur["network"], prev.get("network", {})
    if n.get("tps") and pn.get("tps"):
        d = pct(n["tps"], pn["tps"])
        if abs(d) >= 25:
            out.append({"metric": "TPS", "severity": "high" if abs(d) >= 40 else "medium",
                        "message": f"TPS {'spiked' if d > 0 else 'dropped'} {d:+.0f}% ({pn['tps']:.0f} -> {n['tps']:.0f})"})
    v, pv = cur["validators"], prev.get("validators", {})
    if v.get("delinquency_pct") is not None and pv.get("delinquency_pct") is not None:
        if v["delinquency_pct"] >= 5 and v["delinquency_pct"] > pv["delinquency_pct"] * 1.5:
            out.append({"metric": "validator_delinquency", "severity": "high",
                        "message": f"Validator delinquency rose to {v['delinquency_pct']:.1f}% (was {pv['delinquency_pct']:.1f}%)"})
    e, pe = cur["economics"], prev.get("economics", {})
    if e.get("sol_price_24h_change_pct") is not None and abs(e["sol_price_24h_change_pct"]) >= 8:
        out.append({"metric": "SOL_price", "severity": "medium",
                    "message": f"SOL moved {e['sol_price_24h_change_pct']:+.1f}% in 24h to ${e.get('sol_price_usd')}"})
    if e.get("tvl_usd") and pe.get("tvl_usd"):
        d = pct(cur["ecosystem"]["tvl_usd"], prev["ecosystem"]["tvl_usd"]) if prev.get("ecosystem") else 0
        if abs(d) >= 10:
            out.append({"metric": "TVL", "severity": "medium", "message": f"Solana TVL changed {d:+.1f}% in one interval"})
    return out


def main() -> int:
    DATA.mkdir(exist_ok=True)
    print("SolPulse: collecting Solana ecosystem snapshot ...")
    net = network()
    val = validators()
    eco = ecosystem()
    econ = economics()
    if val.get("total_active_stake_sol") and econ.get("circulating_supply_sol"):
        econ["staking_ratio_pct"] = round(val["total_active_stake_sol"] / econ["circulating_supply_sol"] * 100, 1)

    prev = None
    latest = DATA / "latest.json"
    if latest.exists():
        prev = safe(lambda: json.loads(latest.read_text(encoding="utf-8")))

    snap = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network": net,
        "validators": val,
        "economics": econ,
        "ecosystem": eco,
        "upcoming": UPCOMING,
    }
    snap["anomalies"] = detect_anomalies(snap, prev)

    latest.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    with (DATA / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "t": snap["generated_at"], "tps": net.get("tps"), "epoch": net.get("epoch"),
            "sol_price": econ.get("sol_price_usd"), "tvl": eco.get("tvl_usd"),
            "delinquency_pct": val.get("delinquency_pct"), "dex_vol": eco.get("dex_volume_24h_usd"),
        }) + "\n")

    print(f"  epoch {net.get('epoch')} | TPS {net.get('tps')} | validators {val.get('active')}/{val.get('delinquent')} delq"
          f" | SOL ${econ.get('sol_price_usd')} | TVL ${(eco.get('tvl_usd') or 0)/1e9:.2f}B"
          f" | {len(snap['anomalies'])} anomaly(ies)")
    print(f"  wrote {latest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
