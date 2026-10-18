"""Chequeos anti-rug con RugCheck (https://api.rugcheck.xyz).

RugCheck detecta, entre otras cosas: mint authority activa (pueden imprimir tokens),
freeze authority activa (pueden congelar tu wallet = honeypot), liquidez no bloqueada,
concentracion de holders, creador con historial de rugs, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .config import FilterConfig
from .http import RateLimiter, get_json

log = logging.getLogger(__name__)

URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
_limiter = RateLimiter(50)

# Riesgos que descartan el token aunque RugCheck los marque solo como "warn"
ALWAYS_REJECT = ("freeze authority", "mint authority", "copycat", "honeypot")


@dataclass
class SafetyResult:
    ok: bool
    score: float | None = None
    reasons: list[str] = field(default_factory=list)


def evaluate_report(report: dict[str, Any], cfg: FilterConfig) -> SafetyResult:
    reasons = []
    score = report.get("score_normalised")
    if score is not None and score > cfg.rugcheck_max_score_normalised:
        reasons.append(f"riesgo RugCheck {score} > {cfg.rugcheck_max_score_normalised}")
    for risk in report.get("risks") or []:
        name = str(risk.get("name", ""))
        level = str(risk.get("level", "")).lower()
        if level in cfg.rugcheck_reject_levels:
            reasons.append(f"{level}: {name}")
        elif any(k in name.lower() for k in ALWAYS_REJECT):
            reasons.append(f"{level or 'riesgo'}: {name}")
    return SafetyResult(ok=not reasons, score=score, reasons=reasons)


class RugCheck:
    def __init__(self, session: aiohttp.ClientSession, cfg: FilterConfig):
        self.session = session
        self.cfg = cfg
        self._cache: dict[str, SafetyResult] = {}

    async def check(self, mint: str) -> SafetyResult:
        if not self.cfg.rugcheck:
            return SafetyResult(ok=True, reasons=["rugcheck desactivado"])
        if mint in self._cache:
            return self._cache[mint]
        try:
            report = await get_json(self.session, URL.format(mint=mint), limiter=_limiter)
        except RuntimeError as e:
            log.warning("RugCheck no respondio para %s: %s", mint, e)
            # no se cachea: se reintentara en la proxima evaluacion
            return SafetyResult(
                ok=not self.cfg.rugcheck_required, reasons=["RugCheck no disponible"]
            )
        result = evaluate_report(report or {}, self.cfg)
        self._cache[mint] = result
        return result
