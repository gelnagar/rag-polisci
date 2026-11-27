"""FastAPI backend serving the single-page UI and the JSON API."""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, analysis, export, jobs, models, updates
from .agent import run_agent
from .config import DATA_ROOT, PDF_DIR, get_settings, resource_dir, update_settings
from .llm_server import runtime
from .pdf_ingest import ingest_pdf
from .store import get_store

STATIC = resource_dir() / "static"


def start_runtime_async() -> None:
    if models.all_ready() and runtime.status in ("stopped", "error"):
        def go():
            try:
                runtime.ensure_started()
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()


@asynccontextmanager
async def lifespan(_app):
    get_store()
    start_runtime_async()
    updates.check_async()
    yield
    runtime.shutdown()


app = FastAPI(title="PoliSci RAG", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Block DNS-rebinding and cross-site requests from other web pages.

    Only pages served by this app may call the API: the Host must be localhost and every
    state-changing request must carry the X-PSR header (other sites cannot add it without
    a CORS preflight, which this server never grants)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.headers.get("x-psr") != "1":
        return JSONResponse({"detail": "Missing X-PSR header"}, status_code=403)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text("utf-8")


# ------------------------------------------------------------------ status / setup
@app.get("/api/status")
def status():
    free = shutil.disk_usage(DATA_ROOT).free
    return {"version": __version__, "data_dir": str(DATA_ROOT), "models": models.downloader.status(),
            "runtime": runtime.info(), "disk_free": free, "update": updates.status()}


@app.post("/api/update")
def run_update():
    updates.launch_updater()
    return {"ok": True}


@app.post("/api/shutdown")
def shutdown():
    def bye():
        time.sleep(0.5)
        runtime.shutdown()
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return {"ok": True}


@app.post("/api/models/download")
def download_models():
    need = sum(m.size for m in models.ALL_MODELS if not models.is_ready(m)) + 500_000_000
    if shutil.disk_usage(DATA_ROOT).free < need:
        raise HTTPException(400, f"Not enough free disk space: about {need / 1e9:.1f} GB is needed "
                                 f"on the drive holding {DATA_ROOT}.")
    models.downloader.start()
    return models.downloader.status()


@app.post("/api/runtime/start")
def runtime_start():
    start_runtime_async()
    return runtime.info()


@app.post("/api/runtime/restart")
def runtime_restart():
    runtime.shutdown()
    start_runtime_async()
    return runtime.info()


@app.get("/api/settings")
def get_settings_api():
    return asdict(get_settings())


@app.post("/api/settings")
async def set_settings(req: Request):
    body = await req.json()
    before = asdict(get_settings())
    s = update_settings(**body)
    restart_keys = {"context_size", "gpu_layers", "threads", "external_base_url", "chat_model_file",
                    "embed_model_file"}
    if any(before.get(k) != getattr(s, k) for k in restart_keys):
        runtime.shutdown()
        start_runtime_async()
    return asdict(s)


# ------------------------------------------------------------------ documents
def _doc_public(d: dict) -> dict:
    d = dict(d)
    d["has_digest"] = bool(d.get("summary"))
    d.pop("summary", None)
    return d


@app.get("/api/documents")
def list_documents():
    return [_doc_public(d) for d in get_store().documents()]


def _queue_ingest(path: Path, title: str | None = None) -> jobs.Job:
    if not models.all_ready() and not get_settings().external_base_url:
        raise HTTPException(400, "Download the AI models first (see the setup screen).")
    def work(job: jobs.Job):
        doc_id = ingest_pdf(path, job.set_progress, job.cancelled, title=title)
        return {"doc_id": doc_id}
    return jobs.submit("ingest", f"Import {path.name}", work, pool="embed")


@app.post("/api/documents/upload")
async def upload(request: Request, filename: str):
    name = os.path.basename(filename) or "document.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")
    dest = PDF_DIR / name
    i = 1
    while dest.exists():
        dest = PDF_DIR / f"{Path(name).stem}_{i}.pdf"
        i += 1
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return _queue_ingest(dest).to_dict()


@app.post("/api/documents/import_path")
async def import_path(req: Request):
    body = await req.json()
    p = Path(body.get("path", "")).expanduser()
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise HTTPException(400, "Not a PDF file path.")
    return _queue_ingest(p).to_dict()


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    st = get_store()
    d = st.document(doc_id)
    if not d:
        raise HTTPException(404)
    labels = st.page_labels(doc_id)
    secs = [{**s, "start_label": labels.get(s["start_pno"]), "end_label": labels.get(s["end_pno"])}
            for s in st.sections(doc_id)]
    return {**d, "sections": secs}


@app.patch("/api/documents/{doc_id}")
async def edit_document(doc_id: int, req: Request):
    body = await req.json()
    get_store().update_document(doc_id, **{k: v for k, v in body.items() if k in ("title", "author")})
    return get_store().document(doc_id)


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    get_store().delete_document(doc_id)
    return {"ok": True}


@app.get("/api/documents/{doc_id}/page/{label}")
def page(doc_id: int, label: str):
    st = get_store()
    pno = st.pno_for_label(doc_id, label)
    if pno is None:
        raise HTTPException(404)
    r = st.pages_text(doc_id, pno, pno)[0]
    return {"pno": r["pno"], "label": r["label"], "text": r["text"]}


