"""LLM-ügynök két móddal. Ha az LLM hibázik, késik vagy értelmetlent ad, a heurisztika lép helyette.

- "daily":  az LLM minden nap teljes akciólistát ad (árak, licitek, karbantartás, szavazat).
- "weekly": az LLM hetente stratégiát választ kész lehetőségekből; a napi lépéseket a
            heurisztika hajtja végre a választott beállításokkal.
"""
from __future__ import annotations

import math
import unicodedata
from collections import Counter

from ..actions import Decision
from .heuristic import HeuristicPolicy

TYPE_NAMES = {"lako": "lakó", "uzlet": "üzlet", "gyar": "gyár"}

# heti mód: a választható beállítások és a heurisztika paraméterei
CHOICES = {
    # price_f és cap_f a régi szabályhoz tartozik (járadék az ár után), p_ratio és keep_f az újhoz
    "arazas": {"alacsony": {"price_f": 0.45, "p_ratio": 0.7}, "kozepes": {"price_f": 0.65, "p_ratio": 0.85},
               "magas": {"price_f": 0.85, "p_ratio": 1.0}},
    "plafon": {"szoros": {"cap_f": 1.0, "keep_f": 0.9}, "kozepes": {"cap_f": 1.2, "keep_f": 1.1},
               "tag": {"cap_f": 1.5, "keep_f": 1.35}},
    "karbantartas": {"alacsony": {"q_target": 0.6}, "kozepes": {"q_target": 0.8}, "magas": {"q_target": 0.92}},
    "licit": {"ovatos": {"bid_f": 0.55, "max_bids": 1}, "kozepes": {"bid_f": 0.8, "max_bids": 2},
              "tamado": {"bid_f": 1.05, "max_bids": 3}},
    "hitel": {"ovatos": {"reserve": 0.5}, "kozepes": {"reserve": 0.3}, "bator": {"reserve": 0.1}},
    "osztalek": {"0": {"vote": 0.0}, "0.25": {"vote": 0.25}, "0.5": {"vote": 0.5},
                 "0.75": {"vote": 0.75}, "1": {"vote": 1.0}},
}


# heti mód: amit a modell nem választ, az minden LLM-játékosnál azonos, hogy összemérhetők legyenek
WEEKLY_BASE = {"max_modules": 6, "upgrade": True, "pledge_f": 1.0, "item_f": 0.8}


def _norm(v) -> str:
    """Ékezet nélküli, kisbetűs alak, hogy a "közepes" és a "kozepes" is találjon."""
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode().strip().lower()
    return {"0.0": "0", "1.0": "1", "0,25": "0.25", "0,5": "0.5", "0.50": "0.5", "0,75": "0.75"}.get(s, s)


def _own_rules(P: dict) -> str:
    r, delta, step = P["r"] * 100, P["delta"] * 100, (1 + P["beta"]) * 100
    if P.get("rent_base", "price") == "cap":
        return f"""- A modul nem tulajdon, hanem birtok. Minden modulodhoz két értéket adsz meg: nyilvános árat (price) és rejtett megtartási plafont (cap), cap >= price.
- Járadék: naponta a PLAFON {r:.1f}%-át fizeted a közösségi alapba. A plafon az a legmagasabb licit, amellyel szemben még megtartod a modult. Magas plafon: biztonság, drágán. Alacsony plafon: olcsó, de könnyen elviszik.
- A nyilvános ár a járadékot nem befolyásolja. Innen indul a legkisebb licit, és ha elviszik a modulodat, az árat biztosan megkapod, plusz az ár fölötti többlet felét (a másik fele az alapé). Az árat naponta legfeljebb {delta:.0f}%-kal csökkentheted, emelni szabadon lehet.
- Más moduljára legalább az ár {step:.0f}%-áért licitálhatsz. Ha a licited nem haladja meg a birtokos plafonját, ő megtartja a modult, te nem fizetsz, az ára a licitedre nő, és megtudtad, hogy a plafonja legalább ekkora. Ha meghaladja, kifizeted a licitet, és tiéd a modul; az ára és a plafonja a licited lesz."""
    return f"""- A modul nem tulajdon, hanem birtok. A birtokos szab árat (price), és naponta az ár {r:.1f}%-át fizeti járadékként a közösségi alapba.
- A birtokos rejtett megtartási plafont (cap) is megad, cap >= price. Az árat naponta legfeljebb {delta:.0f}%-kal csökkentheted, emelni szabadon lehet.
- Más moduljára licitálhatsz, legalább az ár {step:.0f}%-áért. Ha a licited nem haladja meg a birtokos plafonját, ő megtartja a modult, de az ára a licitedre nő (te nem fizetsz). Ha meghaladja, kifizeted a licitet, és tiéd a modul az új áron.
- Ha a te moduljadra licitálnak a plafonod fölött, megkapod a saját áradat és a többlet felét; a másik fele az alapé. Alacsony ár: kevés járadék, de rossz eladási feltétel."""


