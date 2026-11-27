# PoliSci RAG

A private, offline **agentic RAG research assistant** for long political-science texts.
Load 1,000+ page PDFs, then:

- **Research chat**: an agent that plans, searches, reads exact pages, checks chapter digests and answers with **page citations** you can click to see the source page.
- **Book reviews**: journal-style reviews (*APSR* / *Perspectives on Politics*), seminar critical-analysis papers, reading briefs with discussion questions, or argument maps, all grounded in the text.
- **Comparisons**: side-by-side analysis of two or more texts along axes you choose (or that the model proposes), with an at-a-glance table and a synthesis.
- **Export** any review, comparison or answer to **Word (.docx)** with readable citations (`(Title, p. 217)`) and a source list.

Everything runs on your own computer. No account, API key or internet connection is needed after the one-time model download.

## Install, repair or update (Windows)

Press **Win + R**, paste this line and press **Enter**:

```
powershell -ExecutionPolicy Bypass -c "iex ((New-Object Net.WebClient).DownloadString('https://github.com/gelnagar/rag-polisci/releases/latest/download/install.ps1'))"
```

This line handles a fresh install, and also repairs or updates an existing one. It takes about a minute and keeps downloaded models, books and settings. It creates a **PoliSci RAG** shortcut in the Start menu and on the desktop, then opens the app in your browser. On first launch, click **Download models** (about 5.3 GB, one time; it resumes if interrupted).

Alternatives, both on the [Releases page](../../releases/latest):
- Double-click `PoliSciRAG-Install.cmd`. If Windows warns, choose **More info → Run anyway**.
- Use the classic `PoliSciRAG-Setup-x.y.z.exe` wizard.

Later updates can be installed from inside the app with the **Update now** button.

**Why not just an .exe?** Version 0.1.0 shipped a self-built, unsigned `.exe`, and Windows Defender/SmartScreen often block those. From 0.2.0 on, the app runs on the **official, digitally signed Python runtime from python.org**, so no self-made executable is involved. The one-line installer doesn't download a file through the browser, so SmartScreen doesn't intercept it. If **Smart App Control** is turned on (Windows 11: Windows Security → App & browser control), Windows blocks every unsigned program, including the llama.cpp engine. In that case the only fixes are code-signing the build or turning Smart App Control off.

**Requirements:** Windows 10/11 64-bit, 16 GB RAM recommended (8 GB minimum), about 8 GB of free disk space. A GPU (NVIDIA, AMD or Intel, through Vulkan) is used automatically and sized to the free video memory. Otherwise the CPU is used, and if the GPU driver crashes the engine restarts on the CPU.

## What's inside (~5.3 GB of models)

| Component | Model | Size | Role |
|---|---|---|---|
| Reasoning/writing LLM | **Qwen3-8B** (Q4_K_M GGUF) | 5.03 GB | Tool-calling agent, analysis, long-form writing; optional "deep thinking" mode |
| Embeddings | **nomic-embed-text v1.5** (f16 GGUF) | 0.27 GB | Semantic search |
| Inference engine | **llama.cpp** `llama-server` (Vulkan + CPU builds) | bundled | Runs both models locally |

### How it handles 1,000-page books

1. **Ingest**: PyMuPDF extracts text page by page and keeps the book's *printed* page numbers (e.g. `xii`, `217`) so citations match the physical book. Running headers and footers are stripped and hyphenation is repaired.
2. **Structure**: chapters come from the PDF's table of contents, or from "Chapter N" headings if there is none. Very long chapters are split into parts of 45 pages or less.
3. **Index**: overlapping ~450-token passages are embedded. Search is **hybrid** (dense vectors + BM25 via SQLite FTS5, combined with reciprocal-rank fusion).
4. **Digest** (optional, recommended for whole-book work): a map-reduce summary of every chapter plus a book-level overview.
   - *Fast* samples each chapter. It takes minutes on a GPU and roughly an hour on a CPU for 1,000 pages.
   - *Thorough* reads every page. It gives the best results but takes several hours on a CPU.
   - You can cancel a digest and resume it later.
5. **Agent**: the model has the tools `search_documents`, `read_pages`, `get_outline`, `get_chapter_summaries`, `compare_documents` and `list_documents`. It gathers evidence over several steps, then writes the final answer (optionally after "thinking").
6. **Reviews and comparisons** are multi-stage pipelines. They combine the digest with targeted retrieval for thesis, method, evidence, rival explanations and limitations, writing each section in a separate call so that everything fits the context window.

## Tips for Liam

- Build a **fast digest** for each book before asking whole-book questions or writing reviews. Reviews build one automatically if it's missing.
- In chat, tick specific documents under **Focus on** to keep the agent on those texts. Untick **Use my library** for a general political-science tutor chat (concepts, essay outlines) that doesn't search the books.
- Use the **Extra instructions** box on reviews: e.g. *"engage Skocpol's critique; assess the case selection; 1,500 words."*
- Click any citation chip to see the source page, or open the PDF at that page.
- Always check quotations and page numbers against the book before submitting work. Local models can make mistakes.
- Scanned PDFs (images of pages) must be OCR'd first (e.g. with Adobe Acrobat "Recognize Text" or `ocrmypdf`).

## Settings

- **Context window**: automatic (8k / 16k / 32k depending on installed RAM). You can override it.
- **GPU layers**: `-1` = auto, `0` = CPU only.
- **Deep thinking**: better analysis. It's off by default because on a CPU it can add minutes per answer; turn it on if you have a GPU.
- **Advanced**: point the app at any OpenAI-compatible server (Ollama, LM Studio, a hosted API) or at your own GGUF files.

Data lives in `%LOCALAPPDATA%\PoliSciRAG` (models, library database, copies of PDFs, exports, logs). It is kept when you uninstall.

## Developing

```bash
pip install -r requirements-dev.txt
# needs a llama.cpp `llama-server` on PATH (or LLAMA_SERVER_BIN=/path/to/llama-server)
python launcher.py            # opens http://127.0.0.1:8765
python -m pytest -q tests
```

GitHub Actions (`.github/workflows/build-windows.yml`) builds every release:

1. Assembles the app on the signed python.org embeddable runtime, plus the newest llama.cpp Vulkan and CPU builds.
2. Tests the repair script against a simulated 0.1.0 install, and also tests a fresh install.
3. Runs an end-to-end test of the installed app with a small model: import, agentic chat, general chat, Word export and the security checks.
4. Publishes `install.ps1`, `PoliSciRAG-Install.cmd`, the payload zips and an Inno Setup wizard to GitHub Releases.

To cut a new release, bump `__version__` in `polisci_rag/__init__.py` and push.

## License

AGPL-3.0 (the app uses PyMuPDF, which is AGPL-licensed). The models are under their own licenses: Qwen3 (Apache-2.0) and nomic-embed-text (Apache-2.0).
