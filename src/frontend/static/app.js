"use strict";

// ------------------------------------------------------------------ state

const MAX_MB = 10;
const ACCEPT = [".pdf", ".docx", ".txt", ".md"];

const STEPS = [
  { key: "project", title: "Dự án", sub: "Chọn hoặc tạo dự án" },
  { key: "ingest", title: "Tải tài liệu & tách đoạn", sub: "document_loader · Gemini" },
  { key: "index", title: "Index vào Qdrant", sub: "retrieval.indexer · tuỳ chọn" },
  { key: "extract", title: "Trích requirement", sub: "requirement_extractor · Gemini" },
  { key: "validate", title: "Kiểm tra evidence & lưu", sub: "evidence_validator · registry" },
  { key: "review", title: "Review: approve / reject", sub: "Con người quyết định" },
  { key: "generate", title: "Sinh SRS", sub: "srs_generator · RAG + CAG" },
  { key: "export", title: "Kiểm tra & xuất Markdown", sub: "srs_validator · exporter" },
];

const S = {
  status: null,
  projects: [],
  project: null,          // detail of the selected project
  step: 0,
  results: {},            // step key -> last job result (this session)
  failed: {},             // step key -> error text
  skipped: {},            // step key -> true
  running: null,          // { key, jobId }
  requirements: null,
  reviewFilter: "pending",
  file: null,
  extractDocs: null,      // selected document ids for step 3
  exportView: "preview",
};