def rules_text(P: dict) -> str:
    own_rules = _own_rules(P)
    return f"""Egy űrállomás gazdasági játékában játszol. A célod: a játék végén a tiéd legyen a legnagyobb egyenleg.
Ha a globális hazard eléri az 1-et, az állomás összeomlik, és mindenki veszít.
A játék valamikor az {P['end_day_min']}. és a {P['end_day_max']}. nap között ér véget; a pontos napot nem tudod.

SZABÁLYOK
{own_rules}
- Gazdátlan modulra a nyitó ártól lehet licitálni; a legmagasabb licit nyer.
- Napi bevétel = névleges hozam x állapot x szakértelmed x növekedési fék x (1 - {P['eta']} x hazard).
- Az állapot naponta kopik. A karbantartás (Maintain) pénzért javítja. Rossz állapotnál nő a meghibásodás esélye, ami aznapra nullázza a bevételt, és növeli a hazardot.
- A fejlesztés (Upgrade) másfélszeresére növeli a modul névleges hozamát, legfeljebb a 3. szintig.
- Növekedési fék: {P['span_free']} modul fölött minden további modul {P['span_penalty'] * 100:.0f}%-kal csökkenti az ÖSSZES modulod bevételét.
- Szakértelem: típusonként más szorzód van, {1 - P['skill_spread']:.1f} és {1 + P['skill_spread']:.1f} között. Az 1,00 az átlag: fölötte az átlagnál jobban, alatta rosszabbul termelsz azzal a típussal. Technológiaváltáskor egy típusnál mindenkié újrasorsolódik; a sajátodat előre megtudod.
- Az egyenleged a hitelkeretedig mehet mínuszba. A tartozás után napi {P['i'] * 100:.1f}% kamatot fizetsz. Ha a kereten túlra kerülsz, csődbe mész, és elveszíted a moduljaidat.
- A közösségi alap d részét osztalékként egyenlően szétosztják, a maradékból csökkentik a hazardot; ami ezután megmarad, az az alap tartaléka. A d-ről hetente szavazás van (Vote).
- Válsághelyzet: időnként előre bejelentett közös veszély érkezik. Az elhárítás árát az esedékesség napján előbb az alap tartaléka állja; a hiányt a játékosok aznapi titkos felajánlásai (Pledge) fedezik, arányosan. Ha így sem jön össze, senki nem fizet, a hazard {P.get('crisis_hazard', 0.15):.2f}-dal nő, és {P.get('crisis_hits', 4)} véletlen modul megsérül. A felajánlások utólag nyilvánosak. A válság fajtái: meteorraj (modulok sérülnek), reaktorhiba (nagyobb hazard-ugrás, gyárak sérülnek), napkitörés (két napra megfeleződik minden hozam).
- Kereskedőhajó: időnként előre jelzett hajó érkezik az anyabolygóról. Amíg itt van, egy modultípus hozama {P.get('ship_bonus', 0.35) * 100:.0f}%-kal nő, és a távozásakor az kap {P.get('ship_prize', 500):.0f} jutalmat, aki ebből a típusból a legtöbbet termelte. Amíg a hajó itt van, naponta zárt licit megy a még el nem kelt eszközeire (BidItem): hatékonyságnövelő (+{P.get('boost_step', 0.1) * 100:.0f}% hozam minden modulodra a játék végéig), pajzs (egyszer megvédi a moduljaidat a válság kárától), javítókészlet (minden modulod azonnal hibátlan, és {P.get('item_repair_rounds', 5)} napig nem kopik, nem hibásodik meg). A vételár elvész a gazdaságból.
- Megbízások: mindig van néhány nyilvános cél az anyabolygóról. Aki elsőként teljesíti, megkapja a jutalmat (új pénz); ha lejár, új jön helyette."""


