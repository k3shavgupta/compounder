"""Read a 10-K with a local model, and refuse anything it cannot prove.

Two stages, deliberately split:

  embed + rank   cheap, runs over every chunk, finds the ~8 passages that matter
  generate       expensive, sees only those passages, answers with quotes

The split is what makes an 8 GB card viable. No consumer GPU can hold a 150k
token filing in KV cache, but none of them needs to: an embedding model finds
the right ten pages far more cheaply — and more accurately — than an LLM
skimming two hundred.

The citation rule is enforced in code, not by asking nicely. Every quote is
checked against the source text and dropped if it is not literally there. A
model that paraphrases loses the claim; a model that invents one loses it too.
"""
from __future__ import annotations

import math
import os

import requests

from ingest.filings import normalise

DEFAULT_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")

# Ollama silently truncates at its default of 2-4k. A chunk set that overflows
# would be read in part and answered from with full confidence, so this is set
# explicitly on every call and never left to the default.
NUM_CTX = 16384
TOP_K = 8

# Ollama evicts an idle model from VRAM and reloads it on the next call. Across
# an eval sweep that is minutes of pure reload time and makes runs look slower
# than they are, so pin both models in memory for the duration.
KEEP_ALIVE = "30m"

QUESTIONS = [
    {
        "key": "customer_concentration",
        "ask": "Do any single customers account for 10% or more of revenue? "
               "If so, name them and give the percentages.",
        "probe": "customer concentration major customers percentage of net revenue "
                 "one customer accounted for revenues",
    },
    {
        "key": "debt_maturity",
        "ask": "When does the company's debt mature? Give the schedule of "
               "principal repayments by year if one is disclosed.",
        "probe": "aggregate maturities of long-term debt principal payments due "
                 "by year thereafter senior notes",
    },
    {
        "key": "competition",
        "ask": "Who does the company say it competes with, and what does it "
               "claim as its competitive advantages?",
        "probe": "competition competitors we compete principally on the basis of "
                 "competitive advantages barriers to entry",
    },
    {
        "key": "key_risks",
        "ask": "What are the most significant risks disclosed that could "
               "permanently impair this business?",
        "probe": "risk factors could materially adversely affect our business "
                 "results of operations depend substantially",
    },
]

SYSTEM = (
    "You answer questions about SEC filings using ONLY the provided excerpts.\n"
    "For every claim you make you must give the exact sentence from the excerpts "
    "that supports it, copied character for character. Never paraphrase inside a "
    "quote. Never write a quote that is not present in the excerpts.\n"
    "If the excerpts do not answer the question, reply with exactly: NOT FOUND\n"
    "Format each finding as:\n"
    "CLAIM: <one sentence in your own words>\n"
    "QUOTE: <exact text from the excerpts>"
)


def _post(path, payload, host=None, timeout=600):
    r = requests.post((host or DEFAULT_HOST) + path, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def embed(texts, host=None, model=None):
    out = []
    for t in texts:
        d = _post("/api/embeddings",
                  {"model": model or EMBED_MODEL, "prompt": t,
                   "keep_alive": KEEP_ALIVE}, host)
        out.append(d["embedding"])
    return out


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank(question_vec, chunk_vecs, k=TOP_K):
    scored = [(_cosine(question_vec, v), i) for i, v in enumerate(chunk_vecs)]
    scored.sort(reverse=True)
    return [i for _, i in scored[:k]]


def ask(question, excerpts, host=None, model=None):
    prompt = "EXCERPTS:\n\n" + "\n\n---\n\n".join(excerpts) + "\n\nQUESTION: " + question
    d = _post(
        "/api/generate",
        {
            "model": model or CHAT_MODEL,
            "system": SYSTEM,
            "prompt": prompt,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.0},
        },
        host,
    )
    return d.get("response", "").strip()


MIN_QUOTE_WORDS = 12


def _is_evidence(quote):
    """Reject section headings masquerading as evidence.

    Existing verbatim in the filing is necessary but not sufficient: a heading
    like "Risks Related to Our Ability to Grow Our Business" is really in the
    document and supports nothing. Headings are short and have no sentence
    structure, so requiring a sentence's worth of words filters them without
    touching real prose.
    """
    words = quote.split()
    if len(words) < MIN_QUOTE_WORDS:
        return False
    # A heading is title-case throughout; prose is not.
    alpha = [w for w in words if w[:1].isalpha()]
    if alpha and all(w[:1].isupper() for w in alpha):
        return False
    return True


def verify(answer, source_text):
    """Split the answer into claim/quote pairs and keep only provable findings.

    Returns (findings, n_rejected). A finding survives three tests: the claim is
    substantive, the quote reads like evidence rather than a heading, and the
    quote appears verbatim in the filing. The model is never trusted, it is
    checked — but note this proves the quote is REAL, not that it supports the
    claim. Measuring that gap is what the eval harness is for.
    """
    haystack = normalise(source_text)
    findings, rejected = [], 0
    claim = None
    for line in answer.splitlines():
        line = line.strip()
        if line.upper().startswith("CLAIM:"):
            claim = line[6:].strip()
        elif line.upper().startswith("QUOTE:"):
            quote = line[6:].strip().strip('"')
            ok = (
                claim
                and claim.lower() not in {"none", "n/a", "not found", "-"}
                and _is_evidence(quote)
                and normalise(quote) in haystack
            )
            if ok:
                findings.append({"claim": claim, "quote": quote})
            else:
                rejected += 1
            claim = None
    return findings, rejected
