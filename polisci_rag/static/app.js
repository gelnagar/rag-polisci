"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { docs: [], chatId: null, chatScope: new Set(), sending: false };

const H = { "X-PSR": "1" };  // required by the server for state-changing requests
async function api(path, opts = {}) {
  const o = { ...opts, headers: { ...H, ...(opts.headers || {}) } };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(path, o);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtBytes = b => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(0) + " MB";

// ---------------------------------------------------------------- markdown
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>")
       .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
       .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  s = s.replace(/\[D(\d+) p\.([^\]]+)\]/g, (m, d, p) => {
    const doc = state.docs.find(x => x.id == d);
    const t = doc ? (doc.title.length > 22 ? doc.title.slice(0, 20) + "…" : doc.title) : "D" + d;
    return `<span class="cite" data-d="${d}" data-p="${esc(p)}" title="${esc(doc ? doc.title : "")}">${esc(t)}, p.${esc(p)}</span>`;
  });
  return s;
}
function md(src) {
  const lines = String(src || "").split(/\r?\n/);
  let html = "", i = 0, list = null;
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:\-|]+\|\s*$/.test(lines[i + 1])) {
      close();
      const row = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      html += "<table><thead><tr>" + row(l).map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) { html += "<tr>" + row(lines[i]).map(c => `<td>${inline(c)}</td>`).join("") + "</tr>"; i++; }
      html += "</tbody></table>"; continue;
    }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { close(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
    else if ((m = l.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { close(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^>\s?(.*)/))) { close(); html += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if (/^\s*(---|\*\*\*)\s*$/.test(l)) { close(); html += "<hr>"; }
    else if (l.trim()) { close(); html += `<p>${inline(l)}</p>`; }
    else close();
    i++;
  }
  close();
  return `<div class="md">${html}</div>`;
}

// citation popover
document.addEventListener("click", async e => {
  const c = e.target.closest(".cite"); const pop = $("#popover");
  if (!c) { if (!e.target.closest("#popover")) pop.classList.add("hidden"); return; }
  const r = c.getBoundingClientRect();
  pop.style.left = Math.min(r.left, innerWidth - 540) + "px";
  pop.style.top = (r.bottom + 6 + 340 > innerHeight ? Math.max(10, r.top - 350) : r.bottom + 6) + "px";
  pop.innerHTML = "Loading…"; pop.classList.remove("hidden");
  try {
    const p = await api(`/api/documents/${c.dataset.d}/page/${encodeURIComponent(c.dataset.p)}`);
    const doc = state.docs.find(x => x.id == c.dataset.d);
    pop.innerHTML = `<b>${esc(doc ? doc.title : "")} — p. ${esc(p.label)} <a href="/api/documents/${c.dataset.d}/pdf#page=${p.pno}" target="_blank">open PDF ↗</a></b>${esc(p.text)}`;
  } catch (err) { pop.textContent = "Page not found."; }
});

// ---------------------------------------------------------------- navigation
$$("#nav button[data-view]").forEach(b => b.onclick = () => show(b.dataset.view));
function show(v) {
  $$("#nav button[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(s => s.classList.toggle("active", s.id === "view-" + v));
  if (v === "saved") loadOutputs();
  if (v === "chat") loadChats();
  if (v === "settings") loadSettings();
}

// ---------------------------------------------------------------- status & setup
async function pollStatus() {
  try {
    const s = await api("/api/status");
    const m = s.models;
    $("#datadir").textContent = s.data_dir;
    if (!m.ready) {
      $("#setup").classList.remove("hidden");
      $("#modellist").innerHTML = m.models.map(x => `<li>${esc(x.label)} — ${fmtBytes(x.size)} ${x.ready ? "✅" : ""}</li>`).join("");
      const pct = m.total_bytes ? 100 * m.done_bytes / m.total_bytes : 0;
      $("#dlbar").style.width = pct + "%";
      $("#dlgo").disabled = m.running;
      $("#dlgo").textContent = m.running ? "Downloading…" : (m.error ? "Retry download" : "Download models");
      $("#dlmsg").textContent = m.error ? "Error: " + m.error : m.running
        ? `${m.current}: ${fmtBytes(m.done_bytes)} / ${fmtBytes(m.total_bytes)} (${pct.toFixed(1)}%) — ${(m.speed / 1e6).toFixed(1)} MB/s` : "";
    } else {
      if (!$("#setup").classList.contains("hidden")) { $("#setup").classList.add("hidden"); api("/api/runtime/start", { method: "POST" }); }
    }
    const u = s.update || {};
    $("#updatebar").classList.toggle("hidden", !u.available);
    if (u.available) $("#updatever").textContent = u.latest;
    const lowDisk = !m.ready && s.disk_free < 6e9;
    $("#diskwarn").textContent = lowDisk ? `Only ${fmtBytes(s.disk_free)} free on this drive — about 6 GB is needed.` : "";
    const r = s.runtime;
    $("#engine").innerHTML = `v${esc(s.version)} · Engine: <b>${esc(r.status)}</b>${r.chat_backend ? "<br>" + esc(r.chat_backend) : ""}<br>RAM ${r.ram_gb} GB · context ${r.context}${r.ram_gb && r.ram_gb < 7.5 ? `<br><span class="warn">Less than 8 GB RAM: the model may not fit.</span>` : ""}${r.error ? `<br><span class="warn">${esc(r.error.slice(0, 160))}</span>` : ""}`;
    $("#sysinfo").textContent = JSON.stringify(s, null, 2);
  } catch (e) { $("#engine").textContent = "Engine: offline"; }
}
$("#dlgo").onclick = () => api("/api/models/download", { method: "POST" }).then(pollStatus).catch(e => alert(e.message));
$("#updatego").onclick = async () => {
  if (!confirm("Update now? The app will close, update itself (your books, models and settings are kept) and reopen.")) return;
  try { await api("/api/update", { method: "POST" }); document.body.innerHTML = "<p style='padding:40px;font:16px system-ui'>Updating… a window shows progress. The app reopens when done.</p>"; }
  catch (e) { alert(e.message + "\n\nYou can also update by running the install command from the GitHub Releases page."); }
};
$("#quitbtn").onclick = async () => {
  if (!confirm("Quit PoliSci RAG and stop the AI engine?")) return;
  await api("/api/shutdown", { method: "POST" }).catch(() => {});
  document.body.innerHTML = "<p style='padding:40px;font:16px system-ui'>PoliSci RAG has stopped. You can close this tab.</p>";
};

// ---------------------------------------------------------------- jobs
const jobWatchers = {};
async function pollJobs() {
  try {
    const js = await api("/api/jobs");
    const active = js.filter(j => j.status === "running" || j.status === "queued");
    $("#jobsbox").innerHTML = active.map(j => `<div class="jobmini">${esc(j.title)}<br><span class="muted">${esc(j.message)}</span>
      <div class="bar"><div style="width:${(j.progress * 100).toFixed(1)}%"></div></div>
      <button class="ghost" onclick="api('/api/jobs/${j.id}/cancel',{method:'POST'})">cancel</button></div>`).join("");
    const finished = js.filter(j => j.kind === "ingest" || j.kind === "digest").map(j => j.id + j.status).join();
    if (finished !== state.lastJobsSig) { state.lastJobsSig = finished; loadDocs(); }
  } catch {}
}
function watchJob(id, outEl, onDone) {
  clearInterval(jobWatchers[outEl.id]);
  const tick = async () => {
    const j = await api(`/api/jobs/${id}`);
    const progress = `<div class="progress-line">${esc(j.message)} — ${(j.progress * 100).toFixed(0)}%
      ${j.status === "running" || j.status === "queued" ? `<button class="ghost" onclick="api('/api/jobs/${id}/cancel',{method:'POST'})">Cancel</button>` : ""}</div>
      <div class="bar"><div style="width:${(j.progress * 100).toFixed(1)}%"></div></div>`;
    if (j.status === "error") { outEl.innerHTML = `<p class="warn">${esc(j.error)}</p>`; clearInterval(jobWatchers[outEl.id]); return; }
    if (j.status === "done") {
      clearInterval(jobWatchers[outEl.id]);
      outEl.innerHTML = outputToolbar(j.result.output_id) + md(j.output);
      onDone && onDone(j);
      return;
    }
    outEl.innerHTML = progress + md(j.output);
  };
  tick(); jobWatchers[outEl.id] = setInterval(tick, 1500);
}
function outputToolbar(oid) {
  if (!oid) return "";
  return `<div class="toolbar"><a class="ghost" href="/api/outputs/${oid}/export?fmt=docx">⬇ Word (.docx)</a>
    <a class="ghost" href="/api/outputs/${oid}/export?fmt=md">⬇ Markdown</a></div>`;
}

// ---------------------------------------------------------------- library
async function loadDocs() {
  state.docs = await api("/api/documents");
  const el = $("#docs");
  if (!state.docs.length) { el.innerHTML = `<p class="muted">No documents yet.</p>`; }
  else el.innerHTML = `<div class="doclist">` + state.docs.map(d => `
    <div class="docrow">
      <div class="t"><b>D${d.id} · ${esc(d.title)}</b>${d.author ? ` <span class="muted">— ${esc(d.author)}</span>` : ""}<br>
        <span class="badge">${d.pages} pages</span><span class="badge">${d.n_sections} sections</span>
        <span class="badge ${d.status === "ready" ? "ok" : ""}">${esc(d.status)}</span>
        ${d.has_digest ? `<span class="badge ok">digest: ${esc(d.digest_mode)}</span>` : d.n_summarized ? `<span class="badge">digest ${d.n_summarized}/${d.n_sections}</span>` : ""}
        ${d.note ? `<div class="warn">${esc(d.note)}</div>` : ""}</div>
      <button class="ghost" onclick="openDoc(${d.id})">Open</button>
    </div>`).join("") + `</div>`;
  const ready = state.docs.filter(d => d.status === "ready");
  const opts = ready.map(d => `<option value="${d.id}">D${d.id} · ${esc(d.title)}</option>`).join("");
  const keep = $("#revdoc").value; $("#revdoc").innerHTML = opts; if (keep) $("#revdoc").value = keep;
  const checked = new Set($$("#cmpdocs input:checked").map(i => i.value));
  $("#cmpdocs").innerHTML = ready.map(d => `<label><input type="checkbox" value="${d.id}" ${checked.has(String(d.id)) ? "checked" : ""}> D${d.id} · ${esc(d.title.slice(0, 50))}</label>`).join("") || `<span class="muted">Import documents first.</span>`;
  $("#chatscope").innerHTML = ready.map(d => `<label><input type="checkbox" value="${d.id}" ${state.chatScope.has(d.id) ? "checked" : ""}> D${d.id} ${esc(d.title.slice(0, 30))}</label>`).join("") || `<span class="muted">all documents</span>`;
  $$("#chatscope input").forEach(i => i.onchange = () => { i.checked ? state.chatScope.add(+i.value) : state.chatScope.delete(+i.value); });
}
async function openDoc(id) {
  const d = await api(`/api/documents/${id}`);
  const el = $("#docdetail"); el.classList.remove("hidden");
  el.innerHTML = `<h2 style="font-family:Georgia">D${d.id} · ${esc(d.title)}</h2>
    <div class="toolbar">
      <button class="ghost" onclick="renameDoc(${d.id})">Edit title/author</button>
      <a class="ghost" href="/api/documents/${d.id}/pdf" target="_blank">Open PDF</a>
      <button class="ghost" onclick="digest(${d.id},'fast')">Build fast digest</button>
      <button class="ghost" onclick="digest(${d.id},'thorough')">Build thorough digest</button>
      <button class="ghost" onclick="delDoc(${d.id})" style="color:var(--err)">Delete</button>
    </div>
    <p class="muted small">A <b>digest</b> summarises every chapter so the assistant can reason about the whole book. <i>Fast</i> samples each chapter (minutes on a GPU, about an hour on CPU for 1,000 pages). <i>Thorough</i> reads every page (best quality; several hours on CPU). You can cancel and resume anytime.</p>
    ${d.summary ? `<h3>Book overview</h3><div class="doc">${md(d.summary)}</div>` : ""}
    <h3>Sections</h3><div class="sections">${d.sections.map(s => `<details><summary>[${s.idx}] ${esc(s.title)} <span class="muted">pp. ${esc(s.start_label)}–${esc(s.end_label)}</span> ${s.summary ? "✅" : ""}</summary>${s.summary ? md(s.summary) : "<p class='muted'>No digest yet.</p>"}</details>`).join("")}</div>`;
  el.scrollIntoView({ behavior: "smooth" });
}
async function digest(id, mode) { await api(`/api/documents/${id}/digest`, { json: { mode } }); pollJobs(); }
async function delDoc(id) { if (!confirm("Delete this document and its index?")) return; await api(`/api/documents/${id}`, { method: "DELETE" }); $("#docdetail").classList.add("hidden"); loadDocs(); }
async function renameDoc(id) {
  const d = state.docs.find(x => x.id === id);
  const title = prompt("Title", d.title); if (title === null) return;
  const author = prompt("Author(s)", d.author || ""); if (author === null) return;
  await api(`/api/documents/${id}`, { method: "PATCH", json: { title, author } }); loadDocs(); openDoc(id);
}
async function uploadFiles(files) {
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith(".pdf")) continue;
    const r = await fetch(`/api/documents/upload?filename=${encodeURIComponent(f.name)}`, { method: "POST", headers: H, body: f });
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch {} alert(`${f.name}: ${m}`); }
  }
  pollJobs();
}
$("#file").onchange = e => uploadFiles(e.target.files);
const drop = $("#drop");
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); uploadFiles(e.dataTransfer.files); };

