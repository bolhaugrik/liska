"""Az LLM-kapu és az LLM-ügynök tesztjei szimulált Gemini-válaszokkal (hálózat nélkül)."""
import json
import random

import pytest

from liska import Engine, Params, run_game
from liska.agents import HeuristicPolicy, LLMPolicy
from liska.llm import LLMError, LLMGateway, extract_json


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def ok(text, t_in=900, t_out=120, think=40):
    body = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": t_in, "candidatesTokenCount": t_out,
                              "thoughtsTokenCount": think}}
    return 200, {}, json.dumps(body).encode()


def err(status, message, delay=None, daily=False):
    details = []
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"})
    if daily:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]})
    body = {"error": {"code": status, "message": message, "status": "RESOURCE_EXHAUSTED"
                      if status == 429 else "ERROR", "details": details}}
    return status, {}, json.dumps(body).encode()


def gateway(responses, **kw):
    """responses: lista (sorban fogy) vagy függvény. A hívásokat a .calls gyűjti."""
    clock, calls = Clock(), []

    def transport(url, headers, body, timeout):
        calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        clock.t += 0.3
        r = responses(calls[-1]) if callable(responses) else responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    gw = LLMGateway(api_key="TITKOS-KULCS", transport=transport, clock=clock.now,
                    sleep=clock.sleep, log=None, **kw)
    gw.calls, gw.fake = calls, clock
    return gw


def counts(gw, word):
    return [line.split()[-1] for line in gw.lines if f" {word} " in line]


# ---------- számozott napló és limit ----------

def test_send_and_recv_show_the_same_running_numbers():
    gw = gateway([ok("{}")] * 3)
    for _ in range(3):
        gw.ask("s", "u", "daily")
    assert counts(gw, "SEND") == ["(1/1)", "(2/2)", "(3/3)"]
    assert counts(gw, "RECV") == ["(1/1)", "(2/2)", "(3/3)"]


def test_local_counter_restarts_after_a_rate_limit_wait_total_keeps_counting():
    gw = gateway(lambda call: ok("{}"), rpm=3)
    for _ in range(7):
        gw.ask("s", "u", "daily")
    assert counts(gw, "SEND") == ["(1/1)", "(2/2)", "(3/3)", "(1/4)", "(2/5)", "(3/6)", "(1/7)"]
    assert len(gw.fake.slept) == 2 and all(55 < s <= 60.1 for s in gw.fake.slept)
    assert sum("VÁR" in line and "számláló újraindul" in line for line in gw.lines) == 2


def test_never_more_than_rpm_requests_in_any_minute():
    gw = gateway(lambda call: ok("{}"), rpm=5)
    times = []
    for _ in range(23):
        gw.ask("s", "u", "x")
        times.append(gw.fake.t)
    for i, t in enumerate(times):
        assert sum(1 for u in times[: i + 1] if t - u < 60.0) <= 5


def test_token_limit_also_throttles():
    gw = gateway(lambda call: ok("{}", t_in=600), rpm=100, tpm=1500)
    for _ in range(4):
        gw.ask("s", "u", "x")
    assert any("tokenkeret" in line for line in gw.lines)


# ---------- hibák ----------

def test_429_logs_full_message_waits_the_advised_delay_and_retries():
    gw = gateway([ok("{}"), err(429, "Quota exceeded for metric X, limit 10", delay=34), ok('{"a": 1}')])
    gw.ask("s", "u", "daily")
    ans = gw.ask("s", "u", "daily")
    assert ans["json"] == {"a": 1}
    assert any("Quota exceeded for metric X, limit 10" in line for line in gw.lines)
    assert gw.fake.slept == [34.0]
    assert counts(gw, "SEND") == ["(1/1)", "(2/2)", "(1/3)"]     # a megállás után újraindul
    assert gw.stats["retries"] == 1 and gw.stats["ok"] == 2


def test_daily_quota_stops_the_gateway_instead_of_waiting_for_hours():
    gw = gateway([err(429, "Daily limit reached", delay=30, daily=True)])
    with pytest.raises(LLMError):
        gw.ask("s", "u", "x")
    assert gw.exhausted and gw.fake.slept == []
    with pytest.raises(LLMError):
        gw.ask("s", "u", "x")
    assert len(gw.calls) == 1                                    # a második már nem megy ki


def test_server_error_and_network_error_are_retried():
    gw = gateway([err(503, "overloaded"), TimeoutError("lassú"), ok('{"ok": true}')])
    assert gw.ask("s", "u", "x")["json"] == {"ok": True}
    assert gw.stats["retries"] == 2 and len(gw.calls) == 3


