"""A tíz invariáns és a fő szabályok tesztjei."""
import json

import pytest

from liska import (Bid, BidItem, Decision, Engine, InvariantError, Maintain, Params, Pledge, SetValuation,
                   Upgrade, Vote, replay, run_game)
from liska.agents import HeuristicPolicy, RandomPolicy
from liska.params import REGIMES
from liska.runner import log_lines

MIX = ["ovatos", "terjeszkedo", "potyautas"]


def heur(pid):
    return HeuristicPolicy(MIX[pid % 3])


def rand(pid):
    return RandomPolicy()


def state_of(e):
    s = e.snapshot()
    s.pop("last_results")
    return json.dumps(s, sort_keys=True)


def midgame(regime="liska", **kw):
    """Állapot a 0. nap után: a 0. játékos birtokolja a 0. modult 1000-es áron, 1500-as plafonnal."""
    e = Engine(Params(regime=regime, p_event=0.0, **kw), seed=3, strict=True)
    e.station.day = 1
    m = e.modules[0]
    m.holder, m.price, m.cap = 0, 1000.0, 1500.0
    return e


# ---------- 1., 4., 5., 6., 7., 8. invariáns: véletlen ügynökökkel, minden rendben ----------

@pytest.mark.parametrize("regime", REGIMES)
def test_invariants_hold_with_random_agents(regime):
    errors = 0
    for seed in range(60):
        e = Engine(Params(regime=regime), seed=seed, strict=True)  # strict: fázisonként ellenőriz
        run_game(e, rand)
        assert e.station.finished
        assert max(abs(r["money_drift"]) for r in e.history) < 1e-6
        errors += len(e.agent_errors)
    assert errors > 0  # a hibázó ügynök útja is lefutott


@pytest.mark.parametrize("regime", REGIMES)
def test_invariants_hold_in_full_length_random_games(regime):
    # a véletlen ügynökök hamar összeomlasztják az állomást; hazard nélkül végigmegy a játék,
    # így a kései csődök, belépők és gazdacserék is lefutnak
    bankruptcies = takeovers = 0
    for seed in range(30):
        e = Engine(Params(regime=regime, alpha=0.0, f=0.0), seed=seed, strict=True)
        run_game(e, rand)
        assert e.station.finished and not e.station.collapsed and e.station.day > 55
        assert max(abs(r["money_drift"]) for r in e.history) < 1e-6
        bankruptcies += sum(r["counts"]["bankruptcies"] for r in e.history)
        takeovers += sum(r["counts"]["takeovers"] for r in e.history)
    if regime != "fixed":       # fix bérletnél nincs átvétel
        assert takeovers > 0
    if regime != "private":     # járadék nélkül a véletlen ügynök sem megy csődbe
        assert bankruptcies > 0


@pytest.mark.parametrize("regime", REGIMES)
def test_invariants_hold_with_heuristic_agents(regime):
    for seed in range(20):
        e = Engine(Params(regime=regime), seed=seed, strict=True)
        run_game(e, heur)
        assert e.station.finished


# ---------- 2. determinizmus, 3. visszajátszás ----------

@pytest.mark.parametrize("policy", [rand, heur])
def test_same_seed_gives_identical_log(policy):
    a, b = Engine(seed=11), Engine(seed=11)
    run_game(a, policy)
    run_game(b, policy)
    assert log_lines(a) == log_lines(b)
    c = Engine(seed=12)
    run_game(c, policy)
    assert log_lines(a) != log_lines(c)


@pytest.mark.parametrize("regime", REGIMES)
def test_replay_from_log_reproduces_state(regime):
    e = Engine(Params(regime=regime), seed=5)
    run_game(e, rand)
    rows = [json.loads(line) for line in log_lines(e)]   # a naplón keresztül, nem memóriából
    again = replay(Params.from_dict(rows[0]["params"]), rows[0]["seed"], rows[1:])
    assert state_of(again) == state_of(e)


