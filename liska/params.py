"""Paraméterek. A jelek a szabályspecifikáció jelöléseit követik."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

REGIMES = ("liska", "private", "fixed")
VOTE_OPTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class ModuleType:
    base_yield: float   # y: napi alaphozam
    wear: float         # w: napi kopás
    fail: float         # pi: hibahajlam
    count: int          # darab a kezdőkészletben


def default_types() -> dict:
    return {
        "lako": ModuleType(40.0, 0.02, 0.15, 10),
        "uzlet": ModuleType(80.0, 0.03, 0.30, 8),
        "gyar": ModuleType(140.0, 0.04, 0.50, 6),
    }


@dataclass(frozen=True)
class Params:
    regime: str = "liska"
    # indulás és játékmenet
    n_players: int = 8
    max_players: int = 12
    entry_interval: int = 10
    heritage: float = 3000.0      # H
    g0: float = 0.2               # induló hazard
    d0: float = 0.5               # induló osztalékarány
    q0: float = 0.9               # modulok induló állapota
    vote_interval: int = 7
    end_day_min: int = 55
    end_day_max: int = 65
    p_event: float = 0.1
    # licit és árazás
    r: float = 0.015              # járadékkulcs / nap
    rent_base: str = "cap"        # a járadék alapja: "cap" (megtartási plafon) vagy "price" (nyilvános ár, a régi szabály)
    i: float = 0.005              # hitelkamat / nap
    delta: float = 0.05           # árcsökkentési korlát / nap
    beta: float = 0.05            # minimális licitlépés
    s: float = 0.5                # régi birtokos része a többletből
    omega: float = 10.0           # nyitó ár szorzója
    p_min: float = 1.0
    tax: float = 0.2              # bevételi adó (csak "private")
    # modulok és hazard
    lam: float = 1.5              # szintszorzó
    mu: float = 4.0               # karbantartási szorzó
    u: float = 6.0                # fejlesztési szorzó
    theta: float = 0.3            # állapotvesztés meghibásodáskor
    eta: float = 0.5              # hazard hatása a hozamra
    alpha: float = 0.05           # elhanyagoltság súlya
    f: float = 0.02               # meghibásodás súlya
    nu: float = 0.005             # természetes csökkenés / nap
    c_g: float = 750.0            # hazard-csökkentés ára modulonként
    g_max: float = 0.02           # napi csökkentési korlát
    max_level: int = 3
    # válsághelyzet: bejelentett közös veszély, amelyet az alap tartaléka és a felajánlások hárítanak el
    p_crisis: float = 0.09        # napi valószínűség, hogy válság érkezik (0: kikapcsolva)
    crisis_lead: int = 3          # ennyi nappal előre jelzi a motor
    crisis_cost: float = 50.0     # az elhárítás ára modulonként
    crisis_hazard: float = 0.15   # ennyivel nő a hazard, ha nem sikerül elhárítani
    crisis_damage: float = 0.4    # ennyit veszít az állapotából egy megsérülő modul
    crisis_hits: int = 4          # ennyi modul sérül meg
    # kereskedőhajó az anyabolygóról: felvásárlás, termelési verseny, eszközök licitre
    p_ship: float = 0.07          # napi valószínűség, hogy hajót jeleznek (0: kikapcsolva)
    ship_lead: int = 3            # ennyi nappal előre jelzi a motor
    ship_stay: int = 5            # ennyi napig marad
    ship_bonus: float = 0.35      # a keresett típus hozama ennyivel nő, amíg a hajó itt van
    ship_prize: float = 500.0     # a keresett típusból a legtöbbet termelő jutalma
    ship_items: int = 2           # ennyi eszközt hoz licitre
    item_reserve: float = 150.0   # egy eszköz legkisebb licitje
    boost_step: float = 0.10      # egy hatékonyságnövelő ennyivel növeli a birtokos hozamát
    item_repair_rounds: int = 5   # a javítókészlet ennyi napig véd a kopástól és a meghibásodástól
    # megbízások az anyabolygóról: nyilvános köztes célok, az első teljesítő jutalmat kap
    mission_slots: int = 2        # ennyi megbízás él egyszerre (0: kikapcsolva)
    mission_days: int = 10        # ennyi nap után lejár
    mission_interval: int = 7     # új megbízás csak ilyen napokon jön az üres helyekre
    mission_reward: float = 250.0 # az alapjutalom; a nehezebb megbízás többet ér
    # szakértelem: játékosonként és típusonként egy rejtett hozamszorzó az [1-s, 1+s] sávból
    skill_spread: float = 0.2
    # előre jelzett piaci fordulat: kikapcsolva (p_shift = 0); a mérés szerint nem indít gazdacserét
    p_shift: float = 0.0          # napi valószínűség, hogy bejelentés érkezik
    shift_lead: int = 3           # ennyi nappal előre jelzi a motor
    shift_duration: int = 10      # ennyi napig tart
    shift_size: float = 0.35      # a típus hozama ennyivel nő vagy csökken
    # előre jelzett technológiaváltás: egy típusnál mindenki szakértelme újrasorsolódik
    p_reskill: float = 0.1        # napi valószínűség (0: kikapcsolva); a shift_lead előrejelzést használja
    # növekedési fék (span_penalty = 0: kikapcsolva)
    span_free: int = 3            # ennyi modulig nincs levonás
    span_penalty: float = 0.10    # e fölött modulonként ennyivel csökken a birtokos összes hozama
    span_floor: float = 0.2       # a szorzó alsó határa
    types: dict = field(default_factory=default_types)

    @classmethod
    def short(cls, **overrides) -> "Params":
        """Rövid, nagyjából 30 körös játék: egy kör két napnyi gazdaságot visz.

        A körönkénti áramlások (hozam, kopás, járadék, kamat, hazard) megduplázódnak, a
        pénzben mért árak (nyitó ár, teljes felújítás, fejlesztés) változatlanok maradnak.
        """
        types = {name: ModuleType(t.base_yield * 2, t.wear * 2, min(1.0, t.fail * 2), t.count)
                 for name, t in default_types().items()}
        base = dict(
            end_day_min=28, end_day_max=32, entry_interval=5, vote_interval=4,
            p_event=0.2, p_reskill=0.2, shift_lead=2,
            p_crisis=0.18, crisis_lead=2, crisis_cost=180.0,
            p_ship=0.14, ship_lead=2, ship_stay=3,
            item_repair_rounds=3, mission_days=6, mission_interval=4,
            r=0.03, i=0.01, delta=0.10,
            omega=5.0, mu=2.0, u=3.0,
            alpha=0.10, nu=0.010, g_max=0.04,
            types=types,
        )
        base.update(overrides)
        return cls(**base)

    def __post_init__(self):
        if self.regime not in REGIMES:
            raise ValueError(f"ismeretlen rend: {self.regime}")
        if self.rent_base not in ("cap", "price"):
            raise ValueError(f"ismeretlen járadékalap: {self.rent_base}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        d = dict(d)
        d["types"] = {k: ModuleType(**v) for k, v in d["types"].items()}
        return cls(**d)

    def public(self) -> dict:
        """A megfigyelésbe kerülő paraméterek (minden nyilvános, a záró nap sávja is)."""
        return self.to_dict()
