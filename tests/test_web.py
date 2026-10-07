"""A webes felület kiszolgálójának tesztjei (böngésző nélkül)."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from liska.agents import HeuristicPolicy
from liska.params import Params
from liska.web.server import App, GameSession, make_handler


def play(session, policy=None):
    policy = policy or HeuristicPolicy("terjeszkedo", horizon=30)
    turns = 0
    while not session.engine.station.finished:
        d = policy.decide(session.engine.observe(0))
        actions = [{"type": type(a).__name__, **a.__dict__} for a in d.actions]
        state = session.turn(actions)
        turns += 1
    return state, turns


def test_short_preset_is_a_30_round_game_with_doubled_tempo():
    p, full = Params.short(), Params()
    assert (p.end_day_min, p.end_day_max) == (28, 32)
    assert p.r == 2 * full.r and p.types["gyar"].base_yield == 2 * full.types["gyar"].base_yield
    assert p.omega * p.types["gyar"].base_yield == full.omega * full.types["gyar"].base_yield  # a nyitó ár pénzben ugyanaz


def test_human_plays_a_full_game_through_the_session():
    s = GameSession(seed=5, save_log=False)
    state, turns = play(s)
    assert state["finished"] and 29 <= turns <= 33
    assert len(state["history"]) == turns + 1
    ranking = state["result"]["ranking"]
    assert {r["kind"] for r in ranking} >= {"emberi játékos", "óvatos gép", "terjeszkedő gép"}
    assert s.turn([])["finished"]                       # vége után a kör lezárása már nem lép


def test_state_never_shows_other_players_cap_or_balance_during_the_game():
    s = GameSession(seed=6, save_log=False)
    for _ in range(6):
        s.turn([])
    e = s.engine
    other = next(m for m in e.modules.values() if m.holder not in (None, 0))
    other.cap = 87654.321
    e.players[other.holder].balance = 4321.98765
    text = json.dumps(s.state())
    assert "87654.321" not in text and "4321.98765" not in text
    assert s.state()["result"] is None                  # az egyenlegek csak a végén derülnek ki


def test_garbage_from_the_browser_is_a_pass():
    s = GameSession(seed=7, save_log=False)
    for junk in (None, "szemét", [{"type": "Bid"}, 42, {"type": "Bid", "module": 0, "amount": "sok"}]):
        state = s.turn(junk)
    assert state["obs"]["day"] == 3 and not s.engine.agent_errors


def test_http_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                         # a játéknapló ide íródik
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()

    try:
        assert b"Liska" in call("/")
        assert json.loads(call("/api/state")) == {"active": False}
        state = json.loads(call("/api/new", {"seed": 9, "gemini": 0}))
        assert state["seed"] == 9 and state["obs"]["day"] == 0
        mid = state["obs"]["modules"][0]
        state = json.loads(call("/api/turn", {"actions": [{"type": "Bid", "module": mid["id"], "amount": mid["min_bid"]}]}))
        assert state["obs"]["day"] == 1 and state["obs"]["my_results"][0]["code"] == "OK"
        while not state["finished"]:
            state = json.loads(call("/api/turn", {"actions": []}))
        assert (tmp_path / state["log_file"]).exists()
    finally:
        server.shutdown()
        server.server_close()


def test_static_files_for_the_3d_view_are_served_and_nothing_else(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.chdir(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, b""

    try:
        status, ctype, body = get("/scene3d.js")
        assert status == 200 and ctype.startswith("text/javascript") and b"GLTFLoader" in body
        for name in ("gyar", "lako", "uzlet", "kozepe", "hajo"):
            status, ctype, body = get(f"/models/{name}.glb")
            assert status == 200 and ctype == "model/gltf-binary" and body[:4] == b"glTF" and len(body) < 3_000_000
        assert get("/vendor/three/build/three.module.js")[0] == 200
        assert get("/vendor/three/examples/jsm/loaders/GLTFLoader.js")[0] == 200
        status, ctype, body = get("/models/bolygo.jpg")
        assert status == 200 and ctype == "image/jpeg" and body[:2] == b"\xff\xd8"
        assert get("/models/nincs.glb")[0] == 404
        assert get("/vendor/../server.py")[0] == 404              # a web mappán kívülre és más típusra nem lát ki
        assert get("/vendor/%2e%2e/server.py")[0] == 404
        assert get("/models/../index.html")[0] == 404
        page = get("/")[2].decode()
        assert 'src="/scene3d.js"' in page and '"three":"/vendor/three/build/three.module.js"' in page
    finally:
        server.shutdown()
        server.server_close()