def test_snapshot_resume_matches_uninterrupted_run():
    full = Engine(seed=7)
    run_game(full, heur)
    part = Engine(seed=7)
    for _ in range(20):
        part.step({pid: heur(pid).decide(part.observe(pid)) for pid in part.players})
    resumed = Engine.from_snapshot(json.loads(json.dumps(part.snapshot())))
    run_game(resumed, heur)
    assert state_of(resumed) == state_of(full)


# ---------- 7. hatástalan elutasítás, 8. hibatűrés, 10. sorrendfüggetlenség ----------

def test_rejected_actions_change_nothing():
    illegal = Decision([Bid(999, 100), Bid(0, -5), SetValuation(3, 1, 1), Maintain(5, 50),
                        Upgrade(7), Vote(0.3), Bid(1, 1e12), "szemét", {"type": "Bid"}])

    def run(first):
        e = Engine(Params(p_event=0.0), seed=2, strict=True)
        for _ in range(15):
            d = {pid: heur(pid).decide(e.observe(pid)) for pid in e.players}
            d[0] = first
            rec = e.step(d)
        return e, rec

    a, rec = run(illegal)
    b, _ = run(None)
    assert state_of(a) == state_of(b)
    assert all(r["code"] != "OK" for r in rec["results"][0])


def test_failing_agent_passes_and_game_continues():
    class Broken:
        def decide(self, obs):
            raise ValueError("elromlott")

    e = Engine(seed=4, strict=True)
    run_game(e, lambda pid: Broken() if pid == 0 else heur(pid))
    assert e.station.finished
    assert any(err["player"] == 0 for err in e.agent_errors)


def test_result_independent_of_polling_order():
    def run(reverse):
        e = Engine(seed=9)
        while not e.station.finished:
            decisions = {}
            for pid in sorted(e.players, reverse=reverse):
                try:
                    decisions[pid] = rand(pid).decide(e.observe(pid))
                except RuntimeError:
                    decisions[pid] = None
            e.step(decisions)
        return state_of(e)

    assert run(False) == run(True)


# ---------- 9. rejtett adat ----------

def test_observation_hides_others_cap_balance_and_end_day():
    e = midgame()
    e.players[0].balance = 1234.56789
    e.modules[0].cap = 98765.4321
    text = json.dumps(e.observe(1))
    assert "1234.56789" not in text and "98765.4321" not in text
    assert '"end_day"' not in text and '"cap":' not in text
    for v in e.players[0].skill.values():
        assert repr(v) not in text
    own = e.observe(0)
    assert own["my_modules"][0]["cap"] == 98765.4321 and own["me"]["balance"] == 1234.56789


# ---------- licit ----------

def settle(e, mid, bids):
    results = {pid: [{}] for _, pid, _ in bids}
    events, flows = [], {"sales": 0.0, "surplus": 0.0}
    counts = {"takeovers": 0, "retained": 0, "acquired": 0}
    e._settle(mid, bids, results, events, flows, counts, e.station.day)
    return results, flows, counts


def test_bid_under_cap_is_retained_and_raises_price():
    e = midgame()
    results, _, counts = settle(e, 0, [(1400.0, 1, 0)])
    m = e.modules[0]
    assert (m.holder, m.price, m.cap) == (0, 1400.0, 1500.0)
    assert e.players[1].balance == 0 and e.players[0].balance == 0
    assert results[1][0]["outcome"] == "retained" and counts["retained"] == 1


def test_bid_over_cap_takes_over_and_splits_surplus():
    e = midgame()
    _, flows, counts = settle(e, 0, [(2000.0, 1, 0), (1800.0, 2, 0)])
    m = e.modules[0]
    assert (m.holder, m.price, m.cap) == (1, 2000.0, 2000.0)
    assert e.players[1].balance == -2000.0
    assert e.players[0].balance == 1000.0 + 0.5 * 1000.0   # P + s·(B−P)
    assert e.station.fund == 500.0 and flows["surplus"] == 500.0
    assert e.players[2].balance == 0 and counts["takeovers"] == 1


