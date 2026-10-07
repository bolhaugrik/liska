"""A determinisztikus motor: egy kör (nap) 11 fázisa a szabályspecifikáció szerint."""
from __future__ import annotations

import math
import random
from dataclasses import asdict
from typing import Any, Optional

from .actions import (
    OK, NOT_HOLDER, OWN_MODULE, PRICE_DROP_LIMIT, CAP_BELOW_PRICE, BID_BELOW_MIN,
    INSUFFICIENT_COVER, MAX_LEVEL, NO_OPEN_VOTE, DUPLICATE, INVALID_TARGET, NOT_IN_REGIME, NO_CRISIS, NO_SHIP,
    SetValuation, Bid, Maintain, Upgrade, Vote, Pledge, BidItem, Decision, action_to_dict, parse_action,
)
from .metrics import day_metrics
from .params import Params, VOTE_OPTIONS
from .state import Module, Player, Station

EPS = 1e-9
OBS_VERSION = 1
MAX_ACTIONS = 200


class InvariantError(AssertionError):
    pass


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class Engine:
    def __init__(self, params: Optional[Params] = None, seed: int = 0, strict: bool = False,
                 _restore: bool = False):
        self.p = params or Params()
        self.seed = seed
        self.strict = strict
        self.players: dict[int, Player] = {}
        self.modules: dict[int, Module] = {}
        self.last_events: list[dict] = []
        self.last_results: dict[int, list] = {}
        self.history: list[dict] = []
        self.agent_errors: list[dict] = []
        if _restore:
            return
        p = self.p
        self.station = Station(hazard=p.g0, d=p.d0,
                               econ={t: 1.0 for t in p.types},
                               econ_timer={t: 0 for t in p.types})
        self.end_day = self._rng("end").randint(p.end_day_min, p.end_day_max)
        for pid in range(p.n_players):
            self.players[pid] = Player(id=pid, heritage=p.heritage, skill=self._skill(pid))
        mid = 0
        for tname in sorted(p.types):
            for _ in range(p.types[tname].count):
                m = Module(id=mid, type=tname, q=p.q0)
                m.price = m.cap = self.opening_price(m)
                self.modules[mid] = m
                mid += 1

    # ---------- segédfüggvények ----------

    def _rng(self, *key) -> random.Random:
        """Célonként és naponként külön, a seedből levezetett véletlenforrás."""
        return random.Random(":".join(str(k) for k in (self.seed,) + key))

    def _skill(self, pid: int) -> dict:
        sp = self.p.skill_spread
        if sp <= 0:
            return {}
        rng = self._rng("skill", pid)
        return {t: rng.uniform(1 - sp, 1 + sp) for t in sorted(self.p.types)}

    def nominal(self, m: Module) -> float:
        return self.p.types[m.type].base_yield * self.p.lam ** (m.level - 1)

    def opening_price(self, m: Module) -> float:
        return self.p.omega * self.nominal(m)

    def min_bid(self, m: Module) -> Optional[float]:
        if m.holder is None:
            return self.opening_price(m)
        if self.p.regime == "liska":
            return m.price * (1 + self.p.beta)
        if self.p.regime == "private":
            return self.p.p_min
        return None  # fixed: birtokolt modulra nem lehet licitálni

    def maintenance_needed(self, m: Module) -> float:
        return max(0.0, (1.0 - m.q) * self.p.mu * self.nominal(m))

    def upgrade_cost(self, m: Module) -> Optional[float]:
        if m.level >= self.p.max_level:
            return None
        return self.p.u * self.nominal(m) * self.p.lam

    def econ_multiplier(self, mtype: str, day: Optional[int] = None) -> float:
        """A típus aznapi gazdasági szorzója: rövid sokk és a futó piaci fordulatok szorzata."""
        day = self.station.day if day is None else day
        mult = self.station.econ[mtype]
        for s in self.station.shifts:
            if s["module_type"] == mtype and s["start"] <= day < s["end"]:
                mult *= s["multiplier"]
        return mult

    def span_factor(self, n_modules: int) -> float:
        """Növekedési fék: a szabad darabszám fölött minden modul rontja a birtokos összes hozamát."""
        p = self.p
        if p.span_penalty <= 0:
            return 1.0
        return max(p.span_floor, 1.0 - p.span_penalty * max(0, n_modules - p.span_free))

    def vote_open(self, day: Optional[int] = None) -> bool:
        day = self.station.day if day is None else day
        return day > 0 and day % self.p.vote_interval == 0

    def money(self) -> float:
        return sum(pl.balance for pl in self.players.values()) + self.station.fund

    # ---------- megfigyelés ----------

    def observe(self, pid: int) -> dict:
        """A játékos megfigyelése. Nem tartalmazza mások plafonját, egyenlegét és a záró napot."""
        st, me = self.station, self.players[pid]
        mods, mine, options = [], [], {}
        for mid in sorted(self.modules):
            m = self.modules[mid]
            mods.append({
                "id": m.id, "type": m.type, "holder": m.holder, "price": m.price,
                "q": m.q, "level": m.level, "nominal": self.nominal(m),
                "opening_price": self.opening_price(m),
                "min_bid": None if m.holder == pid else self.min_bid(m),
            })
            if m.holder == pid:
                mine.append({
                    "id": m.id, "type": m.type, "price": m.price, "cap": m.cap, "q": m.q,
                    "level": m.level, "nominal": self.nominal(m),
                    "last_income": m.last_income, "last_rent": m.last_rent,
                })
                options[m.id] = {
                    "min_price": max(self.p.p_min, m.price * (1 - self.p.delta)),
                    "full_maintenance": self.maintenance_needed(m),
                    "upgrade_cost": self.upgrade_cost(m),
                }
        events = [e for e in self.last_events
                  if e["visible"] == "all" or pid in e["visible"]]
        return {
            "version": OBS_VERSION,
            "day": st.day,
            "regime": self.p.regime,
            "me": {"id": pid, "balance": me.balance, "heritage": me.heritage,
                   "cover": me.balance + me.heritage, "status": me.status,
                   "skill": dict(me.skill), "span_factor": self.span_factor(len(mine)),
                   "items": dict(me.items), "boost": me.boost,
                   "ship_earned": st.ship["earned"].get(str(pid), 0.0) if st.ship else 0.0,
                   # a bejelentett technológiaváltásból mindenki csak a saját új értékét látja
                   "skill_next": [{"module_type": x["module_type"], "start": x["start"],
                                   "value": x["skills"][str(pid)]}
                                  for x in st.reskills if str(pid) in x["skills"]]},
            "my_modules": mine,
            "modules": mods,
            "players": [{"id": q.id, "status": q.status} for q in
                        (self.players[k] for k in sorted(self.players))],
            "station": {"fund": st.fund, "hazard": st.hazard, "d": st.d,
                        "econ": dict(st.econ), "forecast": [dict(x) for x in st.shifts],
                        "missions": [{**{k: v for k, v in ms.items() if k != "progress"},
                                      "my_progress": ms["progress"].get(str(pid), 0)} for ms in st.missions],
                        "crisis": dict(st.crisis) if st.crisis else None,
                        "ship": ({**{k: v for k, v in st.ship.items() if k != "earned"},
                                  "here": st.ship["arrives"] <= st.day < st.ship["leaves"],
                                  "auction_open": (st.ship["arrives"] <= st.day < st.ship["leaves"]
                                                   and any(i["sold_to"] is None for i in st.ship["items"]))}
                                 if st.ship else None),
                        "pledge_open": bool(st.crisis and st.crisis["due"] <= st.day),
                        "reskill_forecast": [{"module_type": x["module_type"], "start": x["start"]}
                                             for x in st.reskills],
                        "vote_open": self.vote_open(),
                        "vote_options": list(VOTE_OPTIONS)},
            "params": self.p.public(),
            "last_events": events,
            "my_results": self.last_results.get(pid, []),
            "options": options,
            "rng_seed": self._rng("agent", st.day, pid).getrandbits(64),
        }

    # ---------- egy kör ----------

    def _normalize(self, dec: Any) -> tuple[list, str]:
        """Bármilyen bemenetből (Decision, dict, None, szemét) akciólistát csinál."""
        if dec is None:
            return [], ""
        if isinstance(dec, Decision):
            raw, why = dec.actions, dec.rationale
        elif isinstance(dec, dict):
            raw, why = dec.get("actions", []), dec.get("rationale", "")
        else:
            return [], ""
        if not isinstance(raw, (list, tuple)):
            return [], ""
        why = why if isinstance(why, str) else ""
        return list(raw)[:MAX_ACTIONS], why[:2000]

    def step(self, decisions: dict) -> dict:
        if self.station.finished:
            raise RuntimeError("a játék véget ért")
        p, st = self.p, self.station
        day = st.day
        events: list[dict] = []
        ledger = {"production": 0.0, "maintenance": 0.0, "upgrade": 0.0,
                  "hazard_spend": 0.0, "crisis": 0.0, "items": 0.0, "prize": 0.0, "writeoff": 0.0}
        flows = {"rent": 0.0, "interest": 0.0, "surplus": 0.0, "sales": 0.0, "tax": 0.0,
                 "dividend": 0.0, "hazard_spend": 0.0, "crisis": 0.0, "items": 0.0, "prize": 0.0}
        counts = {"takeovers": 0, "retained": 0, "failures": 0, "bankruptcies": 0,
                  "acquired": 0, "crises_averted": 0, "crises_hit": 0}
        money_before = self.money()
        pids = sorted(self.players)

        # 2. döntések normalizálása
        acts: dict[int, list] = {}
        results: dict[int, list] = {}
        rationale: dict[int, str] = {}
        for pid in pids:
            raw, why = self._normalize(decisions.get(pid) if isinstance(decisions, dict) else None)
            parsed = []
            res = []
            for item in raw:
                a = parse_action(item)
                parsed.append(a)
                res.append({"action": action_to_dict(a if a is not None else item),
                            "code": None if a is not None else INVALID_TARGET})
            acts[pid], results[pid], rationale[pid] = parsed, res, why

        # 3–4. árazás: érvényesítés és életbe léptetés
        for pid in pids:
            seen = set()
            for idx, a in enumerate(acts[pid]):
                if not isinstance(a, SetValuation):
                    continue
                code = self._check_valuation(pid, a, seen)
                results[pid][idx]["code"] = code
                if code == OK:
                    seen.add(a.module)
                    m = self.modules[a.module]
                    if p.regime == "liska":
                        m.price = float(a.price)
                    m.cap = float(a.cap)
        self._check("valuation")

        # 3. a többi akció érvényesítése, fedezetfoglalás a lista sorrendjében
        bids: dict[int, list] = {}
        maint: list = []
        upgr: list = []
        votes: dict[int, float] = {}
        pledges: dict[int, tuple] = {}
        item_bids: dict[int, list] = {}
        for pid in pids:
            pl = self.players[pid]
            cover = pl.balance + pl.heritage
            seen = set()
            for idx, a in enumerate(acts[pid]):
                r = results[pid][idx]
                if r["code"] is not None:
                    continue
                code, amount = self._check_other(pid, a, seen, cover)
                r["code"] = code
                if code != OK:
                    continue
                cover -= amount
                if isinstance(a, Bid):
                    seen.add(("bid", a.module))
                    bids.setdefault(a.module, []).append((float(a.amount), pid, idx))
                elif isinstance(a, Maintain):
                    seen.add(("maint", a.module))
                    maint.append((pid, a.module, amount, idx))
                elif isinstance(a, Upgrade):
                    seen.add(("upg", a.module))
                    upgr.append((pid, a.module, amount, idx))
                elif isinstance(a, Vote):
                    seen.add("vote")
                    votes[pid] = float(a.choice)
                elif isinstance(a, Pledge):
                    seen.add("pledge")
                    pledges[pid] = (float(a.amount), idx)
                elif isinstance(a, BidItem):
                    seen.add(("item", a.item))
                    item_bids.setdefault(a.item, []).append((float(a.amount), pid, idx))

        # 5. licit
        for mid in sorted(bids):
            self._settle(mid, bids[mid], results, events, flows, counts, day)
        if st.ship is not None and st.ship["arrives"] <= day < st.ship["leaves"]:
            self._sell_items(item_bids, results, events, ledger, flows, day)
        self._check("auction", cover=True)

        if day > 0:
            # 6. karbantartás és fejlesztés
            for pid, mid, charge, idx in maint:
                m = self.modules[mid]
                if m.holder != pid:
                    results[pid][idx]["outcome"] = "lapsed"
                    continue
                y = self.nominal(m)
                charge = min(charge, self.maintenance_needed(m))
                self.players[pid].balance -= charge
                m.q = min(1.0, m.q + charge / (p.mu * y))
                ledger["maintenance"] += charge
                results[pid][idx]["outcome"] = "done"
                results[pid][idx]["charged"] = charge
            for pid, mid, cost, idx in upgr:
                m = self.modules[mid]
                if m.holder != pid:
                    results[pid][idx]["outcome"] = "lapsed"
                    continue
                self.players[pid].balance -= cost
                m.level += 1
                ledger["upgrade"] += cost
                results[pid][idx]["outcome"] = "done"
                results[pid][idx]["charged"] = cost
                events.append({"type": "upgrade", "visible": "all", "module": mid,
                               "level": m.level, "player": pid})
            self._check("upkeep", cover=True)

            # 7. események, termelés, kopás, meghibásodás
            delta_event = 0.0
            er = self._rng("event", day)
            if er.random() < p.p_event:
                kind = er.choice(["shock", "boom", "accident"])
                if kind == "accident":
                    delta_event = 0.05
                    events.append({"type": "accident", "visible": "all"})
                else:
                    t = er.choice(sorted(p.types))
                    st.econ[t] = 0.6 if kind == "shock" else 1.4
                    st.econ_timer[t] = 3
                    events.append({"type": kind, "visible": "all", "module_type": t,
                                   "multiplier": st.econ[t]})
            if p.p_shift > 0:
                sr = self._rng("shift", day)
                busy = {x["module_type"] for x in st.shifts}
                free = [t for t in sorted(p.types) if t not in busy]
                if free and sr.random() < p.p_shift:
                    t = sr.choice(free)
                    mult = 1 + p.shift_size if sr.random() < 0.5 else 1 - p.shift_size
                    shift = {"module_type": t, "multiplier": mult,
                             "start": day + p.shift_lead,
                             "end": day + p.shift_lead + p.shift_duration}
                    st.shifts.append(shift)
                    events.append({"type": "shift_announced", "visible": "all", **shift})
            if p.p_reskill > 0 and p.skill_spread > 0:
                for x in [x for x in st.reskills if x["start"] <= day]:      # életbe lép
                    for key, value in x["skills"].items():
                        self.players[int(key)].skill[x["module_type"]] = value
                    st.reskills.remove(x)
                    events.append({"type": "reskill", "visible": "all",
                                   "module_type": x["module_type"]})
                rr = self._rng("reskill", day)
                busy = {x["module_type"] for x in st.reskills}
                free = [t for t in sorted(p.types) if t not in busy]
                if free and rr.random() < p.p_reskill:
                    t = rr.choice(free)
                    sp = p.skill_spread
                    new_skills = {str(pid): rr.uniform(1 - sp, 1 + sp) for pid in pids}
                    st.reskills.append({"module_type": t, "start": day + p.shift_lead,
                                        "skills": new_skills})
                    events.append({"type": "reskill_announced", "visible": "all",
                                   "module_type": t, "start": day + p.shift_lead})
            held_by: dict[int, int] = {}
            for m in self.modules.values():
                if m.holder is not None:
                    held_by[m.holder] = held_by.get(m.holder, 0) + 1
            if p.p_ship > 0 and st.ship is None:
                sr = self._rng("ship", day)
                if sr.random() < p.p_ship:
                    kinds = ["turbo", "pajzs", "javito"]
                    st.ship = {"module_type": sr.choice(sorted(p.types)), "announced": day,
                               "arrives": day + p.ship_lead, "leaves": day + p.ship_lead + p.ship_stay,
                               "bonus": p.ship_bonus, "prize": p.ship_prize, "earned": {},
                               "items": [{"id": k, "kind": sr.choice(kinds), "reserve": p.item_reserve, "sold_to": None}
                                         for k in range(p.ship_items)]}
                    events.append({"type": "ship_announced", "visible": "all",
                                   **{k: v for k, v in st.ship.items() if k != "earned"}})
            if p.p_crisis > 0 and st.crisis is None:
                cr = self._rng("crisis", day)
                if cr.random() < p.p_crisis:
                    st.crisis = {"kind": cr.choice(["meteor", "reaktor", "napkitores"]),
                                 "cost": p.crisis_cost * len(self.modules),
                                 "due": day + p.crisis_lead, "announced": day}
                    events.append({"type": "crisis_announced", "visible": "all", **st.crisis})
            pr = self._rng("prod", day)
            for mid in sorted(self.modules):
                m = self.modules[mid]
                if m.holder is None:
                    m.last_income = m.last_rent = 0.0
                    continue
                mt = p.types[m.type]
                draw = pr.random()
                guarded = self.players[m.holder].items.get("javito_until", 0) > day
                p_fail = 0.0 if guarded else min(1.0, mt.fail * (1 - m.q) ** 2 * (1 + st.hazard))
                if draw < p_fail:
                    income = 0.0
                    m.q = max(0.0, m.q - p.theta)
                    counts["failures"] += 1
                    events.append({"type": "failure", "visible": "all", "module": mid})
                else:
                    income = (self.nominal(m) * m.q * self.econ_multiplier(m.type, day)
                              * (1 - p.eta * st.hazard))
                holder = self.players[m.holder]
                income *= holder.skill.get(m.type, 1.0) * self.span_factor(held_by[m.holder]) * (1 + holder.boost)
                ship = st.ship
                if ship is not None and ship["module_type"] == m.type and ship["arrives"] <= day < ship["leaves"]:
                    income *= 1 + ship["bonus"]                      # a hajó felvásárolja ezt a típust
                    ship["earned"][str(m.holder)] = ship["earned"].get(str(m.holder), 0.0) + income
                if p.regime == "private":
                    tax = p.tax * income
                    st.fund += tax
                    flows["tax"] += tax
                    holder.balance += income - tax
                else:
                    holder.balance += income
                ledger["production"] += income
                m.last_income = income
                if not guarded:
                    m.q = max(0.0, m.q - mt.wear)
            for t in st.econ_timer:
                if st.econ_timer[t] > 0:
                    st.econ_timer[t] -= 1
                    if st.econ_timer[t] == 0:
                        st.econ[t] = 1.0

            # 8. járadék és kamat
            for mid in sorted(self.modules):
                m = self.modules[mid]
                if m.holder is None:
                    continue
                if p.regime == "liska":
                    # az önértékelés a plafon: az fizet többet, aki többet hajlandó megvédeni
                    rent = p.r * (m.cap if p.rent_base == "cap" else m.price)
                elif p.regime == "fixed":
                    rent = p.r * self.opening_price(m)
                else:
                    rent = 0.0
                self.players[m.holder].balance -= rent
                st.fund += rent
                flows["rent"] += rent
                m.last_rent = rent
            for pid in pids:
                pl = self.players[pid]
                interest = p.i * max(0.0, -pl.balance)
                pl.balance -= interest
                st.fund += interest
                flows["interest"] += interest

            # 9. hazard
            n_bar = sum(1 - m.q for m in self.modules.values()) / max(1, len(self.modules))
            st.hazard = min(1.0, max(0.0, st.hazard + p.alpha * n_bar
                                     + p.f * counts["failures"] - p.nu + delta_event))

            if st.hazard >= 1.0 - 1e-12:
                # az összeomlás a hazard frissítésekor dől el; az aznapi alap már nem menti meg
                st.collapsed = True
                st.finished = True
                events.append({"type": "collapse", "visible": "all"})

            # 10. alap: előbb az esedékes válság (tartalék + felajánlások), aztán osztalék, hazard-csökkentés, szavazás
            if st.crisis is not None and st.crisis["due"] <= day and not st.collapsed:
                self._resolve_crisis(pledges, results, events, ledger, flows, counts, day)
            f0 = st.fund
            div_total = st.d * f0
            per_head = div_total / len(pids)
            for pid in pids:
                self.players[pid].balance += per_head
            n_mod = len(self.modules)
            budget = (1 - st.d) * f0
            reduction = 0.0 if st.collapsed else min(p.g_max, st.hazard)
            cost = reduction * p.c_g * n_mod
            spend = min(budget, cost)
            if spend > 0:
                st.hazard = max(0.0, st.hazard - spend / (p.c_g * n_mod))
            st.fund = f0 - div_total - spend
            flows["dividend"] = div_total
            flows["hazard_spend"] = spend
            ledger["hazard_spend"] = spend
            if self.vote_open(day):
                ballots = sorted(votes.get(pid, st.d) for pid in pids)
                new_d = ballots[(len(ballots) - 1) // 2]
                events.append({"type": "vote", "visible": "all", "d": new_d,
                               "voters": len(votes)})
                st.d = new_d

        if st.ship is not None and day >= st.ship["leaves"] - 1 and day >= st.ship["arrives"]:
            earned = st.ship["earned"]                               # a hajó távozik: a legtöbbet termelő jutalmat kap
            winner = None
            if earned:
                best = max(earned.values())
                winner = int(self._rng("ship-win", day).choice(sorted(k for k, v in earned.items() if v == best)))
                self.players[winner].balance += st.ship["prize"]
                ledger["prize"] += st.ship["prize"]; flows["prize"] += st.ship["prize"]
            events.append({"type": "ship_left", "visible": "all", "module_type": st.ship["module_type"],
                           "winner": winner, "prize": st.ship["prize"] if winner is not None else 0.0,
                           "best": max(earned.values()) if earned else 0.0})
            st.ship = None

        # 11. zárás: csőd, összeomlás, belépők, napló
        for pid in pids:
            pl = self.players[pid]
            if pl.balance < -pl.heritage - 1e-7:
                debt = -pl.balance
                paid = min(st.fund, debt)
                st.fund -= paid
                writeoff = debt - paid
                ledger["writeoff"] += writeoff
                freed = []
                for mid in sorted(self.modules):
                    m = self.modules[mid]
                    if m.holder == pid:
                        m.holder = None
                        m.price = m.cap = self.opening_price(m)
                        freed.append(mid)
                pl.balance = 0.0
                pl.heritage = 0.0
                pl.status = "bankrupt"
                counts["bankruptcies"] += 1
                events.append({"type": "bankruptcy", "visible": "all", "player": pid,
                               "modules": freed, "writeoff": writeoff})
        self._missions(events, ledger, flows, day)
        if (not st.finished and day > 0 and day % p.entry_interval == 0
                and len(self.players) < p.max_players):
            new_id = max(self.players) + 1
            self.players[new_id] = Player(id=new_id, heritage=p.heritage, joined=day,
                                          skill=self._skill(new_id))
            events.append({"type": "entry", "visible": "all", "player": new_id})
        if day >= self.end_day:
            st.finished = True
        st.shifts = [x for x in st.shifts if x["end"] > day + 1]

        money_after = self.money()
        expected = (ledger["production"] - ledger["maintenance"] - ledger["upgrade"]
                    - ledger["hazard_spend"] - ledger["crisis"] - ledger["items"] + ledger["prize"]
                    + ledger["writeoff"])
        drift = (money_after - money_before) - expected
        record = {
            "day": day,
            "decisions": {pid: {"actions": [r["action"] for r in results[pid]],
                                "rationale": rationale[pid]} for pid in pids},
            "results": {pid: results[pid] for pid in pids},
            "events": events,
            "ledger": ledger,
            "flows": flows,
            "counts": counts,
            "money_drift": drift,
            "metrics": day_metrics(self, ledger, flows, counts),
        }
        if self.strict and abs(drift) > 1e-6 * max(1.0, abs(money_after)):
            raise InvariantError(f"pénzmegmaradás sérült a(z) {day}. napon: {drift}")
        self._check("close")
        self.history.append(record)
        self.last_events = events
        self.last_results = {pid: results[pid] for pid in pids}
        st.day += 1
        return record

    # ---------- érvényesítés ----------

    def _mod(self, mid: Any) -> Optional[Module]:
        if isinstance(mid, int) and not isinstance(mid, bool):
            return self.modules.get(mid)
        return None

    def _check_valuation(self, pid: int, a: SetValuation, seen: set) -> str:
        p = self.p
        m = self._mod(a.module)
        if m is None or not _num(a.price) or not _num(a.cap):
            return INVALID_TARGET
        if p.regime == "fixed":
            return NOT_IN_REGIME
        if m.holder != pid:
            return NOT_HOLDER
        if a.module in seen:
            return DUPLICATE
        if p.regime == "private":
            return OK if a.cap >= p.p_min else INVALID_TARGET
        if a.price < p.p_min:
            return INVALID_TARGET
        if a.price < m.price * (1 - p.delta) - EPS:
            return PRICE_DROP_LIMIT
        if a.cap < a.price:
            return CAP_BELOW_PRICE
        return OK

    def _check_other(self, pid: int, a: Any, seen: set, cover: float) -> tuple[str, float]:
        p = self.p
        if isinstance(a, Vote):
            if not self.vote_open():
                return NO_OPEN_VOTE, 0.0
            if not _num(a.choice) or a.choice not in VOTE_OPTIONS:
                return INVALID_TARGET, 0.0
            if "vote" in seen:
                return DUPLICATE, 0.0
            return OK, 0.0
        if isinstance(a, BidItem):
            ship = self.station.ship
            if ship is None or not ship["arrives"] <= self.station.day < ship["leaves"]:
                return NO_SHIP, 0.0
            if not isinstance(a.item, int) or isinstance(a.item, bool) or not 0 <= a.item < len(ship["items"]):
                return INVALID_TARGET, 0.0
            if ship["items"][a.item]["sold_to"] is not None:      # már elkelt
                return INVALID_TARGET, 0.0
            if not _num(a.amount) or a.amount <= 0:
                return INVALID_TARGET, 0.0
            if ("item", a.item) in seen:
                return DUPLICATE, 0.0
            if a.amount < ship["items"][a.item]["reserve"] - EPS:
                return BID_BELOW_MIN, 0.0
            if a.amount > cover + EPS:
                return INSUFFICIENT_COVER, 0.0
            return OK, float(a.amount)
        if isinstance(a, Pledge):
            if self.station.crisis is None or self.station.crisis["due"] > self.station.day:
                return NO_CRISIS, 0.0
            if not _num(a.amount) or a.amount <= 0:
                return INVALID_TARGET, 0.0
            if "pledge" in seen:
                return DUPLICATE, 0.0
            if a.amount > cover + EPS:
                return INSUFFICIENT_COVER, 0.0
            return OK, float(a.amount)
        m = self._mod(getattr(a, "module", None))
        if m is None:
            return INVALID_TARGET, 0.0
        if isinstance(a, Bid):
            if not _num(a.amount) or a.amount <= 0:
                return INVALID_TARGET, 0.0
            if m.holder == pid:
                return OWN_MODULE, 0.0
            if ("bid", a.module) in seen:
                return DUPLICATE, 0.0
            lo = self.min_bid(m)
            if lo is None:
                return NOT_IN_REGIME, 0.0
            if a.amount < lo - EPS:
                return BID_BELOW_MIN, 0.0
            if a.amount > cover + EPS:
                return INSUFFICIENT_COVER, 0.0
            return OK, float(a.amount)
        if isinstance(a, Maintain):
            if not _num(a.amount) or a.amount <= 0:
                return INVALID_TARGET, 0.0
            if m.holder != pid:
                return NOT_HOLDER, 0.0
            if ("maint", a.module) in seen:
                return DUPLICATE, 0.0
            charge = min(float(a.amount), self.maintenance_needed(m))
            if charge > cover + EPS:
                return INSUFFICIENT_COVER, 0.0
            return OK, charge
        if isinstance(a, Upgrade):
            if m.holder != pid:
                return NOT_HOLDER, 0.0
            if ("upg", a.module) in seen:
                return DUPLICATE, 0.0
            cost = self.upgrade_cost(m)
            if cost is None:
                return MAX_LEVEL, 0.0
            if cost > cover + EPS:
                return INSUFFICIENT_COVER, 0.0
            return OK, cost
        return INVALID_TARGET, 0.0

    # ---------- licit elszámolása ----------

    def _settle(self, mid, blist, results, events, flows, counts, day):
        p, st = self.p, self.station
        m = self.modules[mid]
        top = max(b[0] for b in blist)
        tied = sorted(b for b in blist if b[0] == top)
        win = tied[0] if len(tied) == 1 else self._rng("tie", day, mid).choice(tied)
        amount, buyer_id, widx = win
        for b in blist:
            if b is not win:
                results[b[1]][b[2]]["outcome"] = "outbid"
        buyer = self.players[buyer_id]
        res = results[buyer_id][widx]
        if m.holder is None:
            buyer.balance -= amount
            st.fund += amount
            flows["sales"] += amount
            m.holder = buyer_id
            if p.regime == "fixed":
                m.price = m.cap = self.opening_price(m)
            else:
                m.price = m.cap = amount
            counts["acquired"] += 1
            res["outcome"] = "won"
            events.append({"type": "acquired", "visible": "all", "module": mid,
                           "player": buyer_id, "amount": amount})
            return
        seller = self.players[m.holder]
        if p.regime == "liska":
            if amount <= m.cap + EPS:
                m.price = amount
                m.cap = max(m.cap, amount)
                counts["retained"] += 1
                res["outcome"] = "retained"
                events.append({"type": "retained", "visible": "all", "module": mid,
                               "holder": m.holder, "bidder": buyer_id, "price": amount})
                return
            surplus = amount - m.price
            buyer.balance -= amount
            seller.balance += m.price + p.s * surplus
            st.fund += (1 - p.s) * surplus
            flows["surplus"] += (1 - p.s) * surplus
        else:  # private
            if amount < m.cap - EPS:
                res["outcome"] = "below_ask"
                return
            buyer.balance -= amount
            seller.balance += amount
        events.append({"type": "takeover", "visible": "all", "module": mid,
                       "from": m.holder, "to": buyer_id, "amount": amount})
        m.holder = buyer_id
        m.price = m.cap = amount
        counts["takeovers"] += 1
        res["outcome"] = "won"

    # ---------- válság ----------

    def _resolve_crisis(self, pledges, results, events, ledger, flows, counts, day):
        """Az elhárítás árát előbb az alap állja, a hiányt a felajánlások arányosan. Ha így sem elég,
        senki nem fizet, a hazard megugrik, és néhány modul megsérül."""
        p, st = self.p, self.station
        cost = st.crisis["cost"]; from_fund = min(st.fund, cost); short = cost - from_fund
        offered = {pid: amt for pid, (amt, _) in pledges.items()}
        total = sum(offered.values())
        base = {"visible": "all", "kind": st.crisis["kind"], "cost": cost}
        if total + 1e-9 >= short:
            ratio = short / total if total > 0 else 0.0
            paid = {}
            for pid, (amt, idx) in pledges.items():
                paid[pid] = amt * ratio
                self.players[pid].balance -= paid[pid]
                results[pid][idx]["outcome"] = "paid"; results[pid][idx]["charged"] = paid[pid]
            st.fund -= from_fund
            ledger["crisis"] = cost; flows["crisis"] = cost; counts["crises_averted"] += 1
            events.append({"type": "crisis_averted", **base, "from_fund": from_fund, "pledged": paid})
        else:
            for pid, (amt, idx) in pledges.items():
                results[pid][idx]["outcome"] = "refunded"
            kind = st.crisis["kind"]; rng = self._rng("crisis-hit", day)
            held = sorted(m.id for m in self.modules.values() if m.holder is not None)
            hit, shielded, jump = [], [], p.crisis_hazard
            if kind == "napkitores":                 # napkitörés: két körre megfeleződik minden hozam, modul nem sérül
                jump = p.crisis_hazard / 3
                for t in st.econ:
                    st.econ[t] = 0.5; st.econ_timer[t] = 2
            else:
                if kind == "reaktor":                # reaktorhiba: kétszeres hazard-ugrás, a gyárak sérülnek
                    jump = 2 * p.crisis_hazard
                    pool = [i for i in held if self.modules[i].type == "gyar"] or held
                    targets = rng.sample(pool, min(max(1, p.crisis_hits // 2), len(pool)))
                else:                                # meteorraj: véletlen modulok sérülnek
                    targets = rng.sample(held, min(p.crisis_hits, len(held)))
                used = set()
                for mid in sorted(targets):
                    owner = self.players[self.modules[mid].holder]
                    if owner.items.get("pajzs", 0) > 0 or owner.id in used:     # a pajzs az egész birtokot megvédi, egyszer
                        if owner.id not in used:
                            owner.items["pajzs"] -= 1; used.add(owner.id)
                        shielded.append(mid); continue
                    self.modules[mid].q = max(0.0, self.modules[mid].q - p.crisis_damage); hit.append(mid)
            st.hazard = min(1.0, st.hazard + jump)
            counts["crises_hit"] += 1
            events.append({"type": "crisis_hit", **base, "from_fund": from_fund, "offered": offered,
                           "missing": short - total, "modules": hit, "shielded": shielded})
            if st.hazard >= 1.0 - 1e-12:
                st.collapsed = True; st.finished = True
                events.append({"type": "collapse", "visible": "all"})
        st.crisis = None

    # ---------- megbízások ----------

    MISSIONS = {"fejlesztes": 300, "hodito": 350, "harom_tipus": 400, "mintagazda": 300, "negy_modul": 400}

    def _mission_done(self, ms, took, upgraded, holdings) -> list:
        kind = ms["kind"]
        if kind == "fejlesztes":
            return sorted(upgraded)
        if kind == "hodito":
            return sorted(took)
        if kind == "harom_tipus":
            return sorted(pid for pid, mods in holdings.items() if {m.type for m in mods} >= set(self.p.types))
        if kind == "negy_modul":
            return sorted(pid for pid, mods in holdings.items() if len(mods) >= 4)
        done = []                                    # mintagazda: legalább két modul, mind 85% fölött, három napon át
        for pid in sorted(self.players):
            mods = holdings.get(pid, [])
            ok = len(mods) >= 2 and all(m.q >= 0.85 for m in mods)
            ms["progress"][str(pid)] = ms["progress"].get(str(pid), 0) + 1 if ok else 0
            if ms["progress"][str(pid)] >= 3:
                done.append(pid)
        return done

    def _missions(self, events, ledger, flows, day):
        """Nap végén: a teljesített megbízás jutalmat fizet (kívülről jövő pénz), a lejárt eltűnik, és új jön."""
        p, st = self.p, self.station
        if p.mission_slots <= 0 or st.finished:
            return
        holdings: dict = {}
        for m in self.modules.values():
            if m.holder is not None:
                holdings.setdefault(m.holder, []).append(m)
        took = {ev["to"] for ev in events if ev["type"] == "takeover"}
        upgraded = {ev["player"] for ev in events if ev["type"] == "upgrade"}
        public = lambda ms: {k: v for k, v in ms.items() if k != "progress"}
        keep = []
        for ms in st.missions:
            done = self._mission_done(ms, took, upgraded, holdings)
            if done:
                w = done[0] if len(done) == 1 else self._rng("mission", day, ms["id"]).choice(done)
                self.players[w].balance += ms["reward"]
                ledger["prize"] += ms["reward"]; flows["prize"] += ms["reward"]
                events.append({"type": "mission_done", "visible": "all", "mission": public(ms), "winner": w})
            elif day >= ms["expires"]:
                events.append({"type": "mission_expired", "visible": "all", "mission": public(ms)})
            else:
                keep.append(ms)
        st.missions = keep
        while day % p.mission_interval == 0 and len(st.missions) < p.mission_slots:
            rng = self._rng("mission-new", day, st.mission_seq)
            active = {ms["kind"] for ms in st.missions}
            blocked = {k for k in ("harom_tipus", "negy_modul")          # amit valaki már most teljesít, az nem cél
                       if self._mission_done({"kind": k, "progress": {}}, set(), set(), holdings)}
            pool = [k for k in sorted(self.MISSIONS) if k not in active and k not in blocked]
            if not pool:
                break
            kind = rng.choice(pool)
            ms = {"id": st.mission_seq, "kind": kind, "reward": self.MISSIONS[kind] * p.mission_reward / 350.0,
                  "issued": day, "expires": day + p.mission_days, "progress": {}}
            st.mission_seq += 1
            st.missions.append(ms)
            events.append({"type": "mission_new", "visible": "all", "mission": public(ms)})

    # ---------- a hajó eszközei ----------

    def _sell_items(self, item_bids, results, events, ledger, flows, day):
        """Amíg a hajó itt van, naponta zárt licit megy a még el nem kelt eszközökre.
        Eszközönként a legmagasabb licit nyer; a vételár kikerül a gazdaságból."""
        ship = self.station.ship
        for item in ship["items"]:
            bids = item_bids.get(item["id"], [])
            if not bids:
                continue
            top = max(b[0] for b in bids); tied = sorted(b for b in bids if b[0] == top)
            win = tied[0] if len(tied) == 1 else self._rng("item", day, item["id"]).choice(tied)
            for b in bids:
                results[b[1]][b[2]]["outcome"] = "won" if b is win else "outbid"
            amount, pid, _ = win; pl = self.players[pid]
            pl.balance -= amount; ledger["items"] += amount; flows["items"] += amount
            item["sold_to"] = pid
            if item["kind"] == "turbo":
                pl.boost = min(0.3, pl.boost + self.p.boost_step)
            elif item["kind"] == "pajzs":
                pl.items["pajzs"] = pl.items.get("pajzs", 0) + 1
            else:                                    # javítókészlet: minden modulja hibátlan, és egy ideig nem kopik
                for m in self.modules.values():
                    if m.holder == pid:
                        m.q = 1.0
                pl.items["javito_until"] = day + self.p.item_repair_rounds
            events.append({"type": "item_sold", "visible": "all", "item": item["id"], "kind": item["kind"],
                           "player": pid, "amount": amount})

    # ---------- invariánsok ----------

    def _check(self, phase: str, cover: bool = False) -> None:
        if not self.strict:
            return
        p, st = self.p, self.station
        if not (0.0 <= st.hazard <= 1.0):
            raise InvariantError(f"{phase}: hazard határon kívül: {st.hazard}")
        for m in self.modules.values():
            if not (0.0 <= m.q <= 1.0):
                raise InvariantError(f"{phase}: állapot határon kívül: {m}")
            if not (1 <= m.level <= p.max_level):
                raise InvariantError(f"{phase}: szint határon kívül: {m}")
            if m.holder is not None and m.holder not in self.players:
                raise InvariantError(f"{phase}: nem létező birtokos: {m}")
            if p.regime == "liska":
                if m.price < p.p_min - EPS or m.cap < m.price - EPS:
                    raise InvariantError(f"{phase}: ár/plafon sérült: {m}")
        if cover:
            for pl in self.players.values():
                if pl.balance < -pl.heritage - 1e-6:
                    raise InvariantError(f"{phase}: fedezet sérült: {pl}")

    # ---------- mentés, eredmény ----------

    def snapshot(self) -> dict:
        return {
            "params": self.p.to_dict(), "seed": self.seed, "end_day": self.end_day,
            "station": asdict(self.station),
            "players": [asdict(self.players[k]) for k in sorted(self.players)],
            "modules": [asdict(self.modules[k]) for k in sorted(self.modules)],
            "last_events": self.last_events,
            "last_results": {str(k): v for k, v in self.last_results.items()},
        }

    @classmethod
    def from_snapshot(cls, snap: dict, strict: bool = False) -> "Engine":
        e = cls(Params.from_dict(snap["params"]), seed=snap["seed"], strict=strict,
                _restore=True)
        e.end_day = snap["end_day"]
        e.station = Station(**snap["station"])
        e.players = {d["id"]: Player(**d) for d in snap["players"]}
        e.modules = {d["id"]: Module(**d) for d in snap["modules"]}
        e.last_events = snap["last_events"]
        e.last_results = {int(k): v for k, v in snap["last_results"].items()}
        return e

    def result(self) -> dict:
        st = self.station
        ranking = sorted(self.players.values(), key=lambda q: (-q.balance, q.id))
        return {
            "finished": st.finished, "collapsed": st.collapsed, "days": st.day,
            "winner": None if st.collapsed or not st.finished else ranking[0].id,
            "ranking": [{"id": q.id, "balance": q.balance, "status": q.status}
                        for q in ranking],
        }