// ---------------------------------------------------------------- chat
async function loadChats() {
  const cs = await api("/api/chats");
  $("#chatlist").innerHTML = cs.map(c => `<div class="chatitem ${c.id === state.chatId ? "active" : ""}" onclick="openChat(${c.id})"><span>${esc(c.title)}</span><span onclick="event.stopPropagation();delChat(${c.id})">✕</span></div>`).join("");
}
async function openChat(id) {
  state.chatId = id;
  const ms = await api(`/api/chats/${id}`);
  $("#messages").innerHTML = ms.map(m => msgHTML(m.role, m.content)).join("");
  loadChats(); $("#messages").scrollTop = 1e9;
}
async function delChat(id) { await api(`/api/chats/${id}`, { method: "DELETE" }); if (state.chatId === id) newChat(); loadChats(); }
function newChat() { state.chatId = null; $("#messages").innerHTML = ""; loadChats(); }
$("#newchat").onclick = newChat;
function msgHTML(role, content) {
  if (role === "user") return `<div class="msg user">${esc(content).replace(/\n/g, "<br>")}</div>`;
  return `<div class="msg">${md(content)}<div class="toolbar"><button class="ghost" onclick="exportAnswer(this)">⬇ Word</button><button class="ghost" onclick="navigator.clipboard.writeText(this.closest('.msg').dataset.raw||this.closest('.msg').innerText)">Copy</button></div></div>`;
}
async function exportAnswer(btn) {
  const raw = btn.closest(".msg").dataset.raw || btn.closest(".msg").innerText;
  const r = await fetch("/api/export_text", { method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ title: "Research answer", content: raw }) });
  const blob = await r.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "Research answer.docx"; a.click();
}
const TOOL_LABEL = { search_documents: a => `Searching: “${a.query || ""}”`, read_pages: a => `Reading D${a.document_id} p.${a.start_page}`,
  get_outline: a => `Viewing outline of D${a.document_id}`, get_chapter_summaries: a => `Reading chapter digests of D${a.document_id}`,
  compare_documents: a => `Comparing ${(a.document_ids || []).map(i => "D" + i).join(", ")} on “${a.topic}”`, list_documents: () => "Listing library", ready_to_answer: () => "Evidence gathered" };