def test_unowned_module_sale_goes_to_fund():
    e = midgame()
    settle(e, 1, [(1500.0, 2, 0)])
    assert e.modules[1].holder == 2 and e.station.fund == 1500.0
    assert e.players[2].balance == -1500.0


def test_private_regime_sells_only_at_ask_and_seller_gets_all():
    e = midgame("private")
    settle(e, 0, [(1400.0, 1, 0)])
    assert e.modules[0].holder == 0
    settle(e, 0, [(1600.0, 1, 0)])
    assert e.modules[0].holder == 1 and e.players[0].balance == 1600.0 and e.station.fund == 0


def test_validation_codes():
    e = midgame()
    rec = e.step({
        0: Decision([SetValuation(0, 900.0, 1500.0),      # 10%-os árcsökkentés
                     SetValuation(0, 1000.0, 900.0),      # plafon az ár alatt
                     Bid(0, 2000.0), Upgrade(1), Vote(0.5)]),
        1: Decision([Bid(0, 1010.0),                      # a minimális lépés alatt
                     Bid(0, 2500.0), Bid(1, 1400.0),      # a második már nem fér a fedezetbe
                     Bid(0, 1100.0)]),
    })
    assert [r["code"] for r in rec["results"][0]] == [
        "PRICE_DROP_LIMIT", "CAP_BELOW_PRICE", "OWN_MODULE", "NOT_HOLDER", "NO_OPEN_VOTE"]
    assert [r["code"] for r in rec["results"][1]] == [
        "BID_BELOW_MIN", "OK", "INSUFFICIENT_COVER", "DUPLICATE"]


def test_fixed_regime_rejects_valuation_and_bids_on_held_modules():
    e = midgame("fixed")
    rec = e.step({0: Decision([SetValuation(0, 1200.0, 1500.0)]), 1: Decision([Bid(0, 5000.0)])})
    assert rec["results"][0][0]["code"] == "NOT_IN_REGIME"
    assert rec["results"][1][0]["code"] == "NOT_IN_REGIME"
    assert e.modules[0].holder == 0


def test_maintenance_lapses_when_module_changes_hands():
    e = midgame()
    e.modules[0].q = 0.5
    rec = e.step({0: Decision([Maintain(0, 100.0)]), 1: Decision([Bid(0, 2000.0)])})
    assert rec["results"][0][0]["outcome"] == "lapsed"
    assert rec["ledger"]["maintenance"] == 0.0 and e.modules[0].holder == 1


def test_maintenance_charges_only_what_is_needed():
    e = midgame()
    e.modules[0].q = 0.95
    need = e.maintenance_needed(e.modules[0])
    rec = e.step({0: Decision([Maintain(0, 1e6)])})
    assert rec["results"][0][0]["charged"] == pytest.approx(need)


# ---------- nap, alap, csőd, összeomlás ----------

def test_day_zero_runs_only_the_auction():
    e = Engine(Params(p_event=0.0), seed=1, strict=True)
    rec = e.step({0: Decision([Bid(0, e.opening_price(e.modules[0]))])})
    assert rec["ledger"]["production"] == 0 and rec["flows"]["dividend"] == 0
    assert e.modules[0].holder == 0 and e.station.hazard == e.p.g0


def test_rent_interest_and_income_formulas():
    e = midgame()
    m, g = e.modules[0], e.station.hazard
    q, y = m.q, e.nominal(m)
    skill = e.players[0].skill[m.type]
    assert 0.8 <= skill <= 1.2
    rec = e.step({})
    assert rec["ledger"]["production"] == pytest.approx(y * q * (1 - 0.5 * g) * skill)
    assert rec["flows"]["rent"] == pytest.approx(0.015 * 1500.0)     # a járadék a plafon (1500) után jár
    assert m.q == pytest.approx(q - e.p.types[m.type].wear)


