"""Véletlenszerűen lépő ügynök a teszteléshez: szándékosan szabálytalan lépéseket is ad."""
from __future__ import annotations

import random

from ..actions import Bid, BidItem, Decision, Maintain, Pledge, SetValuation, Upgrade, Vote


class RandomPolicy:
    def __init__(self, chaos: float = 0.25):
        self.chaos = chaos  # a szabálytalan vagy hibás lépések aránya

    def decide(self, obs: dict):
        rng = random.Random(obs["rng_seed"])
        roll = rng.random()
        if roll < self.chaos * 0.1:
            raise RuntimeError("szándékos ügynökhiba")
        if roll < self.chaos * 0.2:
            return rng.choice([None, "szemét", 42, {"actions": "nem lista"}])
        ids = [m["id"] for m in obs["modules"]]
        mine = {m["id"]: m for m in obs["my_modules"]}
        cover = obs["me"]["cover"]
        actions = []
        for _ in range(rng.randint(0, 8)):
            bad = rng.random() < self.chaos
            mid = rng.choice(ids + ([999, -1, "x", None, 2.5] if bad else []))
            kind = rng.choice(["val", "bid", "maint", "upg", "vote"])
            if kind == "val":
                if not bad and mine:
                    m = mine[rng.choice(sorted(mine))]
                    price = m["price"] * rng.uniform(0.96, 1.3)
                    actions.append(SetValuation(m["id"], price, price * rng.uniform(1.0, 2.0)))
                else:
                    actions.append(SetValuation(mid, rng.choice([-5, 0, 10, float("nan"), 1e12]),
                                                rng.choice([-1, 5, float("inf"), 50])))
            elif kind == "bid":
                if bad:
                    amt = rng.choice([-10, 0, 1e15, float("nan"), "sok", cover * 3 + 1])
                else:
                    m = obs["modules"][rng.randrange(len(ids))]
                    mid = m["id"]
                    lo = max(m["min_bid"] or 0.0, m["price"])
                    amt = lo * rng.uniform(0.9, 1.6)
                actions.append(Bid(mid, amt))
            elif kind == "maint":
                tgt = rng.choice(sorted(mine)) if mine and not bad else mid
                actions.append(Maintain(tgt, rng.choice([-1, 5, 50, 500, 1e9])))
            elif kind == "upg":
                tgt = rng.choice(sorted(mine)) if mine and not bad else mid
                actions.append(Upgrade(tgt))
            else:
                actions.append(Vote(rng.choice([0.0, 0.25, 0.5, 0.75, 1.0, 0.3, "igen"])))
        if rng.random() < 0.4:
            actions.append(BidItem(rng.choice([0, 1, 5, -1, "x"]), rng.choice([100, 200, 400, -3, float("nan")])))
        if rng.random() < 0.4:
            actions.append(Pledge(rng.choice([20, 150, 600, -5, float("nan"), cover * 2 + 1])))
        if rng.random() < self.chaos * 0.3:
            actions.append({"type": "Bid", "module": ids[0]})          # hiányos dict
            actions.append({"type": "Nincs ilyen"})
            actions.append(object())
        if rng.random() < 0.3:  # dict alakban is jöhet döntés
            return {"actions": actions, "rationale": "véletlen"}
        return Decision(actions, "véletlen")
