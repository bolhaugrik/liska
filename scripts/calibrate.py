"""Kalibráló futás:  python scripts/calibrate.py [seedek száma]

Heurisztikus ügynökökkel (3 óvatos, 3 terjeszkedő, 2 potyautas) lefuttatja a három
birtoklási rendet ugyanazokon a seedeken, és kiírja a mutatók átlagát.
"""
import sys
from collections import Counter
from statistics import mean

sys.path.insert(0, ".")
from liska import Engine, Params, run_game  # noqa: E402
from liska.agents import HeuristicPolicy, RandomPolicy  # noqa: E402
from liska.metrics import summarize  # noqa: E402
from liska.params import REGIMES  # noqa: E402

MIX = ["ovatos", "terjeszkedo", "potyautas"]
COLS = [("collapsed", "összeoml."), ("bankruptcies", "csőd"), ("mean_balance", "átl.egyenl."),
        ("gini", "Gini"), ("mean_q", "állapot"), ("investment_rate", "beruh.ráta"),
        ("turnover", "gazdacsere"), ("retention_ratio", "megtart.ar."),
        ("price_yield", "ár/hozam"), ("hazard_final", "záró hazard"), ("income", "össztermelés")]


def run(regime, seeds, policy_for, **kw):
    out = []
    for seed in range(seeds):
        e = Engine(Params(regime=regime, **kw), seed=seed, strict=True)
        run_game(e, policy_for)
        out.append(summarize(e))
    return out


def row(label, sums):
    cells = []
    for key, _ in COLS:
        vals = [s[key] for s in sums if s[key] is not None]
        cells.append(f"{mean(float(v) for v in vals):11.3f}" if vals else f"{'–':>11}")
    print(f"{label:<22}" + " ".join(cells))


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    heur = lambda pid: HeuristicPolicy(MIX[pid % 3])  # noqa: E731
    print(f"{seeds} seed, rendenként\n")
    print(f"{'':<22}" + " ".join(f"{name:>11}" for _, name in COLS))
    for spread in (0.2, 0.0):   # 0.2 az alapérték; 0.0 az összevetéshez (egyforma termelékenység)
        for regime in REGIMES:
            row(f"{regime} (szakért. {spread})", run(regime, seeds, heur, skill_spread=spread))
        print()
    print("Véletlen ügynökök (a motor terheléses próbája), liska rend:")
    codes, counts = Counter(), Counter()
    for seed in range(seeds):
        e = Engine(Params(), seed=seed, strict=True)
        run_game(e, lambda pid: RandomPolicy())
        counts["összeomlás"] += e.station.collapsed
        for rec in e.history:
            for k in ("takeovers", "retained", "bankruptcies", "failures", "acquired"):
                counts[k] += rec["counts"][k]
            for res in rec["results"].values():
                codes.update(r["code"] for r in res)
        counts["ügynökhiba"] += len(e.agent_errors)
    print("  események:", dict(counts))
    print("  okkódok:  ", dict(codes.most_common()))


if __name__ == "__main__":
    main()
