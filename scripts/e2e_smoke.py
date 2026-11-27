"""End-to-end smoke test against a running app (used in CI with a tiny model).

usage: python scripts/e2e_smoke.py http://127.0.0.1:8765
"""
import json
import sys
import tempfile
import time

import httpx
import pymupdf

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8765"
H = {"X-PSR": "1"}
c = httpx.Client(base_url=BASE, timeout=600)


def wait(pred, what, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(3)
    raise SystemExit(f"timeout waiting for {what}")


def runtime_ready():
    try:
        st = c.get("/api/status").json()
    except httpx.TransportError:
        return None  # app still starting
    rt = st["runtime"]
    if rt["status"] == "error":
        raise SystemExit("runtime error: " + str(rt["error"]))
    return st if rt["status"] == "ready" else None


st = wait(runtime_ready, "engine")
print("engine:", json.dumps(st["runtime"], indent=1))

# security: state-changing request without header must be rejected
assert c.post("/api/settings", json={"temperature": 0.5}).status_code == 403, "CSRF guard missing"
assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 403, "Host guard missing"

doc = pymupdf.open()
for ch, (title, thesis) in enumerate([("War and the State", "war made the state and the state made war"),
                                      ("Credible Commitment", "constitutional limits let sovereigns borrow")], 1):
    for k in range(8):
        p = doc.new_page()
        head = f"Chapter {ch}\n{title}\n\n" if k == 0 else ""
        p.insert_textbox(pymupdf.Rect(50, 50, 550, 780),
                         head + (f"The author argues that {thesis}. Evidence from England and France. " * 12),
                         fontsize=10)
pdf = tempfile.mktemp(suffix=".pdf")
doc.save(pdf)
job = c.post("/api/documents/upload", params={"filename": "e2e.pdf"}, headers=H,
             content=open(pdf, "rb").read()).json()
print("ingest job", job["id"])


def job_done(jid):
    def f():
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("error", "cancelled"):
            raise SystemExit(f"job failed: {j}")
        return j if j["status"] == "done" else None
    return f


j = wait(job_done(job["id"]), "ingest")
print("ingest:", j["message"])
docs = c.get("/api/documents").json()
assert docs and docs[0]["n_chunks"] > 0


def chat(body):
    events = []
    with c.stream("POST", "/api/chat", json=body, headers=H) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


ev = chat({"message": "What does the author argue about war and the state?"})
kinds = [e["type"] for e in ev]
print("library chat events:", kinds[:12], "...")
assert "tool" in kinds, "agent did not use any tool"
done = [e for e in ev if e["type"] == "done"]
assert done and done[0]["content"].strip(), f"no answer: {ev[-3:]}"
print("answer:", done[0]["content"][:300])

ev = chat({"message": "Define the security dilemma in two sentences.", "use_library": False})
done = [e for e in ev if e["type"] == "done"]
assert done and done[0]["content"].strip() and not any(e["type"] == "tool" for e in ev)
print("general chat ok")

r = c.post("/api/export_text", json={"title": "t", "content": "# Hi\n\nText [D1 p.1]"}, headers=H)
assert r.status_code == 200 and len(r.content) > 1000
print("E2E OK")