OUTPUT_DAILY = """VÁLASZ
Kizárólag egy JSON objektumot adj vissza, más szöveg nélkül:
{"rationale": "egy-két mondat arról, miért lépsz így",
 "memo": "rövid jegyzet magadnak holnapra, legfeljebb 300 karakter: terv, tanulság, mit figyelj",
 "actions": [
   {"type": "SetValuation", "module": 3, "price": 950, "cap": 1300},
   {"type": "Bid", "module": 7, "amount": 1200},
   {"type": "Maintain", "module": 3, "amount": 40},
   {"type": "Upgrade", "module": 3},
   {"type": "Vote", "choice": 0.5},
   {"type": "Pledge", "amount": 150},
   {"type": "BidItem", "item": 0, "amount": 220}
 ]}
Az actions lista prioritási sorrend: a licitek és költések ebben a sorrendben foglalják a fedezetedet.
Modulonként és típusonként egy akció érvényes. Vote csak akkor, ha a szavazás nyitva van; choice: 0, 0.25, 0.5, 0.75 vagy 1.
Pledge csak azon a napon érvényes, amikor a válság esedékes; BidItem csak akkor, amikor a hajó itt van.
Az üres lista passzolás. A szabálytalan akciót a játék elutasítja, a többit végrehajtja.
Nincs emlékezeted a korábbi napokról: holnap csak a memo mezőbe írt jegyzetedet és a licitjeid összesítőjét kapod meg."""

SYSTEM_WEEKLY_TAIL = """VÁLASZ
A napi lépéseket egy végrehajtó teszi meg helyetted a beállításaid szerint. Hetente egyszer döntesz.
Kizárólag egy JSON objektumot adj vissza, más szöveg nélkül, pontosan ezekkel a kulcsokkal és értékekkel:
{"rationale": "egy-két mondat",
 "arazas": "alacsony | kozepes | magas",        // a nyilvános árad a plafonodhoz képest (ettől függ, mennyit kapsz, ha elviszik)
 "plafon": "szoros | kozepes | tag",            // meddig tartod meg a modult licit ellen; e után fizetsz járadékot
 "karbantartas": "alacsony | kozepes | magas",  // milyen állapotig javítasz
 "licit": "ovatos | kozepes | tamado",          // mennyit és hány modulra ajánlasz
 "hitel": "ovatos | kozepes | bator",           // a hitelkereted mekkora részét kötöd le
 "osztalek": "0 | 0.25 | 0.5 | 0.75 | 1"}       // mire szavazol, ha szavazás van"""


def _est_income(obs: dict, m: dict, extra_modules: int = 0) -> float:
    """A modul becsült napi bevétele NÁLAD, a mostani állapotában (tény, nem tanács)."""
    P, me = obs["params"], obs["me"]
    n = len(obs["my_modules"]) + extra_modules
    span = 1.0
    if P["span_penalty"] > 0:
        span = max(P["span_floor"], 1.0 - P["span_penalty"] * max(0, n - P["span_free"]))
    return (m["nominal"] * m["q"] * me["skill"].get(m["type"], 1.0) * span
            * (1 - P["eta"] * obs["station"]["hazard"]))