def test_gives_up_after_max_retries():
    gw = gateway(lambda call: err(503, "overloaded"), max_retries=2)
    with pytest.raises(LLMError):
        gw.ask("s", "u", "x")
    assert len(gw.calls) == 3


def test_client_error_is_not_retried_and_key_never_reaches_the_log():
    gw = gateway([err(400, "Unknown field: temperature")])
    with pytest.raises(LLMError, match="Unknown field"):
        gw.ask("s", "u", "x")
    assert len(gw.calls) == 1
    assert gw.calls[0]["headers"]["x-goog-api-key"] == "TITKOS-KULCS"
    assert "TITKOS-KULCS" not in "\n".join(gw.lines) and "TITKOS-KULCS" not in gw.calls[0]["url"]


def test_missing_key_and_empty_answer_fail_cleanly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        LLMGateway(log=None).ask("s", "u", "x")
    gw = gateway([(200, {}, json.dumps({"candidates": [{"finishReason": "SAFETY"}]}).encode())])
    with pytest.raises(LLMError, match="üres válasz"):
        gw.ask("s", "u", "x")


def test_request_shape_and_json_extraction():
    gw = gateway([ok('```json\n{"x": 2}\n```')])
    assert gw.ask("rendszer", "kérdés", "x")["json"] == {"x": 2}
    call = gw.calls[0]
    assert call["url"].endswith("/models/gemini-3.6-flash:generateContent")
    assert call["body"]["systemInstruction"]["parts"][0]["text"] == "rendszer"
    assert call["body"]["contents"] == [{"role": "user", "parts": [{"text": "kérdés"}]}]
    assert "temperature" not in call["body"]["generationConfig"]     # a 3.6 Flash nem fogadja el
    assert extract_json('Íme: {"a": [1, 2]} remélem jó') == {"a": [1, 2]}
    assert extract_json("nem json") is None


# ---------- ügynök ----------

_calls = [0]


def scripted_model(call):
    """Szimulált modell: hol jó, hol szabálytalan, hol értelmetlen választ ad (minden hetedik hívás szemét)."""
    _calls[0] += 1
    rng = random.Random(_calls[0])
    if _calls[0] % 7 == 3:
        return ok("bocsánat, nem tudom")
    if _calls[0] % 11 == 5:
        return err(503, "overloaded")
    if "arazas" in call["body"]["systemInstruction"]["parts"][0]["text"]:
        return ok(json.dumps({"rationale": "terjeszkedem", "arazas": "közepes", "plafon": "tág",
                              "karbantartas": "magas", "licit": "támadó", "hitel": "bátor",
                              "osztalek": 0.25}))
    acts = [{"type": "Bid", "module": rng.randrange(0, 26), "amount": rng.choice([500, 1500, "900", -3])},
            {"type": "Maintain", "module": rng.randrange(0, 24), "amount": 40},
            {"type": "Vote", "choice": "0.5"}, {"type": "Repül"}, "szemét"]
    return ok(json.dumps({"rationale": "próba", "actions": acts}))


