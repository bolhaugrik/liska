"""Paraméterezett szabályalapú ügynök három alapprofillal."""
from __future__ import annotations

import random

from ..actions import Bid, BidItem, Decision, Maintain, Pledge, SetValuation, Upgrade, Vote

# keep_f: a megtartási plafon a becsült érték hányadában, ha a járadék a plafon után jár (ekkor az ár
# a plafon p_ratio-szorosa, alapból maga a plafon, mert az alacsony ár semmit sem spórol);
# price_f és cap_f: ugyanez a régi szabály szerint, amikor a járadék az ár után járt;
# q_target: eddig tartja karban a modult; bid_f: ennyit ajánl mások moduljáért az érték arányában;
# reserve: az örökség ekkora részét nem köti le; vote: kedvelt osztalékarány;
# pledge_f: válságnál a rá eső rész hányszorosát ajánlja fel.
PROFILES = {
    "ovatos": dict(keep_f=1.2, price_f=0.75, cap_f=1.4, q_target=0.90, bid_f=0.55, max_bids=1,
                   reserve=0.5, vote=0.25, upgrade=False, max_modules=4, pledge_f=1.3, item_f=0.7),
    "terjeszkedo": dict(keep_f=1.05, price_f=0.60, cap_f=1.15, q_target=0.80, bid_f=0.95, max_bids=3,
                        reserve=0.15, vote=0.5, upgrade=True, max_modules=8, pledge_f=1.0, item_f=0.9),
    "potyautas": dict(keep_f=0.9, price_f=0.35, cap_f=1.0, q_target=0.45, bid_f=0.5, max_bids=1,
                      reserve=0.3, vote=1.0, upgrade=False, max_modules=4, pledge_f=0.0, item_f=0.4),
    # a vadász a saját értékelése fölé is licitál, hogy elvigye az alacsony plafonú modulokat
    "vadasz": dict(keep_f=1.0, price_f=0.6, cap_f=1.1, q_target=0.85, bid_f=1.03, max_bids=2,
                   reserve=0.15, vote=0.5, upgrade=True, max_modules=5, pledge_f=0.8, item_f=0.75),
}