$("#chatform").onsubmit = async e => {
  e.preventDefault();
  const text = $("#chatinput").value.trim();
  if (!text || state.sending) return;
  state.sending = true; $("#sendbtn").disabled = true; $("#chatinput").value = "";
  if ($("#messages .empty")) $("#messages").innerHTML = "";
  $("#messages").insertAdjacentHTML("beforeend", msgHTML("user", text));
  const box = document.createElement("div"); box.className = "msg";
  box.innerHTML = `<div class="trace"></div><details class="reasoning hidden"><summary>Model reasoning</summary><pre></pre></details><div class="body"><span class="muted">…</span></div>`;
  $("#messages").appendChild(box);
  let answer = "";
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { ...H, "Content-Type": "application/json" }, body: JSON.stringify({ message: text, chat_id: state.chatId, doc_ids: [...state.chatScope], use_library: $("#uselib").checked }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1); if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "chat") state.chatId = ev.chat_id;
        else if (ev.type === "status") $(".body", box).innerHTML = answer ? md(answer) : `<span class="muted">${esc(ev.text)}</span>`;
        else if (ev.type === "tool") $(".trace", box).insertAdjacentHTML("beforeend", `<div>${esc((TOOL_LABEL[ev.name] || (() => ev.name))(ev.args))}</div>`);
        else if (ev.type === "reasoning") { $(".reasoning", box).classList.remove("hidden"); $(".reasoning pre", box).textContent += ev.text; }
        else if (ev.type === "token") { answer += ev.text; $(".body", box).innerHTML = md(answer); }
        else if (ev.type === "done") { answer = ev.content; box.dataset.raw = answer; $(".body", box).innerHTML = md(answer) + `<div class="toolbar"><button class="ghost" onclick="exportAnswer(this)">⬇ Word</button></div>`; }
        else if (ev.type === "error") $(".body", box).innerHTML = `<p class="warn">${esc(ev.text)}</p>`;
        $("#messages").scrollTop = 1e9;
      }
    }
  } catch (err) { $(".body", box).innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
  state.sending = false; $("#sendbtn").disabled = false; loadChats();
};
$("#chatinput").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chatform").requestSubmit(); } };