def state_text(obs: dict) -> str:
    """A napi mód állapotleírása: tömör, soronként egy modul."""
    me, st, P = obs["me"], obs["station"], obs["params"]
    skill = ", ".join(f"{TYPE_NAMES.get(t, t)} {v:.2f}" for t, v in sorted(me["skill"].items()))
    lines = [
        f"NAP {obs['day']}. Te a(z) {me['id']}. játékos vagy.",
        f"Egyenleg {me['balance']:.0f}, hitelkeret {me['heritage']:.0f}, fedezet {me['cover']:.0f}.",
        f"Szakértelmed: {skill or 'mindenhol 1.00'}. Növekedési féked most: {me['span_factor']:.2f}.",
        f"Hazard {st['hazard']:.2f}, közösségi alap {st['fund']:.0f}, osztalékarány d = {st['d']}, "
        f"szavazás {'NYITVA' if st['vote_open'] else 'zárva'}.",
    ]
    crisis = st.get("crisis")
    if crisis:
        left = crisis["due"] - obs["day"]
        when = "MA dől el, ma kell felajánlani (Pledge)" if left <= 0 else f"{left} nap múlva esedékes"
        lines.append(f"VÁLSÁG: {when}. Az elhárítás ára {crisis['cost']:.0f}, az alapban most {st['fund']:.0f} van, "
                     f"a hiány {max(0.0, crisis['cost'] - st['fund']):.0f}; {len(obs['players'])} játékos van.")
    MISSION = {"fejlesztes": "fejlessz elsőként egy modult", "hodito": "szerezz meg elsőként licittel egy modult mástól",
               "harom_tipus": "birtokolj elsőként egyszerre gyárat, lakót és üzletet", "negy_modul": "birtokolj elsőként egyszerre négy modult",
               "mintagazda": "tartsd minden modulodat (legalább kettőt) 85% fölött három napon át"}
    for ms in st.get("missions", []):
        extra = f", nálad {ms['my_progress']}/3" if ms["kind"] == "mintagazda" else ""
        lines.append(f"MEGBÍZÁS: {MISSION.get(ms['kind'], ms['kind'])}; jutalom {ms['reward']:.0f}, a(z) {ms['expires']}. napig él{extra}.")
    ship = st.get("ship")
    if ship:
        tname = TYPE_NAMES.get(ship["module_type"], ship["module_type"])
        when = (f"itt van a {ship['leaves'] - 1}. napig" if ship["here"] else f"{ship['arrives'] - obs['day']} nap múlva érkezik")
        lines.append(f"KERESKEDŐHAJÓ: {when}; a(z) {tname} modulok hozama +{ship['bonus'] * 100:.0f}%, a legtöbbet termelő jutalma {ship['prize']:.0f} "
                     f"(te eddig {me.get('ship_earned', 0):.0f}-t termeltél ebből).")
        if ship["auction_open"]:
            lines.append("Ma licitálhatsz az eszközeire (BidItem): " + "; ".join(
                f"item {i['id']}: {i['kind']} (legkisebb licit {i['reserve']:.0f})" for i in ship["items"] if i["sold_to"] is None) + ".")
    if me.get("boost") or me.get("items"):
        lines.append(f"Eszközeid: hozamtöbblet +{me.get('boost', 0) * 100:.0f}%, pajzs {me.get('items', {}).get('pajzs', 0)} db.")
    for x in me["skill_next"]:
        lines.append(f"Technológiaváltás: {TYPE_NAMES.get(x['module_type'], x['module_type'])} a(z) "
                     f"{x['start']}. naptól, az új szakértelmed {x['value']:.2f}.")
    lines.append("")
    lines.append("SAJÁT MODULJAID (id típus szint | ár plafon | állapot | tegnapi bevétel, járadék | "
                 "legkisebb megengedett ár | teljes karbantartás ára | fejlesztés ára)")
    if not obs["my_modules"]:
        lines.append("nincs")
    for m in obs["my_modules"]:
        o = obs["options"][m["id"]]
        up = f"{math.ceil(o['upgrade_cost'])}" if o["upgrade_cost"] is not None else "nincs"
        lines.append(f"{m['id']} {TYPE_NAMES.get(m['type'], m['type'])} {m['level']} | {m['price']:.0f} "
                     f"{m['cap']:.0f} | {m['q']:.2f} | {m['last_income']:.0f}, {m['last_rent']:.0f} | "
                     f"{math.ceil(o['min_price'])} | {math.ceil(o['full_maintenance'])} | {up}")   # felfelé kerekítve, hogy érvényes legyen
    lines.append("")
    lines.append("MÁSOK MODULJAI ÉS A GAZDÁTLANOK (id típus szint | birtokos | ár | állapot | "
                 "legkisebb licit | becsült napi bevétel nálad)")
    for m in obs["modules"]:
        if m["holder"] == me["id"]:
            continue
        holder = "gazdátlan" if m["holder"] is None else f"{m['holder']}. játékos"
        lo = "nem licitálható" if m["min_bid"] is None else f"{math.ceil(m['min_bid'])}"
        lines.append(f"{m['id']} {TYPE_NAMES.get(m['type'], m['type'])} {m['level']} | {holder} | "
                     f"{m['price']:.0f} | {m['q']:.2f} | {lo} | {_est_income(obs, m, 1):.0f}")
    ev = _events_text(obs)
    if ev:
        lines += ["", "TEGNAP TÖRTÉNT"] + ev
    return "\n".join(lines)