// ------------------------------------------------------------------ helpers

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(2)} MB` : `${(n / 1024).toFixed(1)} KB`;
const pct = (x) => x == null ? "—" : `${Math.round(x * 100)}%`;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
});

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${isErr ? " err" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isErr ? 6000 : 3000);
}

function stat(v, k, cls = "") { return `<div class="stat ${cls}"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`; }
function note(cls, html) { return `<div class="note ${cls}">${html}</div>`; }
function list(items) { return `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`; }
function typeBadge(t) { return t === "functional" ? `<span class="badge fr">FR</span>` : `<span class="badge nfr">NFR</span>`; }
function prioBadge(p) { return p ? `<span class="badge ${esc(p)}">${esc(p)}</span>` : ""; }

function highlight(text, quote) {
  if (!text) return `<em>(không tìm thấy passage)</em>`;
  if (!quote) return esc(text);
  const i = text.indexOf(quote);
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + `<mark>${esc(quote)}</mark>` + esc(text.slice(i + quote.length));
}

// ------------------------------------------------------------------ log console

let logHasLines = false;
function logSep(text) {
  const box = $("#log");
  if (!logHasLines) { box.innerHTML = ""; logHasLines = true; }
  box.insertAdjacentHTML("beforeend", `<div class="log-sep">── ${esc(text)} ──</div>`);
  box.scrollTop = box.scrollHeight;
}
function logLines(lines) {
  if (!lines.length) return;
  const box = $("#log");
  if (!logHasLines) { box.innerHTML = ""; logHasLines = true; }
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.insertAdjacentHTML("beforeend", lines.map((l) =>
    `<div class="log-line ${esc(l.level)}"><span class="t">${esc(l.t)}</span> ${esc(l.level.padEnd(7))} <span class="src">${esc(l.source)}</span> ${esc(l.msg)}</div>`).join(""));
  if (nearBottom) box.scrollTop = box.scrollHeight;
}
$("#clearLog").addEventListener("click", () => {
  $("#log").innerHTML = `<div class="log-empty">Log của từng bước sẽ hiện ở đây.</div>`;
  logHasLines = false;
});

// ------------------------------------------------------------------ jobs

async function runJob(key, start) {
  if (S.running) { toast("Đang có bước khác chạy, vui lòng đợi", true); return null; }
  delete S.failed[key];
  let job;
  try {
    job = await start();
  } catch (e) {
    S.failed[key] = e.message; render(); toast(e.message, true); return null;
  }
  S.running = { key, jobId: job.job };
  logSep(`${STEPS.find((s) => s.key === key).title} · ${new Date().toLocaleTimeString()}`);
  render();
  let since = 0;
  while (true) {
    await new Promise((r) => setTimeout(r, 700));
    let v;
    try { v = await api(`/api/jobs/${job.job}?since=${since}`); }
    catch (e) { continue; }
    logLines(v.logs);
    since = v.log_count;
    $("#jobInfo").innerHTML = v.status === "running"
      ? `<span class="spinner"></span> ${esc(v.kind)} · ${v.elapsed}s` : "";
    if (v.status !== "running") {
      S.running = null;
      if (v.status === "done") {
        S.results[key] = v.result;
        delete S.skipped[key];
        toast(`Xong: ${STEPS.find((s) => s.key === key).title} (${v.elapsed}s)`);
      } else {
        S.failed[key] = v.error;
        toast(v.error, true);
      }
      await refreshProject();
      render();
      return v.status === "done" ? v.result : null;
    }
  }
}

// ------------------------------------------------------------------ data

async function loadStatus() {
  try { S.status = await api("/api/status"); } catch (e) { S.status = { error: e.message }; }
  renderStatus();
}

async function loadProjects(selectId) {
  S.projects = await api("/api/projects");
  const sel = $("#projectSelect");
  sel.innerHTML = `<option value="">— chọn dự án —</option>` + S.projects.map((p) =>
    `<option value="${esc(p.id)}">${esc(p.name)} (${esc(p.id)})</option>`).join("");
  const id = selectId ?? S.project?.id ?? localStorageGet("project");
  if (id && S.projects.some((p) => p.id === id)) { sel.value = id; await selectProject(id, false); }
}

async function selectProject(id, resetStep = true) {
  S.results = {}; S.failed = {}; S.skipped = {}; S.requirements = null; S.extractDocs = null; S.file = null;
  if (!id) { S.project = null; S.step = 0; localStorageSet("project", ""); render(); return; }
  S.project = await api(`/api/projects/${encodeURIComponent(id)}`);
  localStorageSet("project", id);
  if (resetStep) S.step = suggestStep();
  render();
}

async function refreshProject() {
  if (!S.project) return;
  try {
    S.project = await api(`/api/projects/${encodeURIComponent(S.project.id)}`);
    if (S.step === 5 || S.requirements) await loadRequirements();
  } catch (e) { toast(e.message, true); }
}

async function loadRequirements() {
  if (!S.project) return;
  S.requirements = await api(`/api/projects/${encodeURIComponent(S.project.id)}/requirements`);
}

function localStorageGet(k) { try { return localStorage.getItem(`spectest.${k}`); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(`spectest.${k}`, v); } catch { /* ignore */ } }

// ------------------------------------------------------------------ step state

function totalReqs() { const r = S.project?.requirements || {}; return (r.pending || 0) + (r.approved || 0) + (r.rejected || 0); }

function isDone(i) {
  const p = S.project;
  if (!p) return false;
  switch (STEPS[i].key) {
    case "project": return true;
    case "ingest": return p.documents.length > 0;
    case "index": return !!S.results.index || !!S.skipped.index;
    case "extract": return !!S.results.extract || totalReqs() > 0;
    case "validate": return totalReqs() > 0;
    case "review": return (p.requirements.approved || 0) > 0;
    case "generate": return p.has_srs;
    case "export": return !!S.results.export?.ok;
  }
  return false;
}

function suggestStep() {
  for (let i = 1; i < STEPS.length; i++) if (!isDone(i)) return i;
  return STEPS.length - 1;
}

// ------------------------------------------------------------------ render: chrome

function renderStatus() {
  const s = S.status;
  if (!s || s.error) { $("#status").innerHTML = `<span class="chip err">Server: ${esc(s?.error || "…")}</span>`; return; }
  $("#status").innerHTML = [
    s.gemini_key ? `<span class="chip ok">Gemini key</span>` : `<span class="chip err" title="Thêm GEMINI_API_KEY vào src/.env">Thiếu GEMINI_API_KEY</span>`,
    s.qdrant.reachable ? `<span class="chip ok">Qdrant ${esc(s.qdrant.host)}:${s.qdrant.port}</span>`
      : `<span class="chip warn" title="Không có Qdrant thì generator dùng mọi passage được trích dẫn">Qdrant offline</span>`,
    s.db_ok ? `<span class="chip ok">SQLite ${esc(s.db)}</span>` : `<span class="chip err" title="${esc(s.db_error)}">DB lỗi</span>`,
  ].join("");
}

function renderSteps() {
  $("#steps").innerHTML = STEPS.map((st, i) => {
    const cls = ["step"];
    if (i === S.step) cls.push("active");
    if (S.running?.key === st.key) cls.push("running");
    else if (S.failed[st.key]) cls.push("failed");
    else if (isDone(i)) cls.push("done");
    const disabled = i > 0 && !S.project;
    const mark = S.running?.key === st.key ? "…" : S.failed[st.key] ? "!" : (isDone(i) && i > 0 ? "✓" : i);
    return `<button type="button" class="${cls.join(" ")}" data-step="${i}" ${disabled ? "disabled" : ""}>
      <span class="step-num">${mark}</span>
      <span><div class="step-title">${esc(st.title)}</div><div class="step-sub">${esc(st.sub)}</div></span></button>`;
  }).join("");
}

$("#steps").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-step]");
  if (!b || b.disabled) return;
  S.step = +b.dataset.step;
  if (STEPS[S.step].key === "review" && !S.requirements) await loadRequirements().catch((err) => toast(err.message, true));
  render();
});

$("#projectSelect").addEventListener("change", (e) => selectProject(e.target.value).catch((err) => toast(err.message, true)));

function head(i, lede, tag) {
  return `<div class="panel-head"><div><h2>${i}. ${esc(STEPS[i].title)}</h2><p class="lede">${lede}</p></div>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}</div>`;
}
function failedNote(key) { return S.failed[key] ? note("err", `<strong>Lỗi:</strong> ${esc(S.failed[key])}`) : ""; }
function runBtn(key, label, action, extra = "") {
  const busy = !!S.running;
  const me = S.running?.key === key;
  return `<button class="btn" type="button" data-action="${action}" ${busy ? "disabled" : ""} ${extra}>${me ? `<span class="spinner"></span> Đang chạy…` : esc(label)}</button>`;
}
function nextBtn(i) {
  if (i >= STEPS.length - 1) return "";
  return `<div class="next"><button class="btn ghost" type="button" data-goto="${i + 1}">Bước tiếp: ${esc(STEPS[i + 1].title)} →</button></div>`;
}

// ------------------------------------------------------------------ render: panels

function render() {
  renderSteps();
  const panel = $("#panel");
  const key = STEPS[S.step].key;
  panel.innerHTML = PANELS[key]();
  if (key === "ingest") bindDropzone();
  if (key === "export") mountMarkdown();
}

const PANELS = {
  project() {
    const p = S.project;
    return head(0, "Mỗi lần chạy pipeline là cho <strong>một dự án</strong>: tài liệu, requirement và SRS đều mang <code>project_id</code> của dự án đó.", "services.project_registry") + `
      ${p ? note("ok", `Đang làm việc với <strong>${esc(p.name)}</strong> <code>${esc(p.id)}</code> — ${p.documents.length} tài liệu, ${totalReqs()} requirement (${p.requirements.approved} approved).`) : ""}
      <h3>Tạo dự án mới</h3>
      <div class="form">
        <div class="field"><label for="pName">Tên dự án</label><input type="text" id="pName" placeholder="Example Support Platform"></div>
        <div class="field"><label for="pDesc">Mô tả (tuỳ chọn)</label><input type="text" id="pDesc"></div>
      </div>
      <div class="actions"><button class="btn" type="button" data-action="create-project">Tạo dự án</button></div>
      <h3>Dự án hiện có</h3>
      ${S.projects.length ? `<div class="table-wrap"><table><thead><tr><th>Id</th><th>Tên</th><th>Tài liệu</th><th>Pending</th><th>Approved</th><th>Rejected</th><th></th></tr></thead><tbody>
        ${S.projects.map((x) => `<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.name)}</td><td>${x.documents}</td>
          <td>${x.requirements.pending}</td><td>${x.requirements.approved}</td><td>${x.requirements.rejected}</td>
          <td><button class="btn ghost small" type="button" data-pick="${esc(x.id)}">${S.project?.id === x.id ? "Đang chọn" : "Chọn"}</button></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">Chưa có dự án nào.</div>`}
      ${p ? nextBtn(0) : ""}`;
  },

  ingest() {
    const r = S.results.ingest;
    const docs = S.project.documents;
    const f = S.file;
    return head(1, `Gemini tách tài liệu thành các đoạn văn (passage); code <strong>kiểm tra từng đoạn có đúng nguyên văn</strong> rồi lưu vào SQLite. File đã ingest trước đó (cùng nội dung) không gọi lại Gemini.`, "services.document_loader") + `
      <label class="dropzone" id="drop">
        <input type="file" id="fileInput" accept="${ACCEPT.join(",")}">
        <div class="big">Kéo thả tài liệu vào đây hoặc bấm để chọn</div>
        <div class="hint">PDF · DOCX · TXT · MD — tối đa ${MAX_MB} MB</div>
      </label>
      ${f ? `<div class="picked"><strong>${esc(f.name)}</strong><span class="tag">${fmtSize(f.size)}</span>
        ${runBtn("ingest", "Tải lên & tách đoạn", "upload")}</div>` : ""}
      ${note("warn", "Nội dung tài liệu sẽ được gửi tới Gemini API. Chỉ dùng tài liệu được phép chia sẻ cho bên thứ ba.")}
      ${failedNote("ingest")}
      ${r ? `<h3>Kết quả: ${esc(r.filename)} <span class="tag">${esc(r.document_id)}</span></h3>
        ${r.skipped ? note("info", "Tài liệu này đã được ingest trước đó — dùng lại passage đã lưu, không gọi Gemini; chỉ liên kết thêm vào dự án.") : ""}
        <div class="stats">${stat(r.passage_count, "passage được lưu", "ok")}${stat(r.rejected.length, "đoạn bị loại (không nguyên văn)", r.rejected.length ? "warn" : "")}
          ${stat(r.unverified, "đoạn chưa kiểm tra được")}${stat(pct(r.coverage), "coverage text gốc")}</div>
        ${r.rejected.length ? `<details><summary>${r.rejected.length} đoạn bị loại</summary>${list(r.rejected.map((x) => `${x.page ? `[tr.${x.page}] ` : ""}${x.text}`))}</details>` : ""}
        <details open><summary>Passage (${r.passages.length})</summary>
          <div class="table-wrap scroll"><table><thead><tr><th>Id</th><th>Trang</th><th>Nội dung</th></tr></thead><tbody>
          ${r.passages.map((p) => `<tr><td class="mono">${esc(p.id.replace(r.document_id, "…"))}</td><td>${p.page ?? ""}</td><td>${esc(p.text)}</td></tr>`).join("")}
          </tbody></table></div></details>` : ""}
      <h3>Tài liệu của dự án (${docs.length})</h3>
      ${docs.length ? `<div class="table-wrap"><table><thead><tr><th>Id</th><th>File</th><th>Passage</th></tr></thead><tbody>
        ${docs.map((d) => `<tr><td class="mono">${esc(d.id)}</td><td>${esc(d.filename)}</td><td>${d.passages}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">Chưa có tài liệu.</div>`}
      ${docs.length ? nextBtn(1) : ""}`;
  },

  index() {
    const r = S.results.index;
    const q = S.status?.qdrant;
    return head(2, `Đưa passage vào Qdrant (vector dense Gemini + BM25) để bước sinh SRS tìm thêm evidence bằng hybrid search. <strong>Tuỳ chọn</strong>: không có Qdrant thì generator dùng mọi passage của tài liệu được trích dẫn.`, "retrieval.indexer") + `
      ${q ? (q.reachable ? note("ok", `Qdrant đang chạy tại ${esc(q.host)}:${q.port}.`) : note("warn", `Không kết nối được Qdrant tại ${esc(q.host)}:${q.port}. Có thể bỏ qua bước này (ví dụ chạy <code>docker run -p 6333:6333 qdrant/qdrant</code> nếu muốn bật).`)) : ""}
      <div class="actions">${runBtn("index", "Chạy index", "index")}
        <button class="btn ghost" type="button" data-action="skip-index" ${S.running ? "disabled" : ""}>Bỏ qua bước này</button></div>
      ${failedNote("index")}
      ${S.skipped.index ? note("info", "Đã bỏ qua bước index.") : ""}
      ${r ? `<div class="stats">${stat(r.in_scope, "passage trong phạm vi")}${stat(r.embedded, "embed mới", "ok")}${stat(r.reused, "dùng lại vector")}
        ${stat(r.unchanged, "không đổi")}${stat(r.payload_updated, "cập nhật payload")}${stat(r.embedding_calls, "text gửi Gemini")}</div>
        <p class="lede">Collection <code>${esc(r.collection)}</code>. Chạy lại bao nhiêu lần cũng được — passage không đổi sẽ được bỏ qua.</p>` : ""}
      ${nextBtn(2)}`;
  },

  extract() {
    const docs = S.project.documents;
    if (S.extractDocs == null) {
      const last = S.results.ingest?.document_id;
      S.extractDocs = last ? [last] : docs.map((d) => d.id);
    }
    const r = S.results.extract;
    return head(3, `Gemini trích functional (FR) và nonfunctional requirement (NFR). Mỗi requirement phải trích <strong>passage id và quote nguyên văn</strong>. Bước này chỉ hiển thị, chưa lưu.`, "agents.requirement_extractor") + `
      ${docs.length ? `<h3>Chọn tài liệu</h3>
        ${docs.map((d) => `<label style="display:block"><input type="checkbox" data-doc="${esc(d.id)}" ${S.extractDocs.includes(d.id) ? "checked" : ""}>
          ${esc(d.filename)} <span class="tag">${esc(d.id)} · ${d.passages} passage</span></label>`).join("")}
        <div class="actions">${runBtn("extract", "Trích requirement", "extract")}</div>` : note("warn", "Dự án chưa có tài liệu — quay lại bước 1.")}
      ${failedNote("extract")}
      ${r ? renderExtract(r) : ""}
      ${r || totalReqs() ? nextBtn(3) : ""}`;
  },

  validate() {
    const r = S.results.validate;
    const drafts = S.results.extract?.requirements?.length;
    return head(4, `Code kiểm tra mọi quote <strong>nằm trong đúng passage được trích</strong> (sai một item là loại cả requirement), rồi lưu phần hợp lệ vào registry với id <code>RQ-…</code> và status <code>pending</code>. Câu trùng trong dự án bị bỏ qua.`, "evidence_validator · requirement_registry") + `
      ${S.project.has_drafts ? note("info", `Có ${drafts ?? ""} requirement vừa trích đang chờ kiểm tra.`) : (r ? "" : note("warn", "Chưa có requirement vừa trích trong phiên này — chạy bước 3 trước."))}
      <div class="actions">${runBtn("validate", "Kiểm tra & lưu", "validate", S.project.has_drafts ? "" : "disabled")}</div>
      ${failedNote("validate")}
      ${r ? `<div class="stats">${stat(r.valid, "evidence hợp lệ", "ok")}${stat(r.invalid.length, "bị loại", r.invalid.length ? "err" : "")}
        ${stat(r.saved_ids.length, "đã lưu (pending)", "ok")}${stat(r.duplicates.length, "trùng, bỏ qua")}</div>
        ${r.saved_ids.length ? note("ok", `Đã lưu: <span class="mono">${esc(r.saved_ids.join(", "))}</span>`) : ""}
        ${r.invalid.length ? `<details open><summary>Requirement bị loại vì evidence sai</summary>${list(r.invalid.map((i) => `${i.statement} → ${i.problems.join("; ")}`))}</details>` : ""}
        ${r.duplicates.length ? `<details><summary>Trùng với requirement đã có</summary>${list(r.duplicates.map((d) => `${d.statement} (= ${d.existing_id})`))}</details>` : ""}` : ""}
      ${totalReqs() ? nextBtn(4) : ""}`;
  },

  review() {
    const reqs = S.requirements;
    const c = S.project.requirements;
    const f = S.reviewFilter;
    const shown = reqs ? reqs.filter((q) => f === "all" || q.status === f) : [];
    return head(5, `Chỉ <strong>con người</strong> approve requirement. Hệ thống chặn approve nếu evidence không hợp lệ. SRS chỉ chứa requirement đã approve.`, "requirement_registry approve / reject") + `
      <div class="actions">
        <button class="btn ok" type="button" data-action="approve-all" ${c.pending ? "" : "disabled"}>Approve tất cả pending (${c.pending})</button>
        <button class="btn ghost" type="button" data-action="reload-reqs">Tải lại</button>
      </div>
      <div class="tabs">${[["pending", "Pending", c.pending], ["approved", "Approved", c.approved], ["rejected", "Rejected", c.rejected], ["all", "Tất cả", totalReqs()]]
        .map(([k, l, n]) => `<button type="button" class="tab ${f === k ? "active" : ""}" data-filter="${k}">${l} (${n})</button>`).join("")}</div>
      ${!reqs ? `<div class="empty"><span class="spinner"></span></div>` : shown.length ? shown.map(renderReq).join("") : `<div class="empty">Không có requirement nào ở mục này.</div>`}
      ${c.approved ? nextBtn(5) : ""}`;
  },

  generate() {
    const r = S.results.generate;
    const c = S.project.requirements;
    return head(6, `Code dựng mục 3, 5, 6 từ requirement đã approve (chép nguyên văn); model chỉ viết văn xuôi (mục 1, 2, 4) và các cặp stimulus/response. Prompt: core context (cache) → requirement → evidence (trích dẫn + hybrid search).`, "agents.srs_generator") + `
      ${c.approved ? note("info", `${c.approved} requirement đã approve sẽ vào SRS.`) : note("warn", "Chưa có requirement nào được approve — quay lại bước 5.")}
      <div class="form">
        <div class="field"><label for="gOrg">Tổ chức</label><input type="text" id="gOrg" placeholder="ExampleCorp" value="${esc(localStorageGet("org") || "")}"></div>
        <div class="field"><label for="gAuthor">Tác giả (cách nhau bằng dấu phẩy)</label><input type="text" id="gAuthor" placeholder="Alice Example" value="${esc(localStorageGet("author") || "")}"></div>
        <div class="field"><label for="gVersion">Phiên bản</label><input type="text" id="gVersion" value="1.0"></div>
      </div>
      <div class="actions">${runBtn("generate", "Sinh SRS", "generate", c.approved ? "" : "disabled")}</div>
      ${failedNote("generate")}
      ${!r && S.project.has_srs ? note("info", "Đã có SRS JSON từ lần chạy trước — có thể sang bước 7 để xuất, hoặc sinh lại.") : ""}
      ${r ? renderGenerate(r) : ""}
      ${S.project.has_srs ? nextBtn(6) : ""}`;
  },

  export() {
    const r = S.results.export;
    return head(7, `<code>srs_validator</code> dò lại nguồn gốc của <strong>từng</strong> FR/NFR trong registry; chỉ khi hợp lệ mới ghi file Markdown (kèm trích dẫn, bảng truy vết và nguyên văn nguồn).`, "srs_validator · exporters.markdown") + `
      ${S.project.has_srs ? "" : note("warn", "Chưa có SRS — chạy bước 6 trước.")}
      <div class="actions">${runBtn("export", "Kiểm tra & xuất .md", "export", S.project.has_srs ? "" : "disabled")}
        ${r?.ok ? `<a class="btn ok" href="/api/download?file=${encodeURIComponent(r.file)}">Tải ${esc(r.file)}</a>` : ""}</div>
      ${failedNote("export")}
      ${r && !r.ok ? note("err", `<strong>SRS không qua kiểm tra, không ghi file:</strong>${list(r.errors)}`) : ""}
      ${r?.ok ? `${note("ok", `Đã ghi <code>${esc(r.path)}</code> (${fmtSize(r.size)}).`)}
        <div class="stats">${stat(r.trace.length, "requirement truy vết được", "ok")}${stat(r.warnings.length, "cảnh báo", r.warnings.length ? "warn" : "")}</div>
        ${r.warnings.length ? `<details><summary>Cảnh báo (${r.warnings.length})</summary>${list(r.warnings)}</details>` : ""}
        <details><summary>Bảng truy vết</summary><div class="table-wrap scroll"><table><thead><tr><th>SRS</th><th>Registry</th><th>Loại</th><th>Statement</th><th>Nguồn</th></tr></thead><tbody>
          ${r.trace.map((t) => `<tr><td class="mono">${esc(t.srs_id)}</td><td class="mono">${esc(t.registry_id || "—")}</td><td>${typeBadge(t.type)}</td><td>${esc(t.statement)}</td><td class="mono">${esc(t.sources.join(", "))}</td></tr>`).join("")}
        </tbody></table></div></details>
        <div class="tabs"><button type="button" class="tab ${S.exportView === "preview" ? "active" : ""}" data-view="preview">Xem trước</button>
          <button type="button" class="tab ${S.exportView === "raw" ? "active" : ""}" data-view="raw">Markdown thô</button></div>
        <div id="mdOut"></div>` : ""}`;
  },
};

function renderExtract(r) {
  const fr = r.requirements.filter((q) => q.type === "functional").length;
  const rejected = r.documents.flatMap((d) => d.rejected.map((x) => `${d.document_id}: ${x.reason}`));
  const errors = r.documents.filter((d) => d.error).map((d) => `${d.document_id}: ${d.error}`);
  return `<div class="stats">${stat(r.requirements.length, "requirement", "ok")}${stat(fr, "functional (FR)")}${stat(r.requirements.length - fr, "nonfunctional (NFR)")}
      ${stat(rejected.length, "item model trả về bị loại", rejected.length ? "warn" : "")}</div>
    ${errors.length ? note("err", `Tài liệu lỗi (bị bỏ qua):${list(errors)}`) : ""}
    ${rejected.length ? `<details><summary>Item bị loại</summary>${list(rejected)}</details>` : ""}
    ${r.requirements.map((q) => `<div class="req"><div class="req-head">${typeBadge(q.type)}${prioBadge(q.priority)}
        <span class="tag">${esc(q.feature || q.nfr_category || "")}</span></div>
      <div class="req-statement">${esc(q.statement)}</div>
      ${q.evidence.map((e) => `<div class="evidence"><span class="src">${esc(e.passage_id)}</span> — “${esc(e.quote)}”</div>`).join("")}</div>`).join("")}`;
}

function renderReq(q) {
  const meta = q.type === "functional"
    ? [q.feature && `Feature: ${q.feature}`, q.stimulus_or_condition && `Khi: ${q.stimulus_or_condition}`, q.response && `Phản hồi: ${q.response}`]
    : [q.nfr_category && `Loại: ${q.nfr_category}`, q.verification_method && `Kiểm chứng: ${q.verification_method}`, q.acceptance_measure && `Tiêu chí: ${q.acceptance_measure}`];
  return `<div class="req ${esc(q.status)}">
    <div class="req-head"><span class="req-id">${esc(q.id)}</span>${typeBadge(q.type)}${prioBadge(q.priority)}<span class="badge ${esc(q.status)}">${esc(q.status)}</span>
      <span class="req-actions">
        ${q.status !== "approved" ? `<button class="btn ok small" type="button" data-approve="${esc(q.id)}" ${q.problems.length ? "disabled title='Evidence không hợp lệ'" : ""}>Approve</button>` : ""}
        ${q.status !== "rejected" ? `<button class="btn danger small" type="button" data-reject="${esc(q.id)}">Reject</button>` : ""}
      </span></div>
    <div class="req-statement">${esc(q.statement)}</div>
    <div class="req-meta">${meta.filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("")}</div>
    ${q.problems.length ? note("err", `Evidence không hợp lệ:${list(q.problems)}`) : ""}
    ${q.evidence.map((e) => `<div class="evidence"><div class="src">${esc(e.passage_id)}${e.page ? ` · trang ${e.page}` : ""}</div>${highlight(e.passage_text, e.quote)}</div>`).join("")}
  </div>`;
}

function renderGenerate(r) {
  const ev = r.evidence || {};
  const cache = r.cache || {};
  const cachedPct = r.tokens.prompt ? Math.round((r.tokens.cached / r.tokens.prompt) * 100) : 0;
  return `<div class="stats">${stat(r.features, "feature")}${stat(r.requirements, "requirement", "ok")}${stat(r.attempts, "lần gọi model")}
      ${stat(ev.sent ?? "—", `passage evidence (${ev.cited ?? 0} trích + ${ev.retrieved ?? 0} tìm thêm)`)}
      ${stat(r.tokens.prompt.toLocaleString(), "token prompt")}${stat(`${cachedPct}%`, "token từ cache")}${stat(r.tokens.output.toLocaleString(), "token output")}</div>
    <p class="lede">Evidence: <code>${esc(ev.mode)}</code> · cache: <code>${esc(cache.mode)}</code>${cache.display_name ? ` (${cache.created ? "tạo mới" : "dùng lại"} <code>${esc(cache.display_name)}</code>)` : ""}
      · core context <code>${esc(r.core_context_version)}</code> · JSON: <a href="/api/download?file=${encodeURIComponent(r.json_file)}">${esc(r.json_file)}</a></p>
    ${r.warnings.length ? note("warn", `<strong>Cảnh báo:</strong>${list(r.warnings)}`) : ""}
    <details><summary>Ánh xạ SRS id → registry id</summary><div class="table-wrap scroll"><table><thead><tr><th>SRS</th><th>Registry</th></tr></thead><tbody>
      ${Object.entries(r.requirement_map).map(([a, b]) => `<tr><td class="mono">${esc(a)}</td><td class="mono">${esc(b)}</td></tr>`).join("")}</tbody></table></div></details>
    <details><summary>SRS JSON</summary><pre class="code">${esc(JSON.stringify(r.srs, null, 2))}</pre></details>`;
}

function mountMarkdown() {
  const r = S.results.export;
  const out = $("#mdOut");
  if (!r?.ok || !out) return;
  if (S.exportView === "raw" || !window.marked) {
    out.innerHTML = `${!window.marked && S.exportView !== "raw" ? note("info", "Không tải được thư viện xem trước (offline) — hiển thị Markdown thô.") : ""}<pre class="code">${esc(r.markdown)}</pre>`;
    return;
  }
  const html = window.marked.parse(r.markdown);
  out.innerHTML = `<div class="md-preview">${window.DOMPurify ? window.DOMPurify.sanitize(html) : esc(r.markdown)}</div>`;
}

// ------------------------------------------------------------------ upload

function bindDropzone() {
  const drop = $("#drop");
  const input = $("#fileInput");
  if (!drop) return;
  input.addEventListener("change", () => pickFile(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
}

function pickFile(file) {
  if (!file) return;
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT.includes(ext)) { toast(`Định dạng ${ext} không được hỗ trợ (chỉ ${ACCEPT.join(", ")})`, true); return; }
  if (file.size > MAX_MB * 1048576) { toast(`File ${fmtSize(file.size)} vượt quá giới hạn ${MAX_MB} MB`, true); return; }
  if (file.size === 0) { toast("File rỗng", true); return; }
  S.file = file;
  render();
}

// ------------------------------------------------------------------ actions

const pid = () => encodeURIComponent(S.project.id);

document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-action],[data-goto],[data-pick],[data-approve],[data-reject],[data-filter],[data-view]");
  if (!t || t.disabled) return;
  try {
    if (t.dataset.goto) { S.step = +t.dataset.goto; if (STEPS[S.step].key === "review") await loadRequirements(); render(); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    if (t.dataset.pick) { $("#projectSelect").value = t.dataset.pick; await selectProject(t.dataset.pick); return; }
    if (t.dataset.filter) { S.reviewFilter = t.dataset.filter; render(); return; }
    if (t.dataset.view) { S.exportView = t.dataset.view; render(); return; }
    if (t.dataset.approve || t.dataset.reject) {
      const id = t.dataset.approve || t.dataset.reject;
      t.disabled = true;
      await post(`/api/requirements/${encodeURIComponent(id)}/${t.dataset.approve ? "approve" : "reject"}`);
      await refreshProject(); await loadRequirements(); render();
      return;
    }
    await ACTIONS[t.dataset.action]?.(t);
  } catch (err) {
    toast(err.message, true);
    render();
  }
});

document.addEventListener("change", (e) => {
  const cb = e.target.closest("[data-doc]");
  if (!cb) return;
  const id = cb.dataset.doc;
  S.extractDocs = cb.checked ? [...new Set([...S.extractDocs, id])] : S.extractDocs.filter((d) => d !== id);
});

const ACTIONS = {
  async "create-project"() {
    const name = $("#pName").value.trim();
    if (!name) { toast("Nhập tên dự án", true); return; }
    const p = await post("/api/projects", { name, description: $("#pDesc").value.trim() });
    toast(`Đã tạo ${p.id}`);
    await loadProjects(p.id);
    S.step = 1; render();
  },
  async upload() {
    const f = S.file;
    if (!f) return;
    await runJob("ingest", async () => {
      const res = await fetch(`/api/projects/${pid()}/upload?filename=${encodeURIComponent(f.name)}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    });
    if (S.results.ingest) { S.file = null; S.extractDocs = null; render(); }
  },
  async index() { await runJob("index", () => post(`/api/projects/${pid()}/index`)); },
  "skip-index"() { S.skipped.index = true; delete S.failed.index; S.step = 3; render(); },
  async extract() {
    if (!S.extractDocs.length) { toast("Chọn ít nhất một tài liệu", true); return; }
    await runJob("extract", () => post(`/api/projects/${pid()}/extract`, { document_ids: S.extractDocs }));
  },
  async validate() { await runJob("validate", () => post(`/api/projects/${pid()}/validate`)); },
  async "approve-all"() {
    const r = await post(`/api/projects/${pid()}/approve-all`);
    toast(`Approved ${r.approved.length}${r.failed.length ? `, ${r.failed.length} bị chặn (evidence sai)` : ""}`, r.failed.length > 0);
    await refreshProject(); await loadRequirements(); render();
  },
  async "reload-reqs"() { await refreshProject(); await loadRequirements(); render(); },
  async generate() {
    const org = $("#gOrg").value.trim(), authors = $("#gAuthor").value.trim();
    localStorageSet("org", org); localStorageSet("author", authors);
    await runJob("generate", () => post(`/api/projects/${pid()}/generate`, {
      organization: org, authors, version: $("#gVersion").value.trim(),
    }));
  },
  async export() { S.exportView = "preview"; await runJob("export", () => post(`/api/projects/${pid()}/export`)); },
};

// ------------------------------------------------------------------ boot

(async function boot() {
  render();
  await loadStatus();
  try { await loadProjects(); } catch (e) { toast(e.message, true); }
  if (S.project) S.step = suggestStep();
  render();
  setInterval(loadStatus, 15000);
})();