class HeuristicPolicy:
    def __init__(self, profile: str = "ovatos", horizon: int = 60, **overrides):
        self.name = profile
        self.c = {**PROFILES[profile], **overrides}
        self.horizon = horizon

    @staticmethod
    def outlook(obs: dict, mtype: str, t_left: int) -> float:
        """A típus átlagos gazdasági szorzója a hátralévő időre, a bejelentett fordulatokból."""
        day, total = obs["day"], 0.0
        shifts = [s for s in obs["station"]["forecast"] if s["module_type"] == mtype]
        if not shifts:
            return 1.0
        for d in range(day, day + t_left):
            mult = 1.0
            for s in shifts:
                if s["start"] <= d < s["end"]:
                    mult *= s["multiplier"]
            total += mult
        return total / t_left

    @staticmethod
    def span(obs: dict, n: int) -> float:
        P = obs["params"]
        if P["span_penalty"] <= 0:
            return 1.0
        return max(P["span_floor"], 1.0 - P["span_penalty"] * max(0, n - P["span_free"]))

    def worth(self, obs: dict, m: dict) -> float:
        """Mennyit ér a modul nekem a hátralévő időre, a saját karbantartási szintemen."""
        P, c = obs["params"], self.c
        mt = P["types"][m["type"]]
        y, g = m["nominal"], obs["station"]["hazard"]
        t_left = max(8, self.horizon - obs["day"])
        skill = obs["me"]["skill"].get(m["type"], 1.0)
        for nxt in obs["me"]["skill_next"]:          # bejelentett technológiaváltás: időarányos átlag
            if nxt["module_type"] == m["type"]:
                before = min(t_left, max(0, nxt["start"] - obs["day"]))
                skill = (before * skill + (t_left - before) * nxt["value"]) / t_left
        income = (y * c["q_target"] * (1 - P["eta"] * g) * skill
                  * self.outlook(obs, m["type"], t_left))
        upkeep = mt["wear"] * P["mu"] * y
        repair = max(0.0, c["q_target"] - m["q"]) * P["mu"] * y
        if obs["regime"] == "liska":
            w = t_left * (income - upkeep) / (1 + P["r"] * t_left)
        elif obs["regime"] == "fixed":
            w = t_left * (income - upkeep - P["r"] * m["opening_price"])
        else:
            w = t_left * (income * (1 - P["tax"]) - upkeep)
        return max(0.0, w - repair)

    def decide(self, obs: dict) -> Decision:
        rng = random.Random(obs["rng_seed"])
        P, c, me = obs["params"], self.c, obs["me"]
        regime, day = obs["regime"], obs["day"]
        actions = []
        budget = max(0.0, me["cover"] - c["reserve"] * me["heritage"])
        public = {m["id"]: m for m in obs["modules"]}

        # a növekedési fék miatt egy modul értéke attól is függ, hány másik van mellette
        n_own = len(obs["my_modules"])
        own_w = {m["id"]: self.worth(obs, public[m["id"]]) for m in obs["my_modules"]}
        own_sum = sum(own_w.values())
        f_now, f_less, f_more = (self.span(obs, n_own), self.span(obs, n_own - 1),
                                 self.span(obs, n_own + 1))

        # 1. árazás
        for m in obs["my_modules"]:
            # megtartási érték: mennyivel érek többet vele, mint nélküle
            w = max(0.0, f_now * own_sum - f_less * (own_sum - own_w[m["id"]]))
            if regime == "liska":
                floor = max(P["p_min"], obs["options"][m["id"]]["min_price"])
                if P.get("rent_base", "price") == "cap":
                    keep = c["keep_f"] * w
                    price = max(floor, c.get("p_ratio", 1.0) * keep)
                    cap = max(price, keep)
                else:
                    price = max(floor, c["price_f"] * w)
                    cap = max(price, c["cap_f"] * w)
                if abs(price - m["price"]) > 0.01 * m["price"] or abs(cap - m["cap"]) > 0.01 * m["cap"]:
                    actions.append(SetValuation(m["id"], price, cap))
            elif regime == "private":
                ask = max(P["p_min"], 1.1 * c["cap_f"] * w)
                if abs(ask - m["cap"]) > 0.01 * m["cap"]:
                    actions.append(SetValuation(m["id"], ask, ask))

        # 2. karbantartás
        q_maint = c.get("q_maint", c["q_target"])   # külön is állítható: értékel, de nem tart karban
        for m in sorted(obs["my_modules"], key=lambda x: x["q"]):
            if m["q"] < q_maint - 0.02:
                amount = (q_maint - m["q"]) * P["mu"] * m["nominal"]
                if amount <= budget:
                    actions.append(Maintain(m["id"], amount))
                    budget -= amount

        # 2/b. válság: ma dől el, a rá eső rész arányában ajánl fel
        crisis = obs["station"].get("crisis")
        if crisis and obs["station"].get("pledge_open") and c.get("pledge_f", 0) > 0:
            short = max(0.0, crisis["cost"] - obs["station"]["fund"])
            amount = min(budget, c["pledge_f"] * short / max(1, len(obs["players"])))
            if amount >= 1:
                actions.append(Pledge(amount))
                budget -= amount

        # 2/c. kereskedőhajó: az érkezés napján licit az eszközökre, a várható haszon arányában
        ship = obs["station"].get("ship")
        if ship and ship.get("auction_open") and obs["my_modules"]:
            t_left = max(4, self.horizon - day)
            daily = sum(m["nominal"] * c["q_target"] * me["skill"].get(m["type"], 1.0) for m in obs["my_modules"])
            worth = {"turbo": P.get("boost_step", 0.1) * daily * t_left * 0.6 if me.get("boost", 0) < 0.29 else 0,
                     "javito": 0.9 * sum(obs["options"][m["id"]]["full_maintenance"]
                                         + P.get("item_repair_rounds", 3) * P["types"][m["type"]]["wear"] * P["mu"] * m["nominal"]
                                         for m in obs["my_modules"]),
                     "pajzs": 0.5 * P.get("crisis_damage", 0.4) * P["mu"] * max(m["nominal"] for m in obs["my_modules"])}
            for item in ship["items"]:
                offer = c.get("item_f", 0.8) * worth.get(item["kind"], 0) * rng.uniform(0.85, 1.1)
                if item["sold_to"] is None and item["reserve"] <= offer <= budget:
                    actions.append(BidItem(item["id"], offer)); budget -= offer

        # 3. fejlesztés: csak a játék elején, adósság nélkül
        if c["upgrade"] and day <= self.horizon // 2 and me["balance"] > 0:
            for m in sorted(obs["my_modules"], key=lambda x: -x["nominal"]):
                cost = obs["options"][m["id"]]["upgrade_cost"]
                if cost is not None and cost <= 0.5 * budget:
                    actions.append(Upgrade(m["id"]))
                    budget -= cost
                    break

        # 4. licit
        if len(obs["my_modules"]) < c["max_modules"]:
            offers = []
            for m in obs["modules"]:
                if m["holder"] == me["id"] or m["min_bid"] is None:
                    continue
                # szerzési érték: az új modul hozama mínusz a meglévők romlása
                w = max(0.0, f_more * (own_sum + self.worth(obs, m)) - f_now * own_sum)
                if m["holder"] is None:
                    ref = m["opening_price"]
                    offer = ref * (1 + rng.uniform(0, 0.2))
                    if offer > w:
                        continue
                else:
                    # az alulárazott modul a jó célpont: az értéket a nyilvános árhoz méri
                    ref = max(m["min_bid"], m["price"])
                    offer = c["bid_f"] * w
                    if offer < ref:
                        continue
                # a szórás miatt nem ugyanarra a modulra licitál mindenki
                offers.append((w / ref * rng.uniform(0.8, 1.25), m["id"], offer))
            offers.sort(key=lambda o: (-o[0], o[1]))
            room = c["max_modules"] - len(obs["my_modules"])
            limit = min(room, 4 if day == 0 else c["max_bids"])
            for _, mid, offer in offers:
                if limit <= 0:
                    break
                if offer <= budget:
                    actions.append(Bid(mid, offer))
                    budget -= offer
                    limit -= 1

        # 5. szavazás
        if obs["station"]["vote_open"]:
            g = obs["station"]["hazard"]
            choice = c["vote"]
            if c.get("vote_adaptive", True):     # az LLM szavazatát nem írja felül
                if g > 0.75:
                    choice = 0.0
                elif g > 0.5:
                    choice = min(choice, 0.25)
            actions.append(Vote(choice))
        return Decision(actions, self.name)
