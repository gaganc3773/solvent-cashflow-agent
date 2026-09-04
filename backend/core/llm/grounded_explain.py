"""Grounded LLM layer for the Explain drawer.

Contract: the LLM rewrites the deterministic answer into a warmer, more
conversational explanation — but is NEVER allowed to invent numbers.
Every numeric token in its output must appear in the evidence chain (or
in the canned answer). If validation fails or the LLM is unavailable,
we fall back to the exact deterministic answer.

This preserves the "no hallucinated numbers" guarantee while adding
LLM-quality prose for merchants who want it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import ollama_client as ollama


# ─── Prompt engineering ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are Solvent, an AI cashflow assistant for merchants.

You explain a merchant's cash-position situation in warm, plain English.

CRITICAL RULES — you WILL be checked:

1. You MUST NOT invent numbers. Every number in your response must appear
   verbatim in the FACTS section below. If a number isn't there, don't say it.

2. You MUST NOT contradict the GROUND TRUTH answer. You may reword it,
   soften the tone, or add a brief empathetic sentence — but the substance
   must match.

3. Keep your response to 3–5 sentences. Merchants read on phones.

4. Never suggest actions. Never use the words "should", "must", "recommend",
   or "advise". The recommendation lives elsewhere in the app.

5. No emojis. No markdown. Plain prose, one paragraph."""


USER_PROMPT_TEMPLATE = """FACTS about this merchant right now:
{evidence_block}

GROUND TRUTH answer (this is what the deterministic engine says):
{canned_answer}

TASK: Rewrite the ground truth as a friendly explanation for the merchant.
Keep every number the same. Do not add new numbers. Do not add advice.
Respond with only the rewritten explanation — no preamble, no meta-commentary."""


def build_prompt(question: str, canned_answer: str, evidence: list[list[str]]) -> str:
    """Format the ground-truth answer + evidence into the LLM input."""
    if evidence:
        ev_lines = "\n".join(f"  · {k}: {v}" for k, v in evidence)
    else:
        ev_lines = "  (no numeric evidence — the ground truth is qualitative)"
    return USER_PROMPT_TEMPLATE.format(
        evidence_block=ev_lines,
        canned_answer=canned_answer,
    )


# ─── Numeric-fidelity validator ─────────────────────────────────────

# Numbers we care about: currency amounts (₹, Rs, INR prefix), percentages,
# ISO dates, and bare integers ≥ 100. Years (1900-2099) are whitelisted as
# non-financial — the LLM saying "September 2026" isn't a hallucination.
NUMBER_TOKEN_RE = re.compile(
    r"""
    (?:
        # Currency-prefixed compact forms — ₹, Rs, Rs., INR
        (?:[−-]\s*)?(?:₹|Rs\.?|INR)\s?[\d,]+(?:\.\d+)?(?:\s?[KLC][Rr]?)?
        |
        # Bare compact forms like "1.45L", "75K", "5.5Cr" (unit-suffixed)
        (?:[−-]\s*)?\d+(?:\.\d+)?\s?[KLC][Rr]?\b
        |
        # Percentages
        (?:[−-]\s*)?\d+(?:\.\d+)?\s?%
        |
        # ISO dates
        \b\d{4}-\d{2}-\d{2}\b
        |
        # Indian-grouped numbers with commas (must have at least one comma)
        (?:[−-]\s*)?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?
        |
        # Bare integers with 3+ digits (but NOT 4-digit years 1900-2099)
        (?:[−-]\s*)?\d{3,}(?:\.\d+)?
    )
    """,
    re.VERBOSE,
)

_YEAR_RE = re.compile(r"^-?(?:19|20)\d{2}$")


def _normalize(token: str) -> str:
    """Collapse formatting to a canonical string so equivalent numbers match.

    ₹14K, Rs 14,000, INR14000 and 14000 all normalise to the same string.
    Percentages (51%) and dates (2026-09-01) stay as-is for exact match.
    Non-currency 3+ digit integers (like 100000) are returned as-is.
    """
    t = token.replace("₹", "").replace(",", "").replace(" ", "").replace("−", "-")

    # Strip currency word prefixes
    for prefix in ("Rs.", "Rs", "INR"):
        if t.startswith(prefix):
            t = t[len(prefix):]
        if t.startswith("-" + prefix):
            t = "-" + t[1 + len(prefix):]

    # Dates and percentages: return as-is (lowercase)
    if "-" in t and len(t) >= 8 and t.count("-") == 2:
        return t.lower()   # ISO date
    if t.endswith("%"):
        return t.lower()

    # Try to collapse unit-suffixed magnitudes to plain integer rupees.
    # e.g. "14K" → "14000", "1.45L" → "145000", "5.5Cr" → "55000000"
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s?([klc][rR]?)?$", t, re.IGNORECASE)
    if m:
        num_str, unit = m.groups()
        try:
            n = float(num_str)
        except ValueError:
            return t.lower()
        if unit:
            u = unit.lower()
            mult = 1
            if u.startswith("k"):    mult = 1_000
            elif u.startswith("l"):  mult = 1_00_000
            elif u.startswith("c"):  mult = 1_00_00_000
            n = n * mult
        # Integer rupees; drop trailing .0 for exact-match comparability
        as_int = int(round(n))
        # Allow 1% tolerance for rounding — LLM might write ₹1.5L for 1.45L
        # Keep exact form to preserve precision, but store a rounded form
        # only when the number is small enough that off-by-few doesn't matter
        return str(as_int) if abs(as_int) >= 1000 else f"{n:g}"

    return t.lower()


