"""A teljes játékállapot: állomás, játékos, modul."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Station:
    day: int = 0
    fund: float = 0.0          # F
    hazard: float = 0.0        # G
    d: float = 0.5             # osztalékarány
    econ: dict = field(default_factory=dict)        # típusonkénti gazdasági szorzó
    econ_timer: dict = field(default_factory=dict)  # hány napig él még a szorzó
    shifts: list = field(default_factory=list)      # bejelentett és futó piaci fordulatok
    missions: list = field(default_factory=list)     # az élő megbízások
    mission_seq: int = 0
    ship: Optional[dict] = None                      # a bejelentett vagy itt tartózkodó kereskedőhajó
    crisis: Optional[dict] = None                    # a bejelentett, még el nem dőlt válság
    reskills: list = field(default_factory=list)    # bejelentett technológiaváltások (rejtett értékekkel)
    finished: bool = False
    collapsed: bool = False


@dataclass
class Player:
    id: int
    balance: float = 0.0       # b
    heritage: float = 0.0      # H
    status: str = "active"     # "active" | "bankrupt"
    joined: int = 0
    skill: dict = field(default_factory=dict)
    items: dict = field(default_factory=dict)  # eszközök: {"pajzs": darab}
    boost: float = 0.0                         # hatékonyságnövelőkből származó hozamtöbblet  # szakértelem: típus -> hozamszorzó


@dataclass
class Module:
    id: int
    type: str
    holder: Optional[int] = None
    price: float = 0.0         # P (nyilvános)
    cap: float = 0.0           # K (rejtett)
    q: float = 1.0
    level: int = 1
    last_income: float = 0.0
    last_rent: float = 0.0