def test_vote_takes_lower_median_and_nonvoters_keep_current():
    e = midgame()
    e.station.day = 7
    rec = e.step({pid: Decision([Vote(1.0)]) for pid in (0, 1, 2, 3)})   # 4 szavazat 1,0; 4 marad 0,5
    assert e.station.d == 0.5
    e.station.day = 14
    e.step({pid: Decision([Vote(1.0)]) for pid in (0, 1, 2, 3, 4)})
    assert e.station.d == 1.0


def test_bankruptcy_frees_modules_and_keeps_player_without_credit():
    e = midgame()
    e.modules[0].price = e.modules[0].cap = 1e6      # a járadék a hitelkeret alá viszi
    rec = e.step({})
    pl = e.players[0]
    assert (pl.status, pl.heritage, pl.balance) == ("bankrupt", 0.0, 0.0)
    assert e.modules[0].holder is None and e.modules[0].price == e.opening_price(e.modules[0])
    assert rec["ledger"]["writeoff"] > 0 and rec["counts"]["bankruptcies"] == 1


def test_collapse_ends_game_without_winner():
    e = midgame()
    e.station.hazard = 0.999
    e.station.fund = 1e6                             # a teli alap sem menti meg aznap
    for m in e.modules.values():
        m.q = 0.0
    rec = e.step({})
    assert e.station.collapsed and e.station.finished and e.result()["winner"] is None
    assert rec["flows"]["hazard_spend"] == 0.0
    with pytest.raises(RuntimeError):
        e.step({})


def test_strict_mode_catches_broken_state():
    e = midgame()
    e.modules[0].q = 1.7
    with pytest.raises(InvariantError):
        e.step({})


def test_skill_is_seeded_per_player_and_can_be_switched_off():
    a, b = Engine(seed=5), Engine(seed=5)
    assert a.players[0].skill == b.players[0].skill != a.players[1].skill
    assert set(a.players[0].skill) == set(a.p.types)
    assert Engine(Params(skill_spread=0.0), seed=5).players[0].skill == {}


# ---------- kapcsolható bővítések: piaci fordulat, növekedési fék ----------

def test_default_rules_include_span_and_reskill_but_not_market_shift():
    p = Params()
    assert p.span_penalty == 0.10 and p.p_reskill == 0.1 and p.p_shift == 0.0
    e = Engine(p, seed=1)
    run_game(e, heur)
    kinds = {ev["type"] for r in e.history for ev in r["events"]}
    assert "reskill_announced" in kinds and "shift_announced" not in kinds


def test_announced_shift_is_forecast_and_applies_only_in_its_window():
    e = midgame(p_shift=1.0, skill_spread=0.0)
    rec = e.step({})                                   # 1. nap: bejelentés
    ann = [ev for ev in rec["events"] if ev["type"] == "shift_announced"][0]
    assert (ann["start"], ann["end"]) == (1 + 3, 1 + 3 + 10)
    assert e.observe(1)["station"]["forecast"][0]["module_type"] == ann["module_type"]

    e = midgame(skill_spread=0.0)
    m = e.modules[0]
    e.station.shifts = [{"module_type": m.type, "multiplier": 1.35, "start": 2, "end": 3}]
    base = lambda: e.nominal(m) * m.q * (1 - 0.5 * e.station.hazard)   # noqa: E731
    for day, mult in ((1, 1.0), (2, 1.35), (3, 1.0)):
        expected = base() * mult
        assert e.step({})["ledger"]["production"] == pytest.approx(expected), day
    assert e.station.shifts == []                      # a lejárt fordulat kikerül az állapotból


