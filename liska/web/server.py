"""Helyi webkiszolgáló az emberi játékhoz. Csak a saját gépről érhető el (127.0.0.1).

A játékot ugyanaz a motor viszi, mint a szimulációt. A böngésző csak az emberi játékos
megfigyelését kapja meg, és ugyanazokat az akciókat küldi, mint bármelyik ügynök.
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from ..agents import HeuristicPolicy, LLMPolicy
from ..agents.llm_agent import _est_income
from ..engine import Engine
from ..llm import LLMGateway
from ..params import Params
from ..runner import dump_log

MIX = ["ovatos", "terjeszkedo", "potyautas"]
KIND = {"ovatos": "óvatos gép", "terjeszkedo": "terjeszkedő gép", "potyautas": "potyautas gép", "vadasz": "vadász gép"}
# az emberi játék ellenfelei: a vadászok az alacsony plafonú modulokra utaznak
WEB_MIX = {1: "terjeszkedo", 2: "potyautas", 3: "ovatos", 4: "vadasz", 5: "terjeszkedo", 6: "ovatos", 7: "vadasz"}
LATE = ["ovatos", "vadasz", "potyautas", "terjeszkedo"]


def profile_of(pid: int) -> str:
    return WEB_MIX.get(pid, LATE[pid % 4])


PERSONAS = {
    1: ("Korall", "terjeszkedő: mindenből többet akar"), 2: ("Lajhár", "potyautas: fizessen helyette más"),
    3: ("Bástya", "óvatos: magas plafon, biztos kéz"), 4: ("Hiéna", "vadász: az olcsón védett modulokra utazik"),
    5: ("Üstökös", "terjeszkedő: gyorsan nő, sokat kockáztat"), 6: ("Remete", "óvatos: kevés modul, jó állapotban"),
    7: ("Sólyom", "vadász: lecsap, ahol alacsony a plafon"), 8: ("Horgony", "óvatos: később érkezett, lassan épít"),
    9: ("Cápa", "vadász: később érkezett, éhesen"), 10: ("Kakukk", "potyautas: más fészkébe ül"),
    11: ("Vihar", "terjeszkedő: később érkezett, mindent visz"),
}
GEMINI_NAMES = ["Orákulum", "Szfinx", "Látnok", "Jós"]

# beszólások: helyzet -> stílus -> mondatok ("*": bármelyik stílus)
QUIPS = {
    "took_from_you": {"vadasz": ["Köszönöm a modult. Legközelebb tedd magasabbra a plafont.", "Olcsón védted, drágán tanulsz."],
                      "terjeszkedo": ["Ez már az enyém. Nálam jobb helyen lesz.", "Kellett a gyűjteménybe."],
                      "*": ["Nem szokásom, de ezt nem hagyhattam ki."]},
    "you_took": {"vadasz": ["Élvezd, amíg lehet. Visszajövök érte."], "*": ["Ezt még visszaszerzem.", "Drágán vetted. Majd meglátjuk, megéri-e."]},
    "held_vs_you": {"ovatos": ["Szép próbálkozás. A plafonom ennél magasabban van.", "Nem eladó. Ennyiért biztosan nem."],
                    "*": ["Ennyiért? Próbálkozz többel.", "Majdnem. De csak majdnem."]},
    "bid_on_yours": {"vadasz": ["Most megtartottad. Jövök még.", "Közel jártam, a plafonod nem volt sokkal feljebb."],
                     "*": ["Legközelebb többet teszek rá."]},
    "freerider": {"potyautas": ["Tudtam, hogy megoldjátok nélkülem.", "Miért fizetnék, ha ti úgyis fizettek?"]},
    "scold_you": {"ovatos": ["Láttuk, ki nem adott a válságra.", "A pajzs nem tölti fel magát. Legközelebb te is adj."],
                  "*": ["Ingyen ebéd, mi?"]},
    "thank_you": {"ovatos": ["Rendes volt tőled, hogy beszálltál.", "Így kell ezt. Köszönjük."], "*": ["Ezúttal megúsztuk."]},
    "crisis_hit": {"ovatos": ["Megmondtam, hogy kevés lesz. Ki spórolt?"], "vadasz": ["A sérült modul olcsó modul. Köszönöm."],
                   "potyautas": ["Hát, ez nem jött össze. Kár."], "*": ["Ez fájt. Legközelebb adjatok többet."]},
    "won_item": {"vadasz": ["Ez kellett nekem. Most figyeljetek."], "*": ["Jó vétel volt.", "Megérte az árát."]},
    "won_prize": {"*": ["A jutalom jó helyre került.", "Termelni tudni kell."]},
    "bankrupt": {"vadasz": ["Egy vetélytárssal kevesebb. A moduljai szabadok."], "*": ["Túl nagyot harapott."]},
    "won_mission": {"vadasz": ["Megbízás kipipálva. Ki a következő?"], "ovatos": ["Lassan, de biztosan. A jutalom az enyém."],
                    "*": ["Ezt is én vittem el.", "Gyorsabb voltam."]},
}
HUMAN = 0
INDEX = Path(__file__).with_name("index.html")
WEB = Path(__file__).parent.resolve()
STATIC = {".js": "text/javascript; charset=utf-8", ".glb": "model/gltf-binary", ".jpg": "image/jpeg"}


class GameSession:
    def __init__(self, seed: Optional[int] = None, gemini: int = 0, short: bool = True,
                 gateway: Optional[LLMGateway] = None, save_log: bool = True):
        self.seed = int(seed) if seed is not None else random.SystemRandom().randrange(1, 1_000_000)
        self.params = Params.short() if short else Params()
        self.horizon = 30 if short else 60
        self.engine = Engine(self.params, seed=self.seed)
        self.gemini = max(0, min(int(gemini), self.params.n_players - 1))
        self.gateway = gateway
        if self.gemini and self.gateway is None:
            self.gateway = LLMGateway()
        self.policies: dict = {}
        self.save_log = save_log
        self.log_file: Optional[str] = None
        self.history = [self._point()]
        self.last_chatter: list = []
        self.said: dict = {}                       # a Gemini-játékosok legutóbb kimondott indoklása

    def _point(self) -> dict:
        st = self.engine.station
        return {"day": st.day, "balance": self.engine.players[HUMAN].balance,
                "hazard": st.hazard, "d": st.d, "fund": st.fund}

    def kind(self, pid: int) -> str:
        if pid == HUMAN:
            return "emberi játékos"
        if 1 <= pid <= self.gemini:
            return "Gemini"
        return KIND[profile_of(pid)]

    def names(self) -> dict:
        out = {}
        for pid in sorted(self.engine.players):
            if pid == HUMAN:
                out[str(pid)] = {"name": "Te", "style": "emberi játékos"}
            elif 1 <= pid <= self.gemini:
                out[str(pid)] = {"name": GEMINI_NAMES[(pid - 1) % len(GEMINI_NAMES)], "style": "Gemini: maga dönt, hetente újragondolja"}
            else:
                name, style = PERSONAS.get(pid, (f"{pid + 1}. játékos", KIND[profile_of(pid)]))
                out[str(pid)] = {"name": name, "style": style}
        return out

    def chatter(self, rec: dict, decisions: dict) -> list:
        """Körönként legfeljebb három beszólás az események alapján; elöl azok, amelyek téged érintenek."""
        rng = random.Random(f"{self.seed}:chat:{rec['day']}")
        gem = lambda pid: 1 <= pid <= self.gemini
        lines = []          # (fontosság, játékos, szöveg)

        def say(prio, pid, kind):
            if pid == HUMAN or pid not in self.engine.players or gem(pid):
                return
            pool = QUIPS[kind].get(profile_of(pid)) or QUIPS[kind].get("*")
            if pool:
                lines.append((prio, pid, rng.choice(pool)))

        for ev in rec["events"]:
            t = ev["type"]
            if t == "takeover":
                if ev["from"] == HUMAN: say(0, ev["to"], "took_from_you")
                elif ev["to"] == HUMAN: say(0, ev["from"], "you_took")
            elif t == "retained":
                if ev["bidder"] == HUMAN: say(1, ev["holder"], "held_vs_you")
                elif ev["holder"] == HUMAN: say(1, ev["bidder"], "bid_on_yours")
            elif t == "crisis_averted":
                paid = {int(k) for k, v in ev["pledged"].items() if v > 0.5}
                short = ev["cost"] - ev["from_fund"]
                if short > 1:
                    bots = [p for p in self.engine.players if p != HUMAN]
                    for p in rng.sample(bots, len(bots)):
                        if p not in paid: say(2, p, "freerider")
                    careful = [p for p in bots if profile_of(p) == "ovatos" and not gem(p)]
                    if careful: say(1 if HUMAN not in paid else 2, rng.choice(careful), "scold_you" if HUMAN not in paid else "thank_you")
            elif t == "crisis_hit":
                bots = [p for p in self.engine.players if p != HUMAN and not gem(p)]
                if bots: say(1, rng.choice(bots), "crisis_hit")
            elif t == "item_sold": say(3, ev["player"], "won_item")
            elif t == "mission_done": say(2, ev["winner"], "won_mission")
            elif t == "ship_left" and ev["winner"] is not None: say(2, ev["winner"], "won_prize")
            elif t == "bankruptcy":
                bots = [p for p in self.engine.players if p not in (HUMAN, ev["player"]) and not gem(p)]
                if bots: say(2, rng.choice(bots), "bankrupt")
        for pid in list(self.engine.players):       # a Gemini-játékos a saját indoklásával szólal meg, ha új
            if gem(pid) and isinstance(decisions.get(pid), object) and hasattr(decisions.get(pid), "rationale"):
                why = decisions[pid].rationale.split("] ", 1)[-1].strip()
                if why and not why.startswith("tartalék") and self.said.get(pid) != why:
                    self.said[pid] = why
                    lines.append((1, pid, why.split(". ")[0].rstrip(".") + "."))
        lines.sort(key=lambda x: x[0])
        seen, out = set(), []
        for _, pid, text in lines:
            if pid in seen: continue
            seen.add(pid); out.append({"player": pid, "text": text})
        return out[:3]

    def policy(self, pid: int):
        if pid not in self.policies:
            profile = profile_of(pid)
            if 1 <= pid <= self.gemini:
                self.policies[pid] = LLMPolicy(self.gateway, "weekly", fallback=profile,
                                               interval=self.params.vote_interval,
                                               horizon=self.horizon)
            else:
                self.policies[pid] = HeuristicPolicy(profile, horizon=self.horizon)
        return self.policies[pid]

    def state(self) -> dict:
        e, p = self.engine, self.params
        obs = e.observe(HUMAN)
        est, upkeep = {}, {}
        for m in obs["modules"]:
            est[m["id"]] = _est_income(obs, m, 0 if m["holder"] == HUMAN else 1)
            upkeep[m["id"]] = p.types[m["type"]].wear * p.mu * m["nominal"]
        result = None
        if e.station.finished:
            res = e.result()
            result = {"collapsed": res["collapsed"], "winner": res["winner"],
                      "ranking": [{**r, "kind": self.kind(r["id"])} for r in res["ranking"]]}
        return {"active": True, "seed": self.seed, "obs": obs, "est": est, "upkeep": upkeep,
                "names": self.names(), "chatter": self.last_chatter,
                "history": self.history, "finished": e.station.finished,
                "collapsed": e.station.collapsed, "result": result,
                "length": [p.end_day_min + 1, p.end_day_max + 1],
                "gemini": self.gemini, "log_file": self.log_file}

    def turn(self, actions) -> dict:
        e = self.engine
        if e.station.finished:
            return self.state()
        if not isinstance(actions, list):
            actions = []
        decisions = {HUMAN: {"actions": actions[:200], "rationale": "ember"}}
        for pid in list(e.players):
            if pid == HUMAN:
                continue
            try:
                decisions[pid] = self.policy(pid).decide(e.observe(pid))
            except Exception as exc:
                decisions[pid] = None
                e.agent_errors.append({"day": e.station.day, "player": pid, "error": repr(exc)[:200]})
        rec = e.step(decisions)
        self.history.append(self._point())
        self.last_chatter = self.chatter(rec, decisions)
        if e.station.finished and self.save_log:
            self.log_file = f"jatek-ui-{self.seed}.jsonl"
            dump_log(e, self.log_file)
        return self.state()


class App:
    def __init__(self):
        self.session: Optional[GameSession] = None
        self.lock = threading.Lock()

    def handle(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        with self.lock:
            if method == "GET" and path == "/api/state":
                return 200, self.session.state() if self.session else {"active": False}
            if method == "POST" and path == "/api/new":
                seed = body.get("seed")
                seed = int(seed) if str(seed or "").strip().lstrip("-").isdigit() else None
                gemini = body.get("gemini", 0)
                gemini = int(gemini) if isinstance(gemini, (int, float)) else 0
                self.session = GameSession(seed=seed, gemini=gemini)
                return 200, self.session.state()
            if method == "POST" and path == "/api/turn":
                if not self.session:
                    return 400, {"error": "Nincs futó játék. Indíts újat."}
                return 200, self.session.turn(body.get("actions"))
        return 404, {"error": "Nincs ilyen cím."}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _api(self, method: str) -> None:
            body = {}
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                try:
                    body = json.loads(self.rfile.read(min(length, 1_000_000)))
                except ValueError:
                    body = {}
            if not isinstance(body, dict):
                body = {}
            try:
                status, data = app.handle(method, self.path.split("?")[0], body)
            except Exception as exc:     # a felület kapjon értelmes hibát, ne szakadjon meg
                status, data = 500, {"error": f"Belső hiba: {exc!r}"[:300]}
            self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif path == "/scene3d.js" or path.startswith(("/vendor/", "/models/")):
                target = (WEB / path.lstrip("/")).resolve()     # csak a web mappán belüli, ismert típusú fájl
                if WEB in target.parents and target.is_file() and target.suffix in STATIC:
                    self._send(200, target.read_bytes(), STATIC[target.suffix])
                else:
                    self._send(404, b"nincs ilyen", "text/plain; charset=utf-8")
            else:
                self._api("GET")

        def do_POST(self):
            self._api("POST")

        def log_message(self, fmt, *args):   # csendes kiszolgáló
            pass

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"A játék itt fut: {url}\nLeállítás: Ctrl+C")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLeállítva.")
    finally:
        server.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Liska-játék v2 – helyi webes felület")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="ne nyissa meg a böngészőt")
    args = ap.parse_args()
    serve(args.port, not args.no_browser)
