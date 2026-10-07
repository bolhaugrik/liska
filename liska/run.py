"""Parancssori futtató.

  python -m liska.run --seed 1 --regime liska --log jatek.jsonl
  python -m liska.run --check                                   # egy próbahívás a Gemini felé
  python -m liska.run --seed 1 --llm 0,1 --llm-mode weekly      # két LLM-játékos, heti stratégia
  python -m liska.run --seed 1 --llm all --llm-mode daily --rpm 10 --llm-log llm.log
"""
from __future__ import annotations

import argparse
import json

from .agents import HeuristicPolicy, LLMPolicy, RandomPolicy
from .engine import Engine
from .llm import LLMError, LLMGateway
from .llm.gateway import DEFAULT_MODEL
from .metrics import summarize
from .params import Params, REGIMES
from .runner import dump_log, run_game

MIX = ["ovatos", "terjeszkedo", "potyautas"]


def make_policy_for(kind: str, llm_ids=None, gateway=None, llm_mode: str = "daily"):
    cache = {}

    def policy_for(pid: int):
        if pid not in cache:
            if gateway is not None and (llm_ids == "all" or pid in (llm_ids or ())):
                cache[pid] = LLMPolicy(gateway, llm_mode, fallback=MIX[pid % 3])
            elif kind == "random":
                cache[pid] = RandomPolicy()
            else:
                cache[pid] = HeuristicPolicy(MIX[pid % 3])
        return cache[pid]

    policy_for.cache = cache
    return policy_for


def main() -> None:
    ap = argparse.ArgumentParser(description="Liska-játék v2 futtató")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--regime", choices=REGIMES, default="liska")
    ap.add_argument("--agents", choices=["heuristic", "random"], default="heuristic",
                    help="a nem LLM-játékosok típusa")
    ap.add_argument("--log", help="eseménynapló kimenete (JSONL)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--llm", help='LLM-játékosok: azonosítók vesszővel (pl. "0,1") vagy "all"')
    ap.add_argument("--llm-mode", choices=["daily", "weekly"], default="daily")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--rpm", type=int, default=10, help="kérés percenként (a fiókod kerete szerint)")
    ap.add_argument("--tpm", type=int, default=250_000, help="bemeneti token percenként")
    ap.add_argument("--llm-log", help="az LLM-hívások számozott naplója fájlba")
    ap.add_argument("--check", action="store_true", help="egy próbahívás, játék nélkül")
    args = ap.parse_args()

    gateway = None
    if args.llm or args.check:
        gateway = LLMGateway(model=args.model, rpm=args.rpm, tpm=args.tpm, log_path=args.llm_log)
    if args.check:
        try:
            ans = gateway.ask("Kizárólag JSON objektummal válaszolj.",
                              'Add vissza pontosan ezt: {"ok": true, "modell": "<a neved>"}', "check")
            print("Válasz:", ans["text"].strip())
            print("Értelmezve:", ans["json"])
        except LLMError as exc:
            print("A próbahívás nem sikerült:", exc)
        print(gateway.summary())
        return

    llm_ids = None
    if args.llm:
        llm_ids = "all" if args.llm.strip() == "all" else {int(x) for x in args.llm.split(",")}
    engine = Engine(Params(regime=args.regime), seed=args.seed, strict=True)
    policy_for = make_policy_for(args.agents, llm_ids, gateway, args.llm_mode)

    def on_day(e, rec):
        if args.quiet:
            return
        m = rec["metrics"]
        print(f"{rec['day']:3d}. nap  hazard {m['hazard']:.3f}  alap {m['fund']:8.0f}  "
              f"d {m['d']:.2f}  állapot {m['mean_q']:.2f}  átlagegyenleg {m['mean_balance']:8.0f}  "
              f"gazdacsere {m['takeovers']}  megtartás {m['retained']}  csőd {m['bankruptcies']}")

    result = run_game(engine, policy_for, on_day)
    print(json.dumps({"eredmény": result, "összegzés": summarize(engine)},
                     ensure_ascii=False, indent=2))
    if gateway is not None:
        print(gateway.summary())
        for pid, pol in sorted(policy_for.cache.items()):
            if isinstance(pol, LLMPolicy):
                print(f"  {pid}. játékos: {pol.stats['llm']} LLM-döntés, "
                      f"{pol.stats['fallback']} tartalék (heurisztika)")
    if args.log:
        dump_log(engine, args.log)


if __name__ == "__main__":
    main()