def test_span_penalty_reduces_every_module_of_a_large_holder():
    e = midgame(span_penalty=0.05, span_free=3, skill_spread=0.0)
    for mid in range(5):                               # öt modul: 2-vel a szabad keret fölött
        m = e.modules[mid]
        m.holder, m.price, m.cap, m.q = 0, 100.0, 100.0, 1.0
    assert e.span_factor(5) == pytest.approx(0.9) and e.span_factor(3) == 1.0
    assert e.observe(0)["me"]["span_factor"] == pytest.approx(0.9)
    full = sum(e.nominal(e.modules[mid]) for mid in range(5)) * (1 - 0.5 * e.station.hazard)
    assert e.step({})["ledger"]["production"] == pytest.approx(0.9 * full)


@pytest.mark.parametrize("regime", REGIMES)
def test_invariants_hold_with_extensions_on(regime):
    for seed in range(15):
        for policy in (rand, heur):
            e = Engine(Params(regime=regime, p_shift=1 / 7, span_penalty=0.1, p_reskill=1 / 10),
                       seed=seed, strict=True)
            run_game(e, policy)
            assert e.station.finished
            assert max(abs(r["money_drift"]) for r in e.history) < 1e-6


def test_reskill_is_announced_privately_and_applies_on_its_start_day():
    e = midgame(p_reskill=1.0)
    old = dict(e.players[0].skill)
    rec = e.step({})                                   # 1. nap: bejelentés
    ann = [ev for ev in rec["events"] if ev["type"] == "reskill_announced"][0]
    t, start = ann["module_type"], ann["start"]
    assert start == 1 + 3 and "skills" not in ann
    hidden = e.station.reskills[0]["skills"]
    obs = e.observe(0)
    assert obs["me"]["skill_next"] == [{"module_type": t, "start": start, "value": hidden["0"]}]
    text = json.dumps(obs)
    assert all(repr(v) not in text for k, v in hidden.items() if k != "0")   # másoké rejtve marad
    assert e.players[0].skill == old
    while e.station.day <= start:
        e.step({})
    assert e.players[0].skill[t] == hidden["0"]


def test_rent_follows_the_cap_and_a_low_price_saves_nothing():
    lo, hi = midgame(), midgame()
    lo.modules[0].price, hi.modules[0].price = 200.0, 1500.0           # ugyanaz a plafon, más ár
    assert lo.step({})["flows"]["rent"] == hi.step({})["flows"]["rent"] == pytest.approx(0.015 * 1500.0)
    old = midgame(rent_base="price")                                   # a régi szabály kapcsolóval elérhető
    assert old.step({})["flows"]["rent"] == pytest.approx(0.015 * 1000.0)
    with pytest.raises(ValueError):
        Params(rent_base="valami")


def test_retained_bid_raises_the_price_but_not_the_rent():
    e = midgame()
    rec = e.step({1: Decision([Bid(0, 1400.0)])})
    m = e.modules[0]
    assert (m.holder, m.price, m.cap) == (0, 1400.0, 1500.0)
    assert rec["flows"]["rent"] == pytest.approx(0.015 * 1500.0)


# ---------- válsághelyzet ----------

def crisis_game(fund, **kw):
    e = midgame(p_crisis=0.0, p_ship=0.0, mission_slots=0, **kw)
    e.station.fund = fund
    e.station.d = 0.0                                   # ne zavarjon be az osztalék
    e.station.crisis = {"kind": "meteor", "cost": 1200.0, "due": 1, "announced": 0}
    for pl in e.players.values():
        pl.balance = 1000.0
    return e


def test_crisis_is_paid_from_the_fund_first():
    e = crisis_game(fund=5000.0)
    rec = e.step({1: Decision([Pledge(300.0)])})
    ev = next(x for x in rec["events"] if x["type"] == "crisis_averted")
    assert ev["from_fund"] == 1200.0 and ev["pledged"] == {1: 0.0}        # a felajánlásra nem volt szükség
    assert rec["ledger"]["crisis"] == 1200.0 and e.station.crisis is None
    assert rec["results"][1][0]["outcome"] == "paid" and rec["results"][1][0]["charged"] == 0.0