@app.get("/api/documents/{doc_id}/pdf")
def pdf(doc_id: int):
    d = get_store().document(doc_id)
    if not d or not Path(d["path"]).exists():
        raise HTTPException(404)
    return FileResponse(d["path"], media_type="application/pdf")


@app.post("/api/documents/{doc_id}/digest")
async def digest(doc_id: int, req: Request):
    body = await req.json()
    mode = "thorough" if body.get("mode") == "thorough" else "fast"
    d = get_store().document(doc_id)
    if not d:
        raise HTTPException(404)

    def work(job: jobs.Job):
        analysis.build_digest(doc_id, mode, job.set_progress, job.cancelled)
        return {"doc_id": doc_id}
    return jobs.submit("digest", f"{mode.title()} digest: {d['title']}", work).to_dict()


# ------------------------------------------------------------------ chat (streaming agent)
@app.get("/api/chats")
def chats():
    return get_store().chats()


@app.get("/api/chats/{chat_id}")
def chat_messages(chat_id: int):
    return get_store().messages(chat_id)


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int):
    get_store().delete_chat(chat_id)
    return {"ok": True}


@app.post("/api/chat")
async def chat_api(req: Request):
    body = await req.json()
    msg = (body.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "Empty message")
    st = get_store()
    chat_id = body.get("chat_id") or st.new_chat(msg)
    history = [{"role": m["role"], "content": m["content"]} for m in st.messages(chat_id)]
    doc_ids = [int(i) for i in body.get("doc_ids") or []]
    use_library = bool(body.get("use_library", True))
    st.add_message(chat_id, "user", msg)
    q: queue.Queue = queue.Queue()
    stop = threading.Event()

    def worker():
        try:
            answer, cites = run_agent(msg, history, doc_ids or None, q.put, stop.is_set,
                                      use_library=use_library)
            st.add_message(chat_id, "assistant", answer, {"citations": cites})
            q.put({"type": "done", "content": answer, "citations": cites, "chat_id": chat_id})
        except Exception as e:
            q.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        yield json.dumps({"type": "chat", "chat_id": chat_id}) + "\n"
        try:
            while True:
                ev = q.get()
                if ev is None:
                    break
                yield json.dumps(ev) + "\n"
        finally:
            stop.set()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ------------------------------------------------------------------ reviews / comparisons
@app.post("/api/review")
async def review(req: Request):
    body = await req.json()
    doc_id = int(body["doc_id"])
    d = get_store().document(doc_id)
    if not d:
        raise HTTPException(404)

    def work(job: jobs.Job):
        title, content = analysis.write_review(doc_id, body.get("style", "journal"), body.get("instructions", ""),
                                               job.set_progress, job.cancelled, job.append)
        oid = get_store().save_output("review", title, [doc_id], content, {"style": body.get("style")})
        job.output = content
        return {"output_id": oid}
    return jobs.submit("review", f"Review: {d['title']}", work).to_dict()


@app.post("/api/compare")
async def compare(req: Request):
    body = await req.json()
    ids = [int(i) for i in body.get("doc_ids", [])]
    if len(ids) < 2:
        raise HTTPException(400, "Pick at least two documents.")
    axes = [a.strip() for a in body.get("axes", []) if a.strip()]

    def work(job: jobs.Job):
        title, content = analysis.compare(ids, body.get("question", ""), axes, job.set_progress,
                                          job.cancelled, job.append)
        oid = get_store().save_output("comparison", title, ids, content, {"question": body.get("question")})
        job.output = content
        return {"output_id": oid}
    return jobs.submit("compare", "Comparison of " + ", ".join(f"D{i}" for i in ids), work).to_dict()


@app.get("/api/jobs")
def list_jobs():
    return [j.to_dict(with_output=False) for j in sorted(jobs.JOBS.values(), key=lambda j: -j.id)[:50]]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    j = jobs.JOBS.get(job_id)
    if not j:
        raise HTTPException(404)
    return j.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    j = jobs.JOBS.get(job_id)
    if j:
        j.cancel()
    return {"ok": True}


@app.get("/api/outputs")
def outputs():
    return get_store().outputs()


@app.get("/api/outputs/{oid}")
def output(oid: int):
    o = get_store().output(oid)
    if not o:
        raise HTTPException(404)
    return o


@app.delete("/api/outputs/{oid}")
def delete_output(oid: int):
    get_store().delete_output(oid)
    return {"ok": True}


@app.get("/api/outputs/{oid}/export")
def export_output(oid: int, fmt: str = "docx"):
    o = get_store().output(oid)
    if not o:
        raise HTTPException(404)
    if fmt == "md":
        p = export.to_markdown(o["title"], o["content"])
        return FileResponse(p, filename=p.name, media_type="text/markdown")
    p = export.to_docx(o["title"], o["content"])
    return FileResponse(p, filename=p.name,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.post("/api/export_text")
async def export_text(req: Request):
    """Export arbitrary Markdown (e.g. a chat answer) to Word."""
    body = await req.json()
    p = export.to_docx(body.get("title", "Answer"), body.get("content", ""))
    return FileResponse(p, filename=p.name,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