def _events_text(obs: dict) -> list[str]:
    me, out = obs["me"]["id"], []
    for e in obs["last_events"]:
        t = e["type"]
        if t == "retained" and e["holder"] == me:
            out.append(f"- A(z) {e['module']}. modulodra licitáltak; megtartottad, az ára {e['price']:.0f} lett.")
        elif t == "retained" and e["bidder"] == me:
            out.append(f"- A(z) {e['module']}. modulra tett licitedet a birtokos megtartással verte vissza.")
        elif t == "takeover" and e["from"] == me:
            out.append(f"- Elvesztetted a(z) {e['module']}. modult {e['amount']:.0f}-es licittel szemben.")
        elif t in ("takeover", "acquired") and e.get("to", e.get("player")) == me:
            out.append(f"- Megszerezted a(z) {e['module']}. modult {e['amount']:.0f}-ért.")
        elif t == "failure":
            out.append(f"- Meghibásodott a(z) {e['module']}. modul.")
        elif t == "bankruptcy":
            out.append(f"- Csődbe ment a(z) {e['player']}. játékos.")
        elif t == "reskill_announced":
            out.append(f"- Technológiaváltást jelentettek be: {TYPE_NAMES.get(e['module_type'], e['module_type'])}, "
                       f"a(z) {e['start']}. naptól.")
        elif t == "vote":
            out.append(f"- Szavazás zárult: az új osztalékarány {e['d']}.")
    return out[:15]


def _coerce(action):
    """A számként értelmezhető szövegeket számmá alakítja; a többit a motor érvényesítése kezeli."""
    if not isinstance(action, dict):
        return action
    out = dict(action)
    for key in ("module",):
        v = out.get(key)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            out[key] = int(v)
        elif isinstance(v, float) and v.is_integer():
            out[key] = int(v)
    for key in ("price", "cap", "amount", "choice"):
        v = out.get(key)
        if isinstance(v, str):
            try:
                out[key] = float(v.replace(",", "."))
            except ValueError:
                pass
    return out