def test_crisis_shortfall_is_shared_in_proportion_to_pledges():
    e = crisis_game(fund=200.0)
    rec = e.step({1: Decision([Pledge(1000.0)]), 2: Decision([Pledge(500.0)])})    # a hiány 1000 mínusz az aznapi járadék
    ev = next(x for x in rec["events"] if x["type"] == "crisis_averted")
    short = 1200.0 - ev["from_fund"]
    assert ev["pledged"][1] == pytest.approx(short * 2 / 3) and ev["pledged"][2] == pytest.approx(short / 3)
    assert rec["counts"]["crises_averted"] == 1 and e.station.hazard < 0.3


def test_failed_crisis_refunds_pledges_and_damages_the_station():
    e = crisis_game(fund=0.0)
    hz = e.station.hazard
    before = {pid: pl.balance for pid, pl in e.players.items()}
    rec = e.step({1: Decision([Pledge(100.0)])})
    ev = next(x for x in rec["events"] if x["type"] == "crisis_hit")
    assert ev["offered"] == {1: 100.0} and ev["missing"] > 900 and rec["results"][1][0]["outcome"] == "refunded"
    assert rec["ledger"]["crisis"] == 0.0 and e.players[1].balance >= before[1]   # nem vontak le semmit
    assert e.station.hazard > hz + 0.1 and e.modules[0].q < 0.6                   # az egyetlen birtokolt modul megsérült


def test_pledge_is_only_valid_on_the_due_day_and_needs_cover():
    e = crisis_game(fund=0.0)
    e.station.crisis["due"] = 3
    assert e.observe(0)["station"]["crisis"]["due"] == 3 and not e.observe(0)["station"]["pledge_open"]
    rec = e.step({1: Decision([Pledge(100.0)])})
    assert rec["results"][1][0]["code"] == "NO_CRISIS" and e.station.crisis is not None
    e.station.day = 3
    rec = e.step({1: Decision([Pledge(1e9), Pledge(50.0), Pledge(60.0)])})
    assert [r["code"] for r in rec["results"][1]] == ["INSUFFICIENT_COVER", "OK", "DUPLICATE"]


def test_crises_are_announced_ahead_and_show_up_in_full_games():
    seen = {"crisis_announced": 0, "crisis_averted": 0, "crisis_hit": 0}
    for seed in range(12):
        e = Engine(Params.short(), seed=seed, strict=True)
        run_game(e, heur)
        for r in e.history:
            for ev in r["events"]:
                if ev["type"] in seen:
                    seen[ev["type"]] += 1
                    if ev["type"] == "crisis_announced":
                        assert ev["due"] == r["day"] + 2
    assert seen["crisis_announced"] > 20 and seen["crisis_averted"] > 0


# ---------- a válság fajtái és a kereskedőhajó ----------

def test_crisis_kinds_differ_and_shield_protects():
    flare = crisis_game(fund=0.0); flare.station.crisis["kind"] = "napkitores"
    q = flare.modules[0].q; rec = flare.step({})
    assert next(x for x in rec["events"] if x["type"] == "crisis_hit")["modules"] == [] and flare.modules[0].q == pytest.approx(q - 0.04)
    assert all(v == 0.5 for v in flare.station.econ.values())                  # két körre megfeleződik a hozam
    reactor = crisis_game(fund=0.0); reactor.station.crisis["kind"] = "reaktor"
    hz = reactor.station.hazard; reactor.step({})
    assert reactor.station.hazard > hz + 0.25 and reactor.modules[0].q < 0.6   # kétszeres ugrás, a gyár megsérül
    shield = crisis_game(fund=0.0); shield.players[0].items["pajzs"] = 1
    q = shield.modules[0].q; rec = shield.step({})
    ev = next(x for x in rec["events"] if x["type"] == "crisis_hit")
    assert ev["shielded"] == [0] and ev["modules"] == [] and shield.players[0].items["pajzs"] == 0
    assert shield.modules[0].q == pytest.approx(q - 0.04)                      # csak a napi kopás


