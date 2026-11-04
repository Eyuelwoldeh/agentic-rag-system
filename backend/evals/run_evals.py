"""Eval harness: ingest the sample corpus, run every question through the agent, grade, report.

Run from backend/:  uv run python -m evals.run_evals
Costs real API money (~7 agent runs + critic calls).
"""
import json
import pathlib
import statistics

from app import agent
from app.db import connect
from app.ingest import ingest_csv, ingest_document

HERE = pathlib.Path(__file__).parent


def setup():
    with connect() as conn:
        conn.execute("DELETE FROM documents WHERE name = 'acme_handbook.md'")
    ingest_document("acme_handbook.md", (HERE / "sample/acme_handbook.md").read_bytes())
    ingest_csv("orders.csv", (HERE / "sample/orders.csv").read_bytes())


def grade(answer: str, case: dict) -> bool:
    a = answer.lower()
    hits = [e.lower() in a for e in case["expect"]]
    return any(hits) if case.get("any") else all(hits)


def main():
    setup()
    results = []
    for case in json.loads((HERE / "questions.json").read_text()):
        done, tools_used, first_pass = {}, [], None
        for ev in agent.run(case["q"], []):
            if ev["type"] == "tool":
                tools_used.append(ev["name"])
            elif ev["type"] == "critic" and first_pass is None:
                first_pass = ev["passed"]
            elif ev["type"] in ("done", "error"):
                done = ev if ev["type"] == "done" else {**done, "error": ev["message"]}
        ok = grade(done.get("answer", ""), case)
        results.append({**done, "ok": ok, "first_pass": first_pass, "tools": tools_used})
        print(f"{'PASS' if ok else 'FAIL'}  attempts={done.get('attempts')}  {done.get('latency_ms')}ms  "
              f"tools={sorted(set(tools_used))}  {case['q']}")

    n = len(results)
    print(f"\nAccuracy:            {sum(r['ok'] for r in results)}/{n}")
    print(f"Critic passed first: {sum(bool(r['first_pass']) for r in results)}/{n}")
    print(f"Median latency:      {statistics.median(r.get('latency_ms', 0) for r in results)} ms")
    print(f"Total tokens:        in={sum(r.get('input_tokens', 0) for r in results)} "
          f"out={sum(r.get('output_tokens', 0) for r in results)}")


if __name__ == "__main__":
    main()
