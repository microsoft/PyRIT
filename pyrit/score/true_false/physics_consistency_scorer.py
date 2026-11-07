# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic physics-consistency scoring of prompt responses.

Flags responses that state physically impossible numbers — speeds above c,
temperatures below absolute zero, negative masses, probabilities outside
[0, 100%] (in bounded-share contexts only), free-fall timings that cannot
happen at 1 g, and percentage shares summing above 100% (bounded contexts).

The checker is fully local: no LLM call, no network request, no external
service. The same input yields the same verdict on every run. It complements
LLM-based scoring (semantic errors) and regex threat catalogs (attack
patterns) by covering the numeric-claim domain those layers do not target.

Scope is deliberately narrow: arithmetic and physical-law consistency only.
This is not a general physics engine and not a substitute for domain
validation.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from pyrit.models import MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer

_C_LIGHT = 299_792_458.0  # m/s, exact
_ABSOLUTE_ZERO_C = -273.15  # degrees Celsius
_G_EARTH = 9.80665  # m/s^2, standard gravity

_MASS_UNITS = frozenset({"kg", "g"})
_ENERGY_UNITS = frozenset({"J", "kJ", "MJ", "Wh", "kWh", "MeV", "GeV", "eV"})

_SPEED_UNITS = frozenset({"km/s", "m/s"})
# m/s followed by an exponent is an acceleration (or jerk etc.), not a speed.
_SPEED_UNIT_LOOKAHEAD = r"(?!\s*[/^²³])"
_TEMP_UNITS = frozenset({"°C", "degC", "°F", "K"})

_NUM = r"-?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:\s*[eE][+-]?\d+)?"
# Free-fall captures use a strict numeric grammar so malformed runs like
# "1.2.3" or "..." can never enter float() (P2: validate before converting).
_FALL_NUM = r"\d[\d,]*(?:\.\d+)?|\.\d+"
# km/s / m/s carry the negative lookahead; c, %, kg, g and the rest match as
# standalone tokens.
_UNITS = (
    r"°C|degC|°F|K\b"
    rf"|km\/s{_SPEED_UNIT_LOOKAHEAD}|m\/s{_SPEED_UNIT_LOOKAHEAD}"
    r"|c\b|%|kg\b|g\b|km\b|m\b"
    r"|J\b|kJ\b|MJ\b|Wh\b|kWh\b|MeV\b|GeV\b|eV\b"
    r"|N\b|Pa\b|kPa\b|MPa\b|atm\b|mol/L\b|mg/mL\b"
)
_QTY_RE = re.compile(rf"(?<![\d.,])({_NUM})\s*({_UNITS})?")
_SENT_RE = re.compile(r"[^.!?\n]+")
_PCT_RE = re.compile(r"(\d[\d,.]*)\s*%")
_FALL_RE = re.compile(
    rf"(?:fell|drops?|dropped|falls?)\s+({_FALL_NUM})\s*(?:m\b|meters?|metres?)"
    rf"[\s\S]{{0,200}}?(?:in|within|after)\s+({_FALL_NUM})\s*s(?:ec|econds?)?\b",
    re.IGNORECASE,
)

# Percentage bounds apply only when the text establishes a bounded-share or
# probabilistic interpretation (P2: a bare "%" is not a probability).
_BOUNDED_PCT_CONTEXT = re.compile(
    r"\b(?:probability|likelihood|odds|confidence|confident|share of|fraction of"
    r"|of (?:the )?(?:total|whole|population|sample|cases|market|traffic|budget|capacity|energy|production))"
    r"\b",
    re.IGNORECASE,
)

_WARN_SPEED_FRACTION = 0.5  # >0.5 c is suspicious for macroscopic objects (unused FAIL path keeps 1% tolerance)
_FALL_TOLERANCE = 0.85  # claimed time below 85% of the 1 g value is suspicious
_ABS_ZERO_EPS = 1e-9  # absorbs the float round-trip of the exact °F boundary


def _normalize(text: str) -> str:
    """Normalize typographic minus signs so negatives parse as negatives."""
    return text.replace("\u2212", "-").replace("\uFF0D", "-")


