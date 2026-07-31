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

# Point OLLAMA_HOST at whatever machine runs the models. It does not have to be
# the machine running this code — mine is a separate box on a Tailscale network,
# because the GPU and the always-on server are not the same computer.
DEFAULT_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")

# Ollama silently truncates at its default of 2-4k. A chunk set that overflows
# would be read in part and answered from with full confidence, so this is set
# explicitly on every call and never left to the default.
NUM_CTX = 16384
TOP_K = 8

# Ollama does not cap output by default. Asking for multi-sentence quotes was
# enough to send the model into a repeating loop that ran past ten minutes and
# blocked every other request, since Ollama serialises per model. Three or four
# quote/claim pairs need well under this.
MAX_TOKENS = 800

# A term matching more than this share of chunks carries no information and is
# ignored for seeding.
MAX_TERM_SHARE = 0.12

# Ollama evicts an idle model from VRAM and reloads it on the next call. Across
# an eval sweep that is minutes of pure reload time and makes runs look slower
# than they are, so pin both models in memory for the duration.
KEEP_ALIVE = "30m"

QUESTIONS = [
    {
        "key": "customer_concentration",
        # Seeding is enabled here alone: it recovered Adobe and Trade Desk
        # (0 -> 1 each, both verified). On the other three questions it either
        # did nothing or displaced better chunks, so it stays off until an eval
        # says otherwise.
        "hybrid": True,
        "terms": ["10% of", "significant customers", "no customers", "accounted for more than", "of our total revenue"],
        "ask": "Do any single customers account for 10% or more of revenue? "
               "If so, name them and give the percentages.",
        # Both polarities. A probe written only for positive disclosures missed
        # every "no customer represented 10% of net revenue" note in the eval —
        # three of six misses — because an affirmative absence embeds nowhere
        # near "one customer accounted for 21% of revenue".
        "probe": "significant customers concentration one customer accounted for "
                 "10% of net revenue; no customers represented at least 10% of "
                 "net revenue or trade receivables",
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

# Reverted to CLAIM-first on 2026-08-01. A QUOTE-first variant that also banned
# heading quotes left the miss rate flat at 22%, doubled the mechanical
# rejection rate, and lost the Adobe customer-concentration finding that hybrid
# retrieval had just recovered. Its only possible gain was precision, which
# would have cost 22 fresh labels to measure — not worth trading a known-good
# state for. Keep it reverted unless an eval says otherwise.
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


# 10 minutes was not enough once the prompt started asking for multi-sentence
# quotes. A stalled generation should fail the run, not silently truncate it,
# so the cap is generous rather than absent.
def _post(path, payload, host=None, timeout=1800):
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


def rank(question_vec, chunk_vecs, k=TOP_K, chunks=None, terms=None):
    """Hybrid retrieval: keyword hits first, then embedding similarity.

    Pure dense retrieval is good at meaning and bad at exact strings. Adobe's
    "no customers that represented at least 10% of net revenue" ranked 35th of
    291 against a well-tuned probe, because the disclosure turns on the literal
    token "10%" and embeddings do not privilege it.

    Chunks matching a question's literal terms are seeded into the result, then
    the remaining slots are filled by cosine rank. Keyword search alone would be
    brittle in the other direction — hence both.
    """
    dense = [i for _, i in sorted(
        ((_cosine(question_vec, v), i) for i, v in enumerate(chunk_vecs)), reverse=True)]

    if not (chunks and terms):
        return dense[:k]

    # Only discriminative terms are allowed to seed. "10% of" appears in a
    # handful of chunks and pinpoints the disclosure; "materially adversely
    # affect" appears on nearly every page of a 10-K and seeding on it floods
    # the excerpts with boilerplate. Naive seeding fixed customer_concentration
    # and broke key_risks for exactly this reason — this is the IDF idea in its
    # cheapest possible form.
    low_chunks = [c.lower() for c in chunks]
    useful = [t for t in terms
              if 0 < sum(1 for c in low_chunks if t in c) <= MAX_TERM_SHARE * len(chunks)]
    if not useful:
        return dense[:k]

    hits = []
    for i, c in enumerate(low_chunks):
        n = sum(1 for t in useful if t in c)
        if n:
            hits.append((n, -dense.index(i), i))
    hits.sort(reverse=True)

    # Reserve at most half the budget for keyword matches so a term that appears
    # everywhere cannot crowd out semantic relevance entirely.
    picked = [i for _, _, i in hits[: max(1, k // 2)]]
    for i in dense:
        if len(picked) >= k:
            break
        if i not in picked:
            picked.append(i)
    return picked


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
            "options": {"num_ctx": NUM_CTX, "temperature": 0.0,
                        "num_predict": MAX_TOKENS},
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
    """Pair up QUOTE/CLAIM lines and keep only provable findings.

    Order-agnostic: the prompt asks for QUOTE first, but a model that reverts to
    CLAIM first should not have its output silently discarded. A pair is emitted
    as soon as both halves are present.

    A finding survives three tests: the claim is substantive, the quote reads
    like evidence rather than a heading, and the quote appears verbatim in the
    filing. The model is never trusted, it is checked — but note this proves the
    quote is REAL, not that it supports the claim. Measuring that gap is what
    the eval harness is for.
    """
    haystack = normalise(source_text)
    findings, rejected = [], 0
    quote = claim = None

    def flush():
        nonlocal quote, claim, rejected
        if quote is None and claim is None:
            return
        ok = (
            claim
            and quote
            and claim.lower() not in {"none", "n/a", "not found", "-"}
            and _is_evidence(quote)
            and normalise(quote) in haystack
        )
        if ok:
            findings.append({"claim": claim, "quote": quote})
        else:
            rejected += 1
        quote = claim = None

    for line in answer.splitlines():
        line = line.strip()
        upper = line.upper()
        if upper.startswith("QUOTE:"):
            if quote is not None:
                flush()
            quote = line[6:].strip().strip('"')
            if claim is not None:
                flush()
        elif upper.startswith("CLAIM:"):
            if claim is not None:
                flush()
            claim = line[6:].strip()
            if quote is not None:
                flush()
    flush()
    return findings, rejected
