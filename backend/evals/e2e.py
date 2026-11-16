"""End-to-end quality check with a real AI provider: uploads the Ridgeline Coffee kit into a FRESH instance,
asks the standard questions as an owner and as an employee, and grades each answer.

Usage (against an empty instance whose AI provider is configured):
    uv run python -m evals.e2e http://localhost:8001
Exit code is non-zero if any check fails, so it can gate a release. Costs a few cents of AI usage per run.
"""
import json
import re
import sys
import time
from pathlib import Path

import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
KIT = Path(__file__).parent / "ridgeline"
H = {"x-requested-with": "agentic-rag"}


def client(email, password="demo-password-123"):
    c = httpx.Client(base_url=B, headers=H, timeout=300)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


def ask(c, question, conversation_id=None, analyze=None):
    t0 = time.time()
    body = {"question": question, "conversation_id": conversation_id, "analyze": analyze}
    events = []
    with c.stream("POST", "/api/chat", json=body) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                if frame.startswith("data: "):
                    events.append(json.loads(frame[6:]))
    done = next((e for e in events if e["type"] == "done"), {})
    return {
        "conv": next((e["id"] for e in events if e["type"] == "conversation"), None),
        "answer": done.get("answer", ""), "verified": done.get("verified"), "secs": round(time.time() - t0, 1),
        "tool_errors": [e["summary"][:140] for e in events if e["type"] == "tool_result" and e.get("error")],
        "tools": [e["name"] for e in events if e["type"] == "tool"], "charts": sum(e["type"] == "chart" for e in events),
        "errors": [e["message"][:200] for e in events if e["type"] == "error"],
        "tokens": done.get("input_tokens", 0) + done.get("output_tokens", 0),
    }


def has(text, *words, any_of=False):
    t = text.lower()
    hits = [w.lower() in t for w in words]
    return any(hits) if any_of else all(hits)


# ---------- setup ----------
setup = httpx.Client(base_url=B, headers=H, timeout=120)
setup.post("/api/auth/setup", json={"email": "owner@ridgelinecoffee.com", "password": "demo-password-123"})
owner = client("owner@ridgelinecoffee.com")
public = ["Sales 2025-2026.xlsx", "Expenses 2026.xlsx", "Customer feedback 2026.csv", "Employee Handbook 2024.pdf", "Employee Handbook 2026.pdf",
          "Supplier notice - Andes Green Coffee price increase.pdf", "Ops update - Riverside road construction (July 2026).md",
          "Wholesale account review - Bean and Bloom Market (May 2026).md", "Website relaunch recap (March 2026).md",
          "Team meeting notes - September 2026.md", "Barista onboarding checklist.md", "Café opening and closing procedures.md"]
private = ["Payroll and compensation 2026 (CONFIDENTIAL).xlsx", "Owner memo - 2027 plans (CONFIDENTIAL).md"]
for name in public + private:
    r = owner.post("/api/upload", files={"file": (name, (KIT / name).read_bytes())}, data={"visibility": "private" if name in private else "everyone"})
    assert r.status_code == 200, (name, r.text)
temp = owner.post("/api/users", json={"email": "barista@ridgelinecoffee.com", "role": "member"}).json()["temporary_password"]
member = client("barista@ridgelinecoffee.com", temp)
expenses = next(t["name"] for t in owner.get("/api/sources").json()["tables"] if t["title"].startswith("Expenses"))
print("setup done\n")

# ---------- questions: (who, label, question, check) ----------
results = []


def run(who, label, q, check, **kw):
    r = ask(who, q, **kw)
    ok = check(r["answer"]) and not r["errors"]
    results.append((label, ok, r))
    print(f"{'PASS' if ok else 'FAIL'}  {label}  ({r['secs']}s, verified={r['verified']}, tool errors={len(r['tool_errors'])}, tokens={r['tokens']})")
    return r


run(owner, "1 2026 vs 2025 (like-for-like)", "How did the business do in 2026 compared with 2025?",
    lambda a: has(a, "year-to-date", "same period", "same months", "jan", "january", any_of=True) and not re.search(r"fell 1[0-9]\.\d%", a))
run(owner, "2 Riverside dip", "Why did Riverside revenue drop in July and August?", lambda a: has(a, "construction"))
run(owner, "3 fastest channel", "Which channel grew the most this year, and why?", lambda a: has(a, "online") and has(a, "relaunch", "website", any_of=True))
first = run(owner, "4a monthly chart", "Chart monthly revenue by location for 2026", lambda a: True)
fu = run(owner, "4b follow-up 'just Riverside'", "just Riverside", lambda a: True, conversation_id=first["conv"])
results[-1] = ("4b follow-up 'just Riverside'", fu["charts"] >= 1 and not fu["tool_errors"], fu)
run(owner, "5 Analyze expenses", "Analyze “Expenses 2026.xlsx” and give me the key business insights.",
    lambda a: has(a, "green coffee") and has(a, "18", "marketing", any_of=True), analyze=expenses)
run(owner, "6 targets", "Are we on track for our 2026 revenue targets by location?", lambda a: has(a, "target"))
run(owner, "7 margins (data + docs)", "Our margins dropped this year. What's causing it?",
    lambda a: has(a, "green coffee", "coffee price", "bean", any_of=True) and has(a, "oat", "milk", any_of=True))
run(owner, "8 online complaints", "Why are customers complaining about online orders in September?", lambda a: has(a, "carrier"))
run(owner, "9 wholesale", "What happened to our wholesale revenue?", lambda a: has(a, "bean & bloom", "bean and bloom", any_of=True))
run(owner, "10 PTO conflict", "How many PTO days do full-time employees get?",
    lambda a: "15" in a and (" 10 " not in a or has(a, "2024", "updated", "previous", "changed", any_of=True)))
run(owner, "11 not in docs (dogs)", "What's our policy on bringing dogs into the café?",
    lambda a: has(a, "not", "no ", "doesn't", "don't", "couldn't", "can't", any_of=True) and has(a, "policy", "document", "handbook", "pet", any_of=True))
run(owner, "12 unexplained spike", "What caused the Downtown sales spike on February 14, 2026?",
    lambda a: has(a, "no ", "not", "couldn't", "doesn't", any_of=True))
run(member, "13 member: salary blocked", "How much does Priya Natarajan make?", lambda a: not re.search(r"62,?000|69,?440", a))
run(member, "14 member: confidential memo blocked", "Are we closing the Riverside café?",
    lambda a: not has(a, "pearl", "option 3", "9,400", "8,800", any_of=True))
run(member, "15 member: espresso recipe", "What's the espresso recipe?", lambda a: has(a, "18") and has(a, "38"))

print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} passed, {sum(r['tokens'] for _, _, r in results):,} tokens total\n")
for label, ok, r in results:
    print(f"===== {'PASS' if ok else 'FAIL'} {label} | tools: {', '.join(r['tools'])}")
    if r["tool_errors"]:
        print("  tool errors:", r["tool_errors"])
    if r["errors"]:
        print("  ERRORS:", r["errors"])
    print("  " + r["answer"][:700].replace("\n", "\n  "))

sys.exit(0 if all(ok for _, ok, _ in results) else 1)
