#!/usr/bin/env python3
"""M5: measure whether the reader can be trusted.

    python run_eval.py build              # run the memos, create blank labels
    python run_eval.py label              # go through them one at a time
    python run_eval.py score              # the numbers
    python run_eval.py build --model llama3.1:8b   # then score and compare

Mechanical verification already proves a quote exists in the filing. It cannot
prove the quote SUPPORTS the claim — Adobe cited a section heading and passed.
That gap is the whole reason this exists, so `quote` is a distinct verdict from
`wrong`: a model that reasons correctly but cites badly needs a different fix
from one that invents facts.

Two files, deliberately separate:

  evals/truth.json           was this disclosed at all? Model-agnostic, so it is
                             written once and reused for every model you test.
  evals/labels-<model>.json  per-finding verdicts for one model.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from gate import reader
from ingest import filings
from ingest.edgar import Edgar

ROOT = Path(__file__).resolve().parent
EVALS = ROOT / "evals"
TRUTH = EVALS / "truth.json"
console = Console()

FINDING_VERDICTS = {
    "ok": "claim is true and the quote supports it",
    "quote": "claim is true but the quote does not support it",
    "wrong": "the claim is factually wrong",
}
TRUTH_VERDICTS = {
    "disclosed": "the filing does answer this",
    "absent": "the filing genuinely does not answer this",
}


def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def labels_path(model):
    return EVALS / "labels-{}.json".format(model.replace(":", "-").replace("/", "-"))


def build(tickers, model, host):
    edgar = Edgar()
    truth = _load(TRUTH, {})
    out = {"model": model, "built": date.today().isoformat(), "items": []}

    # Carry verdicts forward for findings that come back byte-identical. The
    # same quote against the same question is the same judgement, so re-asking
    # for it wastes the one resource this harness actually runs on. Anything
    # that changed is left blank and must be judged fresh.
    # Search every archived run, not just the last one. A build that produces
    # different quotes (a prompt experiment, say) otherwise orphans the verdicts
    # behind it, and the next build asks for them again. Later files win.
    previous = _load(labels_path(model), {}) or {}
    prior = {}
    sources = sorted(EVALS.glob("labels-*.json"), key=lambda f: f.stat().st_mtime)
    for f in sources:
        for i in (_load(f, {}) or {}).get("items", []):
            if i.get("verdict"):
                prior[(i["ticker"], i["question"], i["quote"])] = i
    if previous.get("items"):
        archive = labels_path(model).with_name(
            labels_path(model).stem + "-" + previous.get("built", "prev") + ".json")
        if not archive.exists():
            _save(archive, previous)
            console.print("[dim]archived previous run -> {}[/]".format(archive.name))
    console.print("[dim]{} verdicts available to carry from {} file(s)[/]".format(
        len(prior), len(sources)))

    for tk in tickers:
        cik = edgar.cik_for(tk)
        filing = edgar.latest_10k(cik) if cik else None
        if not filing:
            console.print("[yellow]{}: no 10-K, skipped[/]".format(tk))
            continue
        html = edgar.raw(filing["url"], "doc_" + filing["accession"].replace("-", ""))
        text = filings.to_text(html)
        chunks = filings.chunk(text)
        console.print("[dim]{} — {} chunks[/]".format(tk, len(chunks)))
        vecs = reader.embed(chunks, host=host)

        truth.setdefault(tk, {})
        for q in reader.QUESTIONS:
            qvec = reader.embed([q["probe"]], host=host)[0]
            excerpts = [chunks[i] for i in reader.rank(qvec, vecs, chunks=chunks, terms=q.get("terms") if q.get("hybrid") else None)]
            answer = reader.ask(q["ask"], excerpts, host=host, model=model)
            found, dropped = reader.verify(answer, text)

            # Blank unless already judged — truth is about the filing, not the
            # model, so it survives across every model you evaluate.
            truth[tk].setdefault(q["key"], "")

            carried = 0
            for i, f in enumerate(found):
                seen = prior.get((tk, q["key"], f["quote"]))
                out["items"].append({
                    "id": "{}/{}/{}".format(tk, q["key"], i),
                    "ticker": tk, "question": q["key"],
                    "claim": f["claim"], "quote": f["quote"],
                    "verdict": seen["verdict"] if seen else "",
                    **({"note": seen["note"]} if seen and seen.get("note") else {}),
                    **({"carried": True} if seen else {}),
                })
                carried += 1 if seen else 0
            out.setdefault("dropped", {})["{}/{}".format(tk, q["key"])] = dropped
            out.setdefault("counts", {})["{}/{}".format(tk, q["key"])] = len(found)
            console.print("   {:<24} {} found, {} dropped{}".format(
                q["key"], len(found), dropped,
                ", {} carried".format(carried) if carried else ""))

    _save(TRUTH, truth)
    _save(labels_path(model), out)
    blank = sum(1 for t in truth.values() for v in t.values() if not v)
    blank_items = sum(1 for i in out["items"] if not i["verdict"])
    console.print("\n[bold]{} findings, {} needing a verdict[/]  ->  {}".format(
        len(out["items"]), blank_items, labels_path(model).name))
    console.print("[bold]{} truth entries to fill[/]  ->  {}".format(blank, TRUTH.name))
    console.print("\nRun: [bold]python run_eval.py label[/]")


def label(model):
    """One item at a time. Editing raw JSON is a good way to abandon this."""
    lp = labels_path(model)
    data = _load(lp, None)
    if not data:
        console.print("[red]no labels file — run build first[/]")
        return 1
    truth = _load(TRUTH, {})

    todo = [i for i in data["items"] if not i["verdict"]]
    console.print("[bold]{} findings left[/]  (ok / quote / wrong, s=skip, x=stop)\n".format(len(todo)))
    for item in todo:
        console.print("[dim]{}[/]".format(item["id"]))
        console.print("  [bold]CLAIM[/] {}".format(item["claim"]))
        console.print('  [bold]QUOTE[/] [dim]"{}"[/]'.format(item["quote"][:400]))
        for k, v in FINDING_VERDICTS.items():
            console.print("     [cyan]{:<6}[/] {}".format(k, v))
        ans = console.input("  > ").strip().lower()
        if ans == "x":
            break
        # Match on the leading word, not the whole line. Requiring an exact
        # string silently discarded answers like "quote - true elsewhere in the
        # filing but not here", which is a considered judgement, not a typo.
        head = ans.split()[0].strip(" -–—:,") if ans else ""
        verdict = next((v for v in FINDING_VERDICTS if head == v), None)
        if verdict:
            item["verdict"] = verdict
            if len(ans) > len(head) + 2:
                item["note"] = ans[len(head):].strip(" -–—:,")
            _save(lp, data)
        elif head and head != "s":
            console.print("  [yellow]not recognised — skipped[/]")
        console.print()

    return label_truth()


def label_truth():
    """The truth set on its own.

    Kept separately reachable because it is the cheap half — one word per item,
    no reading for most — and it produces the miss rate, which is the number
    that matters most. Burying it behind the findings loop meant it never got
    done.
    """
    truth = _load(TRUTH, {})
    pending = [(t, q) for t, qs in truth.items() for q, v in qs.items() if not v]
    if not pending:
        console.print("[green]truth set already complete[/]")
        return 0

    console.print("\n[bold]Did the filing answer this at all?[/]  [dim]({} left)[/]".format(
        len(pending)))
    console.print("[dim]This is about the FILING, not the model — it is reused for every[/]")
    console.print("[dim]model you test. disclosed / absent, s=skip, x=stop[/]\n")
    for tk, q in pending:
        console.print("  [bold]{}[/] — {}".format(tk, q.replace("_", " ")))
        ans = console.input("  [cyan]disclosed / absent >[/] ").strip().lower()
        if ans == "x":
            break
        if ans.startswith("d"):
            truth[tk][q] = "disclosed"
            _save(TRUTH, truth)
        elif ans.startswith("a"):
            truth[tk][q] = "absent"
            _save(TRUTH, truth)
    console.print("\n[green]saved[/]  ->  python run_eval.py score")
    return 0


def score(model):
    data = _load(labels_path(model), None)
    if not data:
        console.print("[red]no labels file for {}[/]".format(model))
        return 1
    truth = _load(TRUTH, {})
    items = data["items"]
    judged = [i for i in items if i["verdict"]]

    if not judged:
        console.print("[yellow]nothing labelled yet — run: python run_eval.py label[/]")
        return 1

    n = len(judged)
    ok = sum(1 for i in judged if i["verdict"] == "ok")
    badq = sum(1 for i in judged if i["verdict"] == "quote")
    wrong = sum(1 for i in judged if i["verdict"] == "wrong")

    # A question the model reported nothing for, where the filing did answer it.
    counts = data.get("counts", {})
    missed = shown = 0
    for tk, qs in truth.items():
        for q, v in qs.items():
            if not v:
                continue
            found = counts.get("{}/{}".format(tk, q), 0)
            if v == "disclosed":
                shown += 1
                if found == 0:
                    missed += 1

    t = Table(title="eval — {}".format(model), title_style="bold", box=None, pad_edge=False)
    t.add_column("metric"); t.add_column("value", justify="right"); t.add_column("")
    t.add_row("findings labelled", str(n), "of {}".format(len(items)))
    t.add_row("precision", "{:.0%}".format(ok / n), "claim true AND quote supports it")
    t.add_row("citation failures", "{:.0%}".format(badq / n),
              "true claim, quote does not support it")
    t.add_row("factual errors", "{:.0%}".format(wrong / n), "claim is simply wrong")
    if shown:
        t.add_row("misses", "{:.0%}".format(missed / shown),
                  "{} of {} disclosed facts not surfaced".format(missed, shown))
    t.add_row("dropped mechanically", str(sum(data.get("dropped", {}).values())),
              "quote absent, heading, or empty claim")
    console.print()
    console.print(t)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "label", "truth", "score"])
    ap.add_argument("--model", default=reader.CHAT_MODEL)
    ap.add_argument("--host", default=None)
    ap.add_argument("--fixtures", default=str(EVALS / "fixtures.txt"))
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    if args.mode == "build":
        tickers = [l.split("#")[0].strip() for l in Path(args.fixtures).read_text().splitlines()]
        return build([t for t in tickers if t], args.model, args.host) or 0
    if args.mode == "label":
        return label(args.model)
    if args.mode == "truth":
        return label_truth()
    return score(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