@pytest.mark.parametrize("mode", ["daily", "weekly"])
def test_full_game_with_llm_players_never_stops(mode):
    gw = gateway(scripted_model, rpm=10_000, tpm=10 ** 9, max_retries=1)
    cache = {}

    def policy_for(pid):
        return cache.setdefault(pid, LLMPolicy(gw, mode) if pid < 3 else HeuristicPolicy("ovatos"))

    e = Engine(Params(), seed=3, strict=True)
    run_game(e, policy_for)
    assert e.station.finished and not e.agent_errors
    llm = [cache[p] for p in range(3)]
    assert all(p.stats["llm"] > 0 for p in llm) and sum(p.stats["fallback"] for p in llm) > 0
    why = [r["decisions"][p]["rationale"] for r in e.history for p in (0, 1, 2)]
    assert any(w.startswith("tartalék") or "[tartalék" in w for w in why)
    if mode == "weekly":
        assert gw.stats["ok"] <= 3 * (len(e.history) // 7 + 2)       # hetente egy kérdés játékosonként
        assert llm[0].choices["licit"] == "tamado" and llm[0].settings["vote"] == 0.25


def test_daily_answer_is_executed_and_string_numbers_are_coerced():
    e = Engine(Params(), seed=1, strict=True)
    mid = 0
    price = e.opening_price(e.modules[mid])
    gw = gateway([ok(json.dumps({"rationale": "nyitólicit",
                                 "actions": [{"type": "Bid", "module": str(mid), "amount": str(price)}]}))])
    pol = LLMPolicy(gw, "daily")
    rec = e.step({0: pol.decide(e.observe(0))})
    assert e.modules[mid].holder == 0 and rec["decisions"][0]["rationale"] == "nyitólicit"
    prompt = gw.calls[0]["body"]
    assert "SZABÁLYOK" in prompt["systemInstruction"]["parts"][0]["text"]
    assert "GAZDÁTLANOK" in prompt["contents"][0]["parts"][0]["text"]


def test_without_key_llm_player_falls_back_to_heuristic(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pol = LLMPolicy(LLMGateway(log=None), "daily")
    e = Engine(Params(), seed=2, strict=True)
    d = pol.decide(e.observe(0))
    assert d.rationale.startswith("tartalék (LLMError)") and len(d.actions) > 0


def test_weekly_llm_players_share_one_base_whatever_their_fallback_profile():
    answer = json.dumps({"rationale": "x", "arazas": "magas", "plafon": "szoros", "karbantartas": "kozepes",
                         "licit": "ovatos", "hitel": "kozepes", "osztalek": "0.5"})
    e = Engine(Params(), seed=4)
    params = []
    for profile in ("ovatos", "terjeszkedo", "potyautas"):
        pol = LLMPolicy(gateway([ok(answer)]), "weekly", fallback=profile)
        d = pol.decide(e.observe(0))
        params.append(pol.executor().c)
        assert d.rationale.startswith("[arazas=magas, plafon=szoros")     # a választás a naplóba kerül
    assert params[0] == params[1] == params[2] and params[0]["max_modules"] == 6


def test_rules_explain_the_skill_scale():
    from liska.agents.llm_agent import rules_text
    text = rules_text(Params().to_dict())
    assert "0.8 és 1.2 között" in text and "1,00 az átlag" in text


def test_prompt_minimums_are_rounded_up_so_using_them_is_always_valid():
    """Az első teljes Gemini-futás 404 elutasítása mind abból jött, hogy a prompt lefelé is kerekített."""
    from liska.agents.llm_agent import state_text
    e = Engine(Params(), seed=1, strict=True)
    for _ in range(12):
        e.step({pid: HeuristicPolicy(["ovatos", "terjeszkedo", "potyautas"][pid % 3]).decide(e.observe(pid)) for pid in e.players})
    checked = 0
    for pid in list(e.players):
        obs = e.observe(pid)
        own, market = state_text(obs).split("MÁSOK MODULJAI")
        for m in obs["my_modules"]:
            line = next(l for l in own.splitlines() if l.startswith(f"{m['id']} "))
            assert float(line.split("|")[4]) >= obs["options"][m["id"]]["min_price"]; checked += 1
        for m in obs["modules"]:
            if m["holder"] != pid and m["min_bid"] is not None:
                line = next(l for l in market.splitlines() if l.startswith(f"{m['id']} "))
                assert float(line.split("|")[4]) >= m["min_bid"]; checked += 1
    assert checked > 100


def test_daily_agent_carries_its_memo_and_bid_history_to_the_next_day():
    e = Engine(Params(), seed=1, strict=True)
    taken = e.modules[0]
    taken.holder, taken.price, taken.cap = 3, 1000.0, 5000.0          # a 3. játékosé, magas plafonnal
    e.station.day = 1
    answer = lambda amount, memo: ok(json.dumps({"rationale": "licit", "memo": memo,
                                                 "actions": [{"type": "Bid", "module": 0, "amount": amount}]}))
    gw = gateway([answer(1100, "A 0-s modul plafonja magas, próbáljak többet vagy mást."), answer(1300, "x")])
    pol = LLMPolicy(gw, "daily")
    e.step({0: pol.decide(e.observe(0))})                              # a licitet megtartással verik vissza
    first = gw.calls[0]["body"]["contents"][0]["parts"][0]["text"]
    assert "TEGNAPI JEGYZETED" not in first and "Gazdátlan modulok" in first
    pol.decide(e.observe(0))
    second = gw.calls[1]["body"]["contents"][0]["parts"][0]["text"]
    assert "TEGNAPI JEGYZETED MAGADNAK: A 0-s modul plafonja magas" in second
    assert "1 licit, ebből 0 nyert, 1 esetben a birtokos megtartotta" in second
    assert "0. modul 1100" in second
    assert "memo" in gw.calls[0]["body"]["systemInstruction"]["parts"][0]["text"]


def test_rules_text_follows_the_rent_base():
    from liska.agents.llm_agent import rules_text
    new, old = rules_text(Params().to_dict()), rules_text(Params(rent_base="price").to_dict())
    assert "a PLAFON 1.5%-át fizeted" in new and "a PLAFON" not in old
    assert "az ár 1.5%-át fizeti járadékként" in old