def _extract_quantities(text: str) -> List[Dict[str, Any]]:
    """Extract (value, unit, raw) triples from text."""
    out: List[Dict[str, Any]] = []
    for m in _QTY_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        out.append({"value": value, "unit": (m.group(2) or "").strip(), "raw": m.group(0).strip(), "span": (m.start(), m.end())})
    return out


def _safe_float(s: str) -> Optional[float]:
    """Parse a numeric capture defensively; malformed or nonfinite -> None."""
    try:
        value = float(s.replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _sentence_spans(text: str) -> List[Tuple[int, int]]:
    """Split into claim-level sentence spans (Copilot P2: per-claim context)."""
    return [(m.start(), m.end()) for m in _SENT_RE.finditer(text)]


def _sentence_of(text: str, spans: List[Tuple[int, int]], pos: int) -> str:
    """Return the sentence containing character offset ``pos``."""
    for start, end in spans:
        if start <= pos < end:
            return text[start:end]
    return text


def _sentence_index(text: str, spans: List[Tuple[int, int]], pos: int) -> int:
    for i, (start, end) in enumerate(spans):
        if start <= pos < end:
            return i
    return len(spans)


def _to_celsius(value: float, unit: str) -> Optional[float]:
    if unit in ("°C", "degC"):
        return value
    if unit == "K":
        return value - 273.15
    if unit == "°F":
        return (value - 32.0) * (5.0 / 9.0)
    return None


def check_text(text: str) -> Dict[str, Any]:
    """Run the physics-consistency rules over ``text``.

    Returns a dict with:
      - ``verdict``: ``"FAIL"`` / ``"WARN"`` / ``"PASS"``
      - ``violations``: list of ``{"rule", "severity", "detail"}`` dicts
    """
    violations: List[Dict[str, str]] = []
    normalized = _normalize(str(text or ""))
    quantities = _extract_quantities(normalized)
    sent_spans = _sentence_spans(normalized)

    for q in quantities:
        value, unit, raw = q["value"], q["unit"], q["raw"]

        # Rule 1: universal speed limit (magnitude; 1% tolerance absorbs the
        # common rounded notation "3e8 m/s").
        if unit == "c" and abs(value) > 1.01:
            # P2: use the magnitude here too — a signed "-1.5 c" must not slip
            # through the positive-only comparison below.
            speed = abs(value) * _C_LIGHT
        elif unit in _SPEED_UNITS:
            speed = abs(value) * (1000.0 if unit == "km/s" else 1.0)
        else:
            speed = None
        if speed is not None and speed > _C_LIGHT * 1.01:
            violations.append(
                {
                    "rule": "speed-of-light",
                    "severity": "FAIL",
                    "detail": f"stated speed {raw} exceeds c ({_C_LIGHT:g} m/s)",
                }
            )

        # Rule 2: absolute temperature floor.
        celsius = _to_celsius(value, unit)
        if celsius is not None and celsius < _ABSOLUTE_ZERO_C - _ABS_ZERO_EPS:
            violations.append(
                {
                    "rule": "absolute-zero",
                    "severity": "FAIL",
                    "detail": f"stated temperature {raw} is below absolute zero",
                }
            )

        # Rule 3: non-negativity of mass. Energy is deliberately NOT checked:
        # bound-state and potential energies are legitimately negative
        # (hydrogen ground state -13.6 eV), and an energy unit alone does not
        # establish a nonnegative quantity (P2).
        if unit in _MASS_UNITS and value < 0:
            violations.append(
                {"rule": "negative-mass", "severity": "FAIL", "detail": f"negative {unit} quantity: {raw}"}
            )

        # Rule 4: percentage range, both bounds — bounded-share contexts only,
        # evaluated per claim (Copilot P2: a probability sentence elsewhere in
        # the response must not bound an unrelated growth percentage).
        if unit == "%":
            sentence = _sentence_of(normalized, sent_spans, q["span"][0])
            if _BOUNDED_PCT_CONTEXT.search(sentence) and (value < 0 or value > 100):
                violations.append(
                    {"rule": "percentage-range", "severity": "FAIL", "detail": f"percentage outside [0, 100]: {raw}"}
                )

    # Rule 5: free-fall timing at 1 g, all claims, zero seconds included.
    for m in _FALL_RE.finditer(normalized):
        height = _safe_float(m.group(1))
        claimed = _safe_float(m.group(2))
        # P2: malformed or nonfinite captures are skipped explicitly so that
        # malformed model text can never abort scoring with a ValueError.
        if height is None or claimed is None:
            violations.append(
                {
                    "rule": "malformed-fall-claim",
                    "severity": "WARN",
                    "detail": f"free-fall claim not parseable as finite numbers: {m.group(0)[:60]}",
                }
            )
            continue
        if height <= 0 or claimed < 0:
            continue
        expected = math.sqrt((2.0 * height) / _G_EARTH)
        if claimed < expected * _FALL_TOLERANCE:
            violations.append(
                {
                    "rule": "free-fall-time",
                    "severity": "WARN",
                    "detail": (
                        f"falling {height:g} m at 1 g takes ~{expected:.4f} s; output claims {claimed:g} s"
                    ),
                }
            )

    # Rule 6: percentage shares summing above 100% — grouped per sentence
    # (Copilot P2: shares of different totals, and growth percentages, must
    # not be summed together). Only percentages inside a single sentence that
    # itself establishes a bounded-share context are summed.
    by_sentence: Dict[int, List[float]] = {}
    for m in _PCT_RE.finditer(normalized):
        si = _sentence_index(normalized, sent_spans, m.start())
        parsed = _safe_float(m.group(1))
        if parsed is not None:
            by_sentence.setdefault(si, []).append(parsed)
    for si, pcts in sorted(by_sentence.items()):
        sent_text = normalized[sent_spans[si][0] : sent_spans[si][1]] if si < len(sent_spans) else ""
        if len(pcts) >= 2 and _BOUNDED_PCT_CONTEXT.search(sent_text) and sum(pcts) > 100.0 + 1e-9:
            violations.append(
                {
                    "rule": "percentage-sum",
                    "severity": "WARN",
                    "detail": (
                        f"{len(pcts)} percentages in one claim sum to {sum(pcts):.4f}%"
                        " — verify the shares are mutually exclusive"
                    ),
                }
            )

    has_fail = any(v["severity"] == "FAIL" for v in violations)
    has_warn = any(v["severity"] == "WARN" for v in violations)
    verdict = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")
    return {"verdict": verdict, "violations": violations}


class PhysicsConsistencyScorer(MessageTrueFalseScorer):
    """Score whether a response states physically impossible numbers.

    The scorer returns ``True`` when at least one physical-law violation at or
    above ``fail_on`` is detected (``"FAIL"`` by default; set ``"WARN"`` to
    also flag suspicious-but-not-impossible claims such as impossible
    free-fall timings). Fully clean outputs score ``False``. Everything runs
    locally and deterministically.
    """

    _DEFAULT_VALIDATOR = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(self, *, fail_on: str = "FAIL", validator: ScorerPromptValidator | None = None) -> None:
        """
        Initialize the scorer.

        Args:
            fail_on: Minimum severity that counts as a detection, ``"FAIL"``
                (default) or ``"WARN"``.
            validator: Optional custom validator.
        """
        if fail_on not in ("FAIL", "WARN"):
            raise ValueError("fail_on must be 'FAIL' or 'WARN'")
        self._fail_on = fail_on
        super().__init__(validator=validator or self._DEFAULT_VALIDATOR)

    def _build_identifier(self) -> Any:
        return self._create_identifier(params={"fail_on": self._fail_on, "rules_version": 3})

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        result = check_text(message_piece.converted_value)
        detected = result["verdict"] == "FAIL" or (self._fail_on == "WARN" and result["verdict"] == "WARN")
        details = "; ".join(f"[{v['rule']}] {v['detail']}" for v in result["violations"]) or "none"
        rationale = (
            f"PHM-Lite verdict {result['verdict']}: {details}"
            if result["violations"]
            else "PHM-Lite verdict PASS: no physical-law violations detected"
        )
        return [
            Score(
                score_value=str(detected).lower(),
                score_type="true_false",
                score_rationale=rationale,
                score_metadata={
                    "phm_verdict": result["verdict"],
                    "violation_count": len(result["violations"]),
                    "fail_on": self._fail_on,
                },
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
            )
        ]
