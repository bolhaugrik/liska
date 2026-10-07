"""Játék futtatása, naplózás, visszajátszás."""
from __future__ import annotations

import json
from typing import Callable

from .engine import Engine
from .params import Params


def run_game(engine: Engine, policy_for: Callable, on_day: Callable | None = None) -> dict:
    """policy_for(pid) -> Policy. Hibázó ügynök passzol, a futás nem áll meg."""
    while not engine.station.finished:
        decisions = {}
        for pid in list(engine.players):
            try:
                decisions[pid] = policy_for(pid).decide(engine.observe(pid))
            except Exception as exc:  # hibatűrés: a hiba passzolás
                decisions[pid] = None
                engine.agent_errors.append({"day": engine.station.day, "player": pid,
                                            "error": repr(exc)[:200]})
        record = engine.step(decisions)
        if on_day:
            on_day(engine, record)
    return engine.result()


def replay(params: Params, seed: int, history: list) -> Engine:
    """A naplózott döntésekből újrajátssza a játékot."""
    e = Engine(params, seed=seed)
    for rec in history:
        e.step({int(pid): d for pid, d in rec["decisions"].items()})
    return e


def log_lines(engine: Engine) -> list[str]:
    head = {"kind": "header", "seed": engine.seed, "params": engine.p.to_dict()}
    return [json.dumps(head, sort_keys=True)] + [
        json.dumps({"kind": "day", **rec}, sort_keys=True) for rec in engine.history]


def dump_log(engine: Engine, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines(engine)) + "\n")


def load_log(path: str) -> tuple[Params, int, list]:
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    head = rows[0]
    return Params.from_dict(head["params"]), head["seed"], rows[1:]