def ship_game(**kw):
    e = midgame(p_ship=0.0, p_crisis=0.0, mission_slots=0, **kw)
    for pl in e.players.values():
        pl.balance = 1000.0
    e.station.ship = {"module_type": "gyar", "announced": 0, "arrives": 1, "leaves": 3, "bonus": 0.35, "prize": 500.0,
                      "earned": {}, "items": [{"id": 0, "kind": "turbo", "reserve": 150.0, "sold_to": None},
                                              {"id": 1, "kind": "javito", "reserve": 150.0, "sold_to": None}]}
    return e


def test_ship_buys_a_type_holds_an_auction_and_rewards_the_top_producer():
    e = ship_game(); m = e.modules[0]; m.q = 0.6
    base = e.nominal(m) * 0.6 * e.players[0].skill[m.type] * (1 - 0.5 * e.station.hazard)
    obs = e.observe(1)["station"]["ship"]
    assert obs["here"] and obs["auction_open"] and "earned" not in obs
    rec = e.step({0: Decision([BidItem(1, 200.0), BidItem(0, 100.0)]),                    # a második a legkisebb licit alatt
                  1: Decision([BidItem(0, 300.0), BidItem(1, 180.0), BidItem(7, 200.0)]),
                  2: Decision([BidItem(0, 250.0)])})
    assert [r["code"] for r in rec["results"][0]] == ["OK", "BID_BELOW_MIN"]
    assert [r["code"] for r in rec["results"][1]] == ["OK", "OK", "INVALID_TARGET"]
    assert [r.get("outcome") for r in rec["results"][1][:2]] == ["won", "outbid"] and rec["results"][2][0]["outcome"] == "outbid"
    assert e.players[1].boost == pytest.approx(0.10) and rec["ledger"]["items"] == 500.0  # 300 + 200 kikerült a gazdaságból
    assert rec["ledger"]["production"] == pytest.approx(base / 0.6 * 1.0 * 1.35)          # a javítókészlet után hibátlan, +35%
    assert e.observe(0)["me"]["ship_earned"] > 0 and e.observe(1)["me"]["ship_earned"] == 0
    bal = e.players[0].balance
    rec = e.step({})                                                                       # a hajó távozik
    ev = next(x for x in rec["events"] if x["type"] == "ship_left")
    assert ev["winner"] == 0 and rec["ledger"]["prize"] == 500.0 and e.station.ship is None
    assert e.players[0].balance > bal + 500
    assert e.step({1: Decision([BidItem(0, 300.0)])})["results"][1][0]["code"] == "NO_SHIP"


def test_ships_and_distinct_crises_occur_in_default_games():
    kinds, ships, sold = set(), 0, 0
    for seed in range(12):
        e = Engine(Params.short(), seed=seed, strict=True)
        run_game(e, heur)
        for r in e.history:
            for ev in r["events"]:
                if ev["type"] == "crisis_announced": kinds.add(ev["kind"])
                ships += ev["type"] == "ship_left"; sold += ev["type"] == "item_sold"
    assert kinds == {"meteor", "reaktor", "napkitores"} and ships > 10 and sold > 5


def test_repair_kit_restores_modules_keeps_them_from_wearing_and_wastes_no_maintenance():
    e = ship_game(); m = e.modules[0]; m.q = 0.5
    rec = e.step({0: Decision([BidItem(1, 200.0), Maintain(0, 500.0)])})         # ugyanabban a körben karbantartást is kér
    assert rec["results"][0][0]["outcome"] == "won" and rec["ledger"]["maintenance"] == 0.0   # a karbantartás nem került semmibe
    assert m.q == 1.0 and e.players[0].items["javito_until"] == 1 + 5              # hibátlan maradt: aznap sem kopott
    for _ in range(4):
        e.step({})
    assert m.q == 1.0                                                              # öt napig véd
    e.step({})
    assert m.q == pytest.approx(1.0 - e.p.types[m.type].wear)                      # utána újra kopik


