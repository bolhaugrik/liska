"""Napi mutatók és futásösszegzés."""
from __future__ import annotations


def gini(values) -> float:
    xs = sorted(max(0.0, v) for v in values)
    n, total = len(xs), sum(xs)
    if n == 0 or total <= 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * total) - (n + 1) / n


def day_metrics(engine, ledger, flows, counts) -> dict:
    p = engine.p
    players = list(engine.players.values())
    mods = list(engine.modules.values())
    held = [m for m in mods if m.holder is not None]
    price_yield = {}
    for t in sorted(p.types):
        hs = [m.price / engine.nominal(m) for m in held if m.type == t]
        price_yield[t] = sum(hs) / len(hs) if hs else None
    return {
        # az egyenleg negatív is lehet, ezért a Gini az egyenleg + induló örökség értékén számol
        "gini": gini(pl.balance + p.heritage for pl in players),
        "mean_balance": sum(pl.balance for pl in players) / len(players),
        "mean_q": sum(m.q for m in mods) / len(mods),
        "held": len(held),
        "price_yield": price_yield,
        "hazard": engine.station.hazard,
        "fund": engine.station.fund,
        "d": engine.station.d,
        "income": ledger["production"],
        "investment": ledger["maintenance"] + ledger["upgrade"],
        "takeovers": counts["takeovers"],
        "retained": counts["retained"],
        "failures": counts["failures"],
        "bankruptcies": counts["bankruptcies"],
        "writeoff": ledger["writeoff"],
        "crises_averted": counts["crises_averted"],
        "crises_hit": counts["crises_hit"],
        "flows": dict(flows),
    }


def summarize(engine) -> dict:
    """Egy teljes futás összegzése a kilenc mutatóra."""
    hist = [r["metrics"] for r in engine.history]
    days = max(1, len(hist) - 1)  # a 0. nap csak nyitólicit
    n_mod = len(engine.modules)
    income = sum(h["income"] for h in hist)
    invest = sum(h["investment"] for h in hist)
    tk = sum(h["takeovers"] for h in hist)
    rt = sum(h["retained"] for h in hist)
    last = hist[-1]
    py = [v for h in hist[1:] for v in h["price_yield"].values() if v is not None]
    flows = {}
    for h in hist:
        for k, v in h["flows"].items():
            flows[k] = flows.get(k, 0.0) + v
    return {
        "days": len(hist),
        "collapsed": engine.station.collapsed,
        "gini": last["gini"],
        "mean_balance": last["mean_balance"],
        "turnover": tk / (n_mod * days),
        "retention_ratio": rt / (rt + tk) if rt + tk else None,
        "price_yield": sum(py) / len(py) if py else None,
        "mean_q": sum(h["mean_q"] for h in hist[1:]) / max(1, len(hist) - 1),
        "investment_rate": invest / income if income else 0.0,
        "hazard_final": last["hazard"],
        "hazard_max": max(h["hazard"] for h in hist),
        "bankruptcies": sum(h["bankruptcies"] for h in hist),
        "writeoff": sum(h["writeoff"] for h in hist),
        "crises_averted": sum(h["crises_averted"] for h in hist),
        "crises_hit": sum(h["crises_hit"] for h in hist),
        "income": income,
        "flows": flows,
        "held_final": last["held"],
    }