// ---------------------------------------------------------------- review / compare
$("#revgo").onclick = async () => {
  const doc_id = +$("#revdoc").value; if (!doc_id) return alert("Import a book first.");
  const j = await api("/api/review", { json: { doc_id, style: $("#revstyle").value, instructions: $("#revinstr").value } });
  watchJob(j.id, $("#revout"));
};
$("#cmpgo").onclick = async () => {
  const doc_ids = $$("#cmpdocs input:checked").map(i => +i.value);
  if (doc_ids.length < 2) return alert("Pick at least two texts.");
  const j = await api("/api/compare", { json: { doc_ids, question: $("#cmpq").value, axes: $("#cmpaxes").value.split("\n") } });
  watchJob(j.id, $("#cmpout"));
};

// ---------------------------------------------------------------- saved
async function loadOutputs() {
  const os = await api("/api/outputs");
  $("#outputs").innerHTML = os.map(o => `<div class="outrow" onclick="viewOutput(${o.id})"><span><span class="badge">${esc(o.kind)}</span> ${esc(o.title)}</span>
    <span class="muted small">${new Date(o.created_at * 1000).toLocaleString()} <button class="ghost" onclick="event.stopPropagation();delOutput(${o.id})">Delete</button></span></div>`).join("") || `<p class="muted">Nothing saved yet.</p>`;
}
async function viewOutput(id) { const o = await api(`/api/outputs/${id}`); $("#outview").innerHTML = outputToolbar(id) + md(o.content); }
async function delOutput(id) { if (!confirm("Delete?")) return; await api(`/api/outputs/${id}`, { method: "DELETE" }); $("#outview").innerHTML = ""; loadOutputs(); }

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const s = await api("/api/settings");
  $$("#settingsform [name]").forEach(i => { if (i.type === "checkbox") i.checked = !!s[i.name]; else i.value = s[i.name] ?? ""; });
}
$("#savesettings").onclick = async () => {
  const body = {};
  $$("#settingsform [name]").forEach(i => body[i.name] = i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value);
  await api("/api/settings", { json: body }); alert("Saved. The engine restarts if needed.");
};

// ---------------------------------------------------------------- boot
loadDocs(); pollStatus(); pollJobs();
setInterval(pollStatus, 2500); setInterval(pollJobs, 2000);