# ---------- megbízások ----------

def mission_game(kind, **kw):
    e = midgame(p_ship=0.0, p_crisis=0.0, mission_slots=1, mission_interval=1, **kw)
    for pl in e.players.values():
        pl.balance = 3000.0
    e.station.missions = [{"id": 0, "kind": kind, "reward": 300.0, "issued": 0, "expires": 9, "progress": {}}]
    e.station.mission_seq = 1
    return e


def test_mission_upgrade_and_takeover_pay_the_first_to_do_it():
    e = mission_game("fejlesztes")
    assert e.observe(3)["station"]["missions"][0]["kind"] == "fejlesztes"
    rec = e.step({0: Decision([Upgrade(0)])})
    ev = next(x for x in rec["events"] if x["type"] == "mission_done")
    assert ev["winner"] == 0 and rec["ledger"]["prize"] == 300.0
    assert [x for x in rec["events"] if x["type"] == "mission_new"] and e.station.missions[0]["id"] == 1   # jön a következő
    e = mission_game("hodito")
    rec = e.step({1: Decision([Bid(0, 2000.0)])})                                  # a plafon (1500) fölött: elviszi
    assert next(x for x in rec["events"] if x["type"] == "mission_done")["winner"] == 1


def test_state_missions_streaks_and_expiry():
    e = mission_game("harom_tipus")
    for mid, holder in ((0, 2), (6, 2), (16, 2)):                                  # gyár, lakó, üzlet egy kézben
        e.modules[mid].holder = holder
    assert next(x for x in e.step({})["events"] if x["type"] == "mission_done")["winner"] == 2
    e = mission_game("mintagazda")
    e.modules[1].holder = 0; e.modules[1].price = e.modules[1].cap = 1000.0
    for day in range(3):
        for mid in (0, 1): e.modules[mid].q = 1.0
        rec = e.step({})
        assert e.observe(0)["station"]["missions"][0]["my_progress"] == day + 1 if day < 2 else True
    assert next(x for x in rec["events"] if x["type"] == "mission_done")["winner"] == 0
    e = mission_game("negy_modul"); e.station.missions[0]["expires"] = 1
    rec = e.step({})
    assert [x["type"] for x in rec["events"] if x["type"].startswith("mission")] == ["mission_expired", "mission_new"]


def test_missions_run_through_default_games():
    done = expired = 0
    for seed in range(10):
        e = Engine(Params.short(), seed=seed, strict=True)
        run_game(e, heur)
        assert len(e.history[0]["events"]) and any(ev["type"] == "mission_new" for ev in e.history[0]["events"])   # már a nyitókör után van cél
        for r in e.history:
            done += sum(ev["type"] == "mission_done" for ev in r["events"]); expired += sum(ev["type"] == "mission_expired" for ev in r["events"])
    assert done > 20 and expired > 0


def test_unsold_ship_items_stay_on_offer_while_the_ship_is_here():
    e = ship_game()
    rec = e.step({1: Decision([BidItem(0, 300.0)])})                               # az érkezés napján csak az egyik kel el
    assert rec["results"][1][0]["outcome"] == "won"
    ship = e.observe(0)["station"]["ship"]
    assert ship["here"] and ship["auction_open"] and [i["sold_to"] for i in ship["items"]] == [1, None]
    rec = e.step({0: Decision([BidItem(1, 150.0), BidItem(0, 500.0)])})            # másnap a maradék megvehető, az elkelt nem
    assert [r["code"] for r in rec["results"][0]] == ["OK", "INVALID_TARGET"] and rec["results"][0][0]["outcome"] == "won"
    assert e.players[0].items["javito_until"] > 0
