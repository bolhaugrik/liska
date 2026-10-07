"""A játékmenet mozgalmasságának mérése:  python scripts/izgalom.py [seedek száma]

Heurisztikus ügynökökkel, Liska-rendben megmutatja, mit ad a játékhoz a növekedési fék
és a technológiaváltás, és mit adna a kikapcsolt piaci fordulat.
"""
import sys
from collections import Counter
from statistics import mean

sys.path.insert(0, ".")
from liska import Engine, Params, run_game  # noqa: E402
from liska.agents import HeuristicPolicy  # noqa: E402
from liska.metrics import gini  # noqa: E402

MIX = ["ovatos", "terjeszkedo", "potyautas"]
NEWS = ("shock", "boom", "accident", "shift_announced", "reskill_announced", "reskill")


def measure(seeds, **kw):
    rows, winners = [], Counter()
    for seed in range(seeds):
        e = Engine(Params(**kw), seed=seed, strict=True)
        bal, skill = [], []

        def on_day(e, rec):
            bal.append({p: pl.balance for p, pl in e.players.items()})
            hs = [e.players[m.holder].skill.get(m.type, 1.0)
                  for m in e.modules.values() if m.holder is not None]
            skill.append(mean(hs) if hs else 1.0)

        run_game(e, lambda pid: HeuristicPolicy(MIX[pid % 3]), on_day)
        n = len(bal)
        lead = [max(b, key=lambda p: (b[p], -p)) for b in bal]
        tk = [r["counts"]["takeovers"] for r in e.history]
        shifts = [d for d, r in enumerate(e.history)
                  if any(ev["type"] in NEWS for ev in r["events"])]
        quiet = sum(1 for r in e.history[1:]
                    if r["counts"]["takeovers"] == 0 and r["counts"]["failures"] == 0
                    and not any(ev["type"] in NEWS for ev in r["events"]))
        final = sorted(bal[-1].values(), reverse=True)
        held = Counter(m.holder for m in e.modules.values() if m.holder is not None)
        rows.append({
            "gazdacsere": sum(tk),
            "20. nap után": sum(tk[21:]),
            "csendes nap %": 100 * quiet / (n - 1),
            "élváltás 30. nap után": sum(1 for i in range(31, n) if lead[i] != lead[i - 1]),
            "1–2. hely különbség %": 100 * (final[0] - final[1]) / max(1.0, final[0]),
            "legnagyobb birtok": max(held.values()) if held else 0,
            "birtokos játékos": len(held),
            "szakértelem a végén": skill[-1],
            "átlagegyenleg": mean(bal[-1].values()),
            "Gini": gini(v + e.p.heritage for v in bal[-1].values()),
            "összeomlás %": 100 * e.station.collapsed,
            "csőd": sum(r["counts"]["bankruptcies"] for r in e.history),
        })
        winners[MIX[lead[-1] % 3]] += 1
    return rows, winners


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    configs = [
        ("végleges szabályok", {}),
        ("fék nélkül", {"span_penalty": 0.0}),
        ("techváltás nélkül", {"p_reskill": 0.0}),
        ("egyik nélkül sem", {"span_penalty": 0.0, "p_reskill": 0.0}),
        ("+ piaci fordulat", {"p_shift": 1 / 7}),
    ]
    results = [(name, *measure(seeds, **kw)) for name, kw in configs]
    keys = list(results[0][1][0])
    print(f"{seeds} seed, Liska-rend, heurisztikus ügynökök\n")
    print(f"{'':<24}" + "".join(f"{name:>22}" for name, _, _ in results))
    for k in keys:
        print(f"{k:<24}" + "".join(f"{mean(r[k] for r in rows):>22.2f}" for _, rows, _ in results))
    for prof in MIX:
        print(f"{'győztes: ' + prof:<24}" + "".join(f"{w[prof]:>22d}" for _, _, w in results))


if __name__ == "__main__":
    main()
