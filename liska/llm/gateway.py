"""LLM-kapu a Gemini API-hoz: limitkezelés, újrapróbálás, számozott napló.

A napló számozása (helyi/összes): az első szám az utolsó megállás óta küldött kérések száma,
a második a futás alatt küldött összes kérésé. Megállás minden várakozás: a percenkénti keret
kivárása, a 429 utáni várakozás és a hibák utáni visszalépés. A számláló a küldéskor nő,
ezért a SEND és a hozzá tartozó RECV sor ugyanazt a számpárt mutatja.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from typing import Any, Callable, Optional

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.6-flash"


class LLMError(Exception):
    pass


def http_transport(url: str, headers: dict, body: bytes, timeout: float):
    """(státusz, fejlécek, törzs). A hálózati hiba kivételként megy tovább."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()


def extract_json(text: str) -> Any:
    """JSON kinyerése a válaszból: tisztán, kódkerítésben vagy szöveg közé ágyazva. Hiba: None."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[4:] if s[:4].lower() == "json" else s
    for cand in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        try:
            return json.loads(cand)
        except (ValueError, TypeError):
            continue
    return None


def _error_info(raw: bytes, headers: dict) -> tuple[str, Optional[float], bool]:
    """(teljes üzenet, javasolt várakozás mp-ben, napi keret merült-e ki)."""
    message, delay, daily = raw.decode("utf-8", "replace")[:2000], None, False
    try:
        err = json.loads(raw).get("error", {})
        message = f"{err.get('status', '')} {err.get('message', '')}".strip() or message
        for d in err.get("details", []) or []:
            if "retryDelay" in d:
                delay = float(str(d["retryDelay"]).rstrip("s"))
            for v in d.get("violations", []) or []:
                if "perday" in str(v.get("quotaId", "")).lower():
                    daily = True
    except (ValueError, AttributeError, TypeError):
        pass
    if delay is None:
        for k, v in headers.items():
            if k.lower() == "retry-after":
                try:
                    delay = float(v)
                except ValueError:
                    pass
    return message, delay, daily


class LLMGateway:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None,
                 key_env: str = "GEMINI_API_KEY", base_url: str = DEFAULT_BASE,
                 rpm: int = 10, tpm: int = 250_000, max_retries: int = 4,
                 timeout: float = 90.0, max_wait: float = 120.0,
                 generation_config: Optional[dict] = None,
                 transport: Callable = http_transport,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Optional[Callable[[str], None]] = print,
                 log_path: Optional[str] = None,
                 now: Callable[[], datetime] = datetime.now):
        self.model, self.base_url = model, base_url.rstrip("/")
        self._key = api_key if api_key is not None else os.environ.get(key_env, "")
        self.key_env = key_env
        self.rpm, self.tpm = max(1, int(rpm)), max(1, int(tpm))
        self.max_retries, self.timeout, self.max_wait = max_retries, timeout, max_wait
        self.generation_config = dict(generation_config or {})
        self.transport, self.clock, self.sleep, self.now = transport, clock, sleep, now
        self._log_fn, self.log_path = log, log_path
        self.total = 0          # a futás alatt küldött összes kérés
        self.local = 0          # az utolsó megállás óta küldött kérések
        self.window: deque = deque()   # [időpont, bemeneti token] az utolsó 60 mp-ből
        self.exhausted: Optional[str] = None
        self.lines: list[str] = []
        self.stats = {"sent": 0, "ok": 0, "failed": 0, "retries": 0, "waits": 0,
                      "wait_seconds": 0.0, "tokens_in": 0, "tokens_out": 0, "tokens_think": 0}

    # ---------- napló ----------

    def _log(self, text: str) -> None:
        line = f"[{self.now().strftime('%H:%M:%S.%f')[:-3]}] {text}"
        self.lines.append(line)
        if self._log_fn:
            self._log_fn(line)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _count(self) -> str:
        return f"({self.local}/{self.total})"

    # ---------- limit ----------

    def _prune(self) -> None:
        now = self.clock()
        while self.window and now - self.window[0][0] >= 60.0:
            self.window.popleft()

    def _pause(self, seconds: float, reason: str) -> None:
        """Megállás: várakozás után a helyi számláló újraindul."""
        seconds = max(0.0, seconds)
        self._log(f"VÁR {seconds:.1f}s ({reason}) → a számláló újraindul")
        self.sleep(seconds)
        self.local = 0
        self.stats["waits"] += 1
        self.stats["wait_seconds"] += seconds

    def _throttle(self, est_tokens: int) -> None:
        self._prune()
        while self.window and (len(self.window) >= self.rpm
                               or sum(t for _, t in self.window) + est_tokens > self.tpm):
            why = "kérés" if len(self.window) >= self.rpm else "token"
            wait = 60.0 - (self.clock() - self.window[0][0]) + 0.05
            self._pause(wait, f"percenkénti {why}keret")
            self._prune()

    # ---------- hívás ----------

    def ask(self, system: str, user: str, tag: str = "") -> dict:
        """Egy kérdés, szinkron. Siker: {"text", "json", "usage", "ms"}. Kudarc: LLMError."""
        if self.exhausted:
            raise LLMError(f"a keret kimerült, a kapu leállt: {self.exhausted}")
        if not self._key:
            raise LLMError(f"nincs API-kulcs a(z) {self.key_env} környezeti változóban")
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", **self.generation_config},
        }).encode("utf-8")
        est = (len(system) + len(user)) // 3    # magyar szövegre óvatos becslés; a tényleges szám felülírja
        attempt = 0
        while True:
            self._throttle(est)
            self.total += 1
            self.local += 1
            self.stats["sent"] += 1
            n = self._count()
            self._log(f"SEND → {tag} {n}")
            slot = [self.clock(), est]
            self.window.append(slot)
            t0 = self.clock()
            try:
                status, rhead, raw = self.transport(url, headers, body, self.timeout)
            except Exception as exc:   # időtúllépés, megszakadt kapcsolat
                attempt += 1
                self._log(f"HIBA hálózat {n}: {exc!r}")
                if attempt > self.max_retries:
                    return self._fail(f"hálózati hiba {attempt} próba után: {exc!r}")
                self.stats["retries"] += 1
                self._pause(min(2.0 ** attempt, 30.0), "hálózati hiba")
                continue
            ms = int((self.clock() - t0) * 1000)
            if status == 200:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    return self._fail(f"a 200-as válasz nem JSON {n}")
                usage = payload.get("usageMetadata", {}) or {}
                t_in = int(usage.get("promptTokenCount", 0) or 0)
                t_out = int(usage.get("candidatesTokenCount", 0) or 0)
                t_think = int(usage.get("thoughtsTokenCount", 0) or 0)
                if t_in:
                    slot[1] = t_in
                cands = payload.get("candidates") or []
                parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                if not text.strip():
                    why = (cands[0].get("finishReason") if cands
                           else json.dumps(payload.get("promptFeedback", {}))[:200])
                    return self._fail(f"üres válasz {n}: {why}")
                self.stats["ok"] += 1
                self.stats["tokens_in"] += t_in
                self.stats["tokens_out"] += t_out
                self.stats["tokens_think"] += t_think
                self._log(f"RECV {ms}ms, {t_in} be + {t_out} ki + {t_think} gondolkodás token {n}")
                return {"text": text, "json": extract_json(text), "ms": ms,
                        "usage": {"in": t_in, "out": t_out, "think": t_think}}
            message, delay, daily = _error_info(raw, rhead)
            if status == 429:
                self._log(f"429 {n}: {message}")    # a teljes üzenet
                if daily or (delay is not None and delay > self.max_wait):
                    self.exhausted = message[:300]
                    return self._fail(f"a keret kimerült: {message}")
                attempt += 1
                if attempt > self.max_retries:
                    return self._fail(f"429 {attempt} próba után is: {message}")
                self.stats["retries"] += 1
                self._pause(delay if delay is not None else min(5.0 * 2 ** attempt, 60.0), "429")
                continue
            if status >= 500:
                attempt += 1
                self._log(f"HIBA {status} {n}: {message}")
                if attempt > self.max_retries:
                    return self._fail(f"HTTP {status} {attempt} próba után is: {message}")
                self.stats["retries"] += 1
                self._pause(min(2.0 ** attempt, 30.0), f"HTTP {status}")
                continue
            return self._fail(f"HTTP {status} {n}: {message}")   # 400, 401, 403, 404: nincs újrapróbálás

    def _fail(self, message: str) -> dict:
        self.stats["failed"] += 1
        self._log(f"KUDARC {message}")
        raise LLMError(message)

    def summary(self) -> str:
        s = self.stats
        return (f"LLM-kapu: {s['sent']} kérés, {s['ok']} sikeres, {s['failed']} sikertelen, "
                f"{s['retries']} újrapróbálás, {s['waits']} megállás ({s['wait_seconds']:.0f} mp), "
                f"token: {s['tokens_in']} be, {s['tokens_out']} ki, {s['tokens_think']} gondolkodás")