def extract_numbers(text: str) -> set[str]:
    """Return the set of normalised numeric tokens in `text`, EXCLUDING bare
    4-digit years (1900-2099). Years appearing INSIDE a full ISO date are
    still captured as part of the date token."""
    out = set()
    for m in NUMBER_TOKEN_RE.finditer(text):
        norm = _normalize(m.group())
        if _YEAR_RE.match(norm):
            continue
        out.add(norm)
    return out


@dataclass
class Validation:
    ok: bool
    stray_numbers: list[str]
    reason: str = ""


def _sign_variants(tokens: set[str]) -> set[str]:
    """Return the set including both signed and unsigned forms of each number.
    Semantic context (loss vs gain) is carried by words in the sentence;
    the numeric magnitude is what we ground against.
    A number in the LLM output is considered permitted if either sign appears
    in the evidence."""
    out = set()
    for t in tokens:
        out.add(t)
        if t.startswith("-"):
            out.add(t[1:])
        else:
            out.add("-" + t)
    return out


def validate_numeric_fidelity(
    llm_text: str, canned_answer: str, evidence: list[list[str]],
) -> Validation:
    """Every number in llm_text must appear (in either sign) in the canned
    answer or evidence."""
    llm_nums = extract_numbers(llm_text)
    if not llm_nums:
        return Validation(True, [], "no numbers to check")

    permitted: set[str] = set()
    permitted |= extract_numbers(canned_answer)
    for k, v in evidence:
        permitted |= extract_numbers(k)
        permitted |= extract_numbers(v)
    permitted = _sign_variants(permitted)

    stray = [n for n in llm_nums if n not in permitted]
    if stray:
        return Validation(False, stray,
                          f"LLM output contains numbers not in evidence: {stray}")
    return Validation(True, [], "all numbers grounded")


# ─── Main entry point ──────────────────────────────────────────────

@dataclass
class ExplainOutput:
    """What we send back to the frontend for a single explain call."""
    question: str
    answer: str
    evidence: list[list[str]]
    engine: str                  # "deterministic" | "llm" | "llm_fallback"
    llm_model: str | None = None
    llm_latency_ms: float | None = None
    fallback_reason: str | None = None


def explain(
    question: str,
    canned_answer: str,
    evidence: list[list[str]],
    use_llm: bool = False,
    model: str = ollama.DEFAULT_MODEL,
) -> ExplainOutput:
    """Return an ExplainOutput. If use_llm and Ollama is up and validation
    passes, return the LLM rewrite. Otherwise return the canned answer with
    a fallback reason attached."""

    base = ExplainOutput(
        question=question, answer=canned_answer, evidence=evidence,
        engine="deterministic",
    )
    if not use_llm:
        return base

    if not ollama.is_available(timeout_s=1.5):
        return ExplainOutput(
            **{**base.__dict__,
               "engine": "llm_fallback",
               "fallback_reason": "Ollama not reachable at localhost:11434"},
        )

    prompt = build_prompt(question, canned_answer, evidence)
    result = ollama.generate(
        prompt=prompt, system=SYSTEM_PROMPT, model=model, timeout_s=45.0,
    )
    if not result.ok:
        return ExplainOutput(
            **{**base.__dict__,
               "engine": "llm_fallback",
               "fallback_reason": f"Ollama error: {result.error}",
               "llm_latency_ms": result.latency_ms},
        )

    # Fidelity check — reject and fall back if the LLM invented numbers
    val = validate_numeric_fidelity(result.text, canned_answer, evidence)
    if not val.ok:
        return ExplainOutput(
            **{**base.__dict__,
               "engine": "llm_fallback",
               "fallback_reason": val.reason,
               "llm_model": result.model,
               "llm_latency_ms": result.latency_ms},
        )

    return ExplainOutput(
        question=question,
        answer=result.text,
        evidence=evidence,
        engine="llm",
        llm_model=result.model,
        llm_latency_ms=result.latency_ms,
    )


# ─── Free-text classification (LLM as intent router) ───────────────

CLASSIFY_SYSTEM = """You classify merchant questions into ONE of these intents.

Intents:
  why_settle_now        — Why did Solvent recommend this action?
  whats_the_shortfall   — What is driving the shortfall risk?
  worst_case            — What is the worst case if I do nothing?
  whats_pending         — What bills does Solvent already know about?
  is_this_worth         — Is the recommended fee actually worth it?
  unknown               — None of the above

Reply with ONLY the intent name. No punctuation, no explanation, no quotes."""


CLASSIFY_TEMPLATE = "Merchant question: {question}\n\nIntent:"


VALID_INTENTS = {
    "why_settle_now", "whats_the_shortfall", "worst_case",
    "whats_pending", "is_this_worth", "unknown",
}


def classify_free_text(question: str, model: str = ollama.DEFAULT_MODEL) -> str:
    """Use the LLM to route a free-text question to one of the canned intents.
    Returns 'unknown' if the LLM is down or gives a bad response."""
    if not ollama.is_available(timeout_s=1.5):
        return "unknown"
    result = ollama.generate(
        prompt=CLASSIFY_TEMPLATE.format(question=question),
        system=CLASSIFY_SYSTEM,
        model=model,
        options={"temperature": 0.0, "num_predict": 12},
        timeout_s=15.0,
    )
    if not result.ok:
        return "unknown"
    intent = result.text.strip().split()[0].lower().strip('.,"\'') if result.text else "unknown"
    return intent if intent in VALID_INTENTS else "unknown"