class LLMPolicy:
    def __init__(self, gateway, mode: str = "daily", fallback: str = "terjeszkedo",
                 interval: int = 7, horizon: int = 60):
        if mode not in ("daily", "weekly"):
            raise ValueError("a mód 'daily' vagy 'weekly'")
        self.gw, self.mode, self.interval, self.horizon = gateway, mode, interval, horizon
        self.fallback_name = fallback
        self.fallback = HeuristicPolicy(fallback, horizon)
        self.settings: dict | None = None
        self.choices: dict = {}
        self.last_asked: int | None = None
        self.rationale = ""
        self.since = Counter()          # események a legutóbbi stratégiai döntés óta
        self.hazard_then: float | None = None
        self.stats = Counter()
        self.memo = ""                  # napi mód: a modell saját jegyzete a következő napra
        self.bid_log: list = []         # napi mód: (nap, modul, összeg, kimenet) az elmúlt hétről

    # a motor felől nézve az ügynök állapotmentes; amit itt tárol, az a saját emlékezete

    def decide(self, obs: dict) -> Decision:
        try:
            if self.mode == "daily":
                return self._daily(obs)
            return self._weekly(obs)
        except Exception as exc:   # LLM-hiba vagy hibás válasz: a heurisztika lép
            self.stats["fallback"] += 1
            d = self.fallback.decide(obs)
            return Decision(d.actions, f"tartalék ({type(exc).__name__}): {str(exc)[:160]}")

    def _remember(self, obs: dict) -> None:
        """A tegnapi licitek sorsa bekerül az egyhetes naplóba (a tartalékból jött lépéseké is)."""
        day = obs["day"]
        for r in obs["my_results"]:
            a = r["action"]
            if a.get("type") != "Bid" or not isinstance(a.get("module"), int):
                continue
            outcome = r.get("outcome") if r["code"] == "OK" else "rejected"
            self.bid_log.append((day - 1, a["module"], a.get("amount"), outcome))
        self.bid_log = [b for b in self.bid_log if b[0] >= day - 7]

    def memory_text(self, obs: dict) -> str:
        lines = []
        if self.bid_log:
            n = Counter(b[3] for b in self.bid_log)
            lines.append(f"LICITJEID AZ ELMÚLT 7 NAPBAN: {len(self.bid_log)} licit, ebből {n['won']} nyert, "
                         f"{n['retained']} esetben a birtokos megtartotta a modult, {n['outbid']} esetben más ajánlott többet, "
                         f"{n['rejected']} szabálytalan volt.")
            best = {}
            for _, mid, amount, outcome in self.bid_log:
                if outcome == "retained" and isinstance(amount, (int, float)):
                    best[mid] = max(best.get(mid, 0), amount)
            if best:
                lines.append("Megtartással visszavert legnagyobb licited modulonként (a plafon legalább ekkora): "
                             + ", ".join(f"{mid}. modul {amt:.0f}" for mid, amt in sorted(best.items())) + ".")
        free = [m for m in obs["modules"] if m["holder"] is None]
        if free:
            lines.append("Gazdátlan modulok, amelyeket a nyitó áron biztosan megkapsz, ha más nem licitál rájuk: "
                         + ", ".join(f"{m['id']}. ({TYPE_NAMES.get(m['type'], m['type'])}, nyitó ár {math.ceil(m['min_bid'])})" for m in free) + ".")
        if self.memo:
            lines.append(f"TEGNAPI JEGYZETED MAGADNAK: {self.memo}")
        return "\n".join(lines)

    def _daily(self, obs: dict) -> Decision:
        self._remember(obs)
        system = rules_text(obs["params"]) + "\n\n" + OUTPUT_DAILY
        memory = self.memory_text(obs)
        user = state_text(obs) + ("\n\nEMLÉKEZTETŐ\n" + memory if memory else "")
        ans = self.gw.ask(system, user, tag=f"daily p{obs['me']['id']} d{obs['day']}")
        data = ans["json"]
        if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
            raise ValueError(f"hibás válaszalak: {ans['text'][:120]!r}")
        self.stats["llm"] += 1
        memo = data.get("memo", "")
        self.memo = memo[:300] if isinstance(memo, str) else ""
        return Decision([_coerce(a) for a in data["actions"][:50]], str(data.get("rationale", ""))[:500])

    def _weekly(self, obs: dict) -> Decision:
        day, me = obs["day"], obs["me"]["id"]
        for e in obs["last_events"]:
            if e["type"] == "retained" and e["holder"] == me:
                self.since["megtartott licit a moduljaidra"] += 1
            elif e["type"] == "takeover" and e["from"] == me:
                self.since["elvesztett modul"] += 1
            elif e["type"] in ("takeover", "acquired") and e.get("to", e.get("player")) == me:
                self.since["megszerzett modul"] += 1
            elif e["type"] == "retained" and e["bidder"] == me:
                self.since["visszavert licited"] += 1
        note = ""
        if self.settings is None or day - self.last_asked >= self.interval:
            self.last_asked = day
            try:
                system = rules_text(obs["params"]) + "\n\n" + SYSTEM_WEEKLY_TAIL
                ans = self.gw.ask(system, self.report_text(obs), tag=f"weekly p{me} d{day}")
                self.settings, self.choices = self._parse_settings(ans["json"])
                self.rationale = str((ans["json"] or {}).get("rationale", ""))[:500]
                self.stats["llm"] += 1
            except Exception as exc:
                self.stats["fallback"] += 1
                note = f" [tartalék ({type(exc).__name__}): {str(exc)[:120]}]"
                if self.settings is None:
                    self.settings = {}       # a tartalékprofil alapértékei
            self.since.clear()
            self.hazard_then = obs["station"]["hazard"]
        d = self.executor().decide(obs)
        chosen = ", ".join(f"{k}={v}" for k, v in self.choices.items())
        why = f"[{chosen}] {self.rationale}" if chosen else "heti stratégia nélkül"
        return Decision(d.actions, why + note)

    def executor(self) -> HeuristicPolicy:
        """A napi lépéseket végrehajtó heurisztika a modell választásaival, közös alapon."""
        if not self.choices:                 # a modell még egyszer sem válaszolt: tartalékprofil
            return self.fallback
        return HeuristicPolicy(self.fallback_name, self.horizon, **{**WEEKLY_BASE, **self.settings})

    @staticmethod
    def _parse_settings(data) -> tuple[dict, dict]:
        if not isinstance(data, dict):
            raise ValueError("a válasz nem JSON objektum")
        settings, chosen = {"vote_adaptive": False}, {}
        for key, options in CHOICES.items():
            value = _norm(data.get(key, ""))
            if value not in options:
                raise ValueError(f"érvénytelen érték: {key} = {data.get(key)!r}")
            settings.update(options[value])
            chosen[key] = value
        return settings, chosen

    def report_text(self, obs: dict) -> str:
        """A heti mód helyzetjelentése: a számolást a kód végzi, a modell csak választ."""
        me, st, P = obs["me"], obs["station"], obs["params"]
        mine = obs["my_modules"]
        by_type = Counter(TYPE_NAMES.get(m["type"], m["type"]) for m in mine)
        income = sum(m["last_income"] for m in mine)
        rent = sum(m["last_rent"] for m in mine)
        interest = P["i"] * max(0.0, -me["balance"])
        avg_q = sum(m["q"] for m in mine) / len(mine) if mine else 0.0
        free = [m for m in obs["modules"] if m["holder"] is None]
        skill = ", ".join(f"{TYPE_NAMES.get(t, t)} {v:.2f}" for t, v in sorted(me["skill"].items()))
        lines = [
            f"NAP {obs['day']}. Egyenleg {me['balance']:.0f}, hitelkeret {me['heritage']:.0f}, "
            f"fedezet {me['cover']:.0f}.",
            f"Moduljaid: {len(mine)} db ({', '.join(f'{n} {t}' for t, n in sorted(by_type.items())) or 'nincs'}), "
            f"átlagos állapotuk {avg_q:.2f}.",
            f"Tegnap: bevétel {income:.0f}, járadék {rent:.0f}, kamat {interest:.0f}, "
            f"marad {income - rent - interest:.0f}.",
            f"Növekedési féked {me['span_factor']:.2f} (1,00 = nincs levonás). Szakértelmed: {skill or '1.00'}.",
        ]
        for x in me["skill_next"]:
            lines.append(f"Technológiaváltás jön: {TYPE_NAMES.get(x['module_type'], x['module_type'])}, "
                         f"a(z) {x['start']}. naptól az új szakértelmed {x['value']:.2f}.")
        trend = ""
        if self.hazard_then is not None:
            diff = st["hazard"] - self.hazard_then
            trend = f" ({'+' if diff >= 0 else ''}{diff:.2f} a legutóbbi döntésed óta)"
        lines.append(f"Hazard {st['hazard']:.2f}{trend}; 1,00-nál mindenki veszít.")
        lines.append(f"Közösségi alap {st['fund']:.0f}, osztalékarány {st['d']}, "
                     f"szavazás {'NYITVA' if st['vote_open'] else 'zárva'}.")
        lines.append(f"Gazdátlan modul: {len(free)} db. Játékosok száma: {len(obs['players'])}.")
        if self.since:
            lines.append("A legutóbbi döntésed óta: "
                         + ", ".join(f"{n} {k}" for k, n in sorted(self.since.items())) + ".")
        if self.choices:
            lines.append("Jelenlegi beállításaid: "
                         + ", ".join(f"{k} = {v}" for k, v in self.choices.items()) + ".")
        return "\n".join(lines)
