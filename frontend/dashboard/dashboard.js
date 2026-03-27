/**
 * Dashboard JavaScript
 */

const API = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
  ? "http://localhost:8000"
  : window.location.origin;

// â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let authToken     = localStorage.getItem("cb_token")   || "";
let authClientId  = localStorage.getItem("cb_client")  || "";
let authName      = localStorage.getItem("cb_name")    || "";
let authEmail     = localStorage.getItem("cb_email")   || "";
let currentBotId  = "";
let logsPage      = 0;
let leadsPage     = 0;
const PAGE_SIZE   = 20;

// â”€â”€ Boot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.addEventListener("DOMContentLoaded", () => {
  if (authToken) {
    showApp();
  } else {
    showAuth();
  }

  const uploadArea = document.getElementById("upload-area");
  if (uploadArea) {
    uploadArea.addEventListener("dragover",  e => { e.preventDefault(); uploadArea.classList.add("drag-over"); });
    uploadArea.addEventListener("dragleave", () => uploadArea.classList.remove("drag-over"));
    uploadArea.addEventListener("drop", e => {
      e.preventDefault();
      uploadArea.classList.remove("drag-over");
      handleFileUpload({ target: { files: e.dataTransfer.files } });
    });
    uploadArea.addEventListener("click", () => document.getElementById("file-input").click());
  }

  document.getElementById("setting-color")?.addEventListener("input", e => {
    document.getElementById("setting-color-hex").value = e.target.value;
  });
  document.getElementById("setting-color-hex")?.addEventListener("input", e => {
    const v = e.target.value;
    if (/^#[0-9a-fA-F]{6}$/.test(v)) document.getElementById("setting-color").value = v;
  });
});

// â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function esc(str) {
  const d = document.createElement("div");
  d.appendChild(document.createTextNode(String(str)));
  return d.innerHTML;
}

function showToast(msg, duration = 2800) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), duration);
}

async function apiFetch(path, opts = {}) {
  const isFormData = opts.body instanceof FormData;
  const headers = isFormData
    ? { ...(opts.headers || {}) }
    : { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const resp = await fetch(API + path, { ...opts, headers });
  if (resp.status === 401) { doLogout(); return null; }
  return resp;
}

function setLoading(btn, loading) {
  if (!btn) return;
  btn.disabled = loading;
  btn._origText = btn._origText || btn.textContent;
  btn.textContent = loading ? "Loading..." : btn._origText;
}

// â”€â”€ Auth â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function showAuth()  { show("auth-screen"); hide("app"); showLogin(); }
function showApp()   { hide("auth-screen"); show("app"); initApp(); }
function showLogin() { show("login-form"); hide("login-error"); }

async function doLogin() {
  const email    = val("login-email");
  const password = val("login-password");
  hide("login-error");
  if (!email || !password) { showErr("login-error", "Please fill in all fields."); return; }

  let resp, data;
  try {
    resp = await fetch(API + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    data = await resp.json();
  } catch (e) {
    showErr("login-error", "Network error. Please try again."); return;
  }
  if (!resp.ok) { showErr("login-error", data.detail || "Login failed."); return; }

  saveAuth(data);
  if (data.must_change_password) {
    showApp();
    openChangePassword(true);  // forced — no cancel
  } else {
    showApp();
  }
}

function saveAuth(data) {
  authToken    = data.token;
  authClientId = data.client_id;
  authName     = data.name     || "";
  authEmail    = data.email    || "";
  localStorage.setItem("cb_token",  authToken);
  localStorage.setItem("cb_client", authClientId);
  localStorage.setItem("cb_name",   authName);
  localStorage.setItem("cb_email",  authEmail);
}

function doLogout() {
  authToken = authClientId = authName = authEmail = "";
  localStorage.clear();
  showAuth();
}

// ── Change Password ──────────────────────────────────────────────────────────

function openChangePassword(forced = false) {
  byId("cp-old").value = "";
  byId("cp-new").value = "";
  byId("cp-confirm").value = "";
  hide("change-pw-error");
  const notice = byId("change-pw-notice");
  const cancelBtn = byId("cp-cancel-btn");
  if (forced) {
    show(notice); cancelBtn.classList.add("hidden");
  } else {
    hide(notice); cancelBtn.classList.remove("hidden");
  }
  show("change-password-overlay");
}

function closeChangePassword() {
  hide("change-password-overlay");
}

async function doChangePassword() {
  const oldPw  = byId("cp-old").value;
  const newPw  = byId("cp-new").value;
  const confirm = byId("cp-confirm").value;
  hide("change-pw-error");
  if (!oldPw || !newPw || !confirm) { showErr("change-pw-error", "All fields are required."); return; }
  if (newPw.length < 8) { showErr("change-pw-error", "New password must be at least 8 characters."); return; }
  if (newPw !== confirm) { showErr("change-pw-error", "New passwords do not match."); return; }

  const resp = await apiFetch("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
  });
  if (!resp) return;
  const data = await resp.json();
  if (!resp.ok) { showErr("change-pw-error", data.detail || "Failed to change password."); return; }
  closeChangePassword();
  showToast("Password updated successfully.");
}

// â”€â”€ App init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function initApp() {
  byId("user-name-display").textContent  = authName  || "-";
  byId("user-email-display").textContent = authEmail || "-";
  byId("user-avatar").textContent        = (authName || "U")[0].toUpperCase();
  showSection("chatbots");
}

function showSection(name) {
  ["chatbots","detail","analytics"].forEach(s => hide("section-" + s));
  show("section-" + name);
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  const navBtn = byId("nav-" + name);
  if (navBtn) navBtn.classList.add("active");

  if (name === "chatbots")  loadChatbots();
  if (name === "analytics") loadAnalyticsBotList();
}

// â”€â”€ Chatbots â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadChatbots() {
  const resp = await apiFetch("/api/chatbots");
  if (!resp) return;
  const data = await resp.json();
  const bots = data.chatbots || [];
  const grid = byId("chatbots-grid");
  const empty= byId("chatbots-empty");

  grid.innerHTML = "";
  if (bots.length === 0) { hide(grid); show(empty); return; }
  show(grid); hide(empty);

  bots.forEach(bot => {
    const card = document.createElement("div");
    card.className = "chatbot-card";
    card.innerHTML = `
      <div class="card-top">
        <div class="card-dot" style="background:${esc(bot.color||'#2563eb')}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </div>
        <div>
          <div class="card-name">${esc(bot.name)}</div>
          <div class="card-id">ID: ${esc(bot.chatbot_id)}</div>
        </div>
      </div>
      <div class="card-stats">
        <div class="stat">
          <span class="stat-value">${bot.doc_count || 0}</span>
          <span class="stat-label">Documents</span>
        </div>
        <div class="stat">
          <span class="stat-value">${bot.message_count || 0}</span>
          <span class="stat-label">Messages</span>
        </div>
      </div>
      <div class="card-actions">
        <button class="btn btn-primary btn-sm" onclick="openDetail('${esc(bot.chatbot_id)}')">Manage</button>
        <button class="btn btn-secondary btn-sm" onclick="quickCopyEmbed('${esc(bot.chatbot_id)}',event)">Copy Embed</button>
      </div>`;
    grid.appendChild(card);
  });
}

async function quickCopyEmbed(chatbotId, event) {
  event.stopPropagation();
  const resp = await apiFetch(`/api/embed-code/${chatbotId}`);
  if (!resp) return;
  const data = await resp.json();
  await navigator.clipboard.writeText(data.embed_code).catch(() => {});
  showToast("Embed code copied!");
}

// â”€â”€ Create chatbot modal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function openCreateModal() {
  hide("modal-error");
  val("modal-bot-name", "");
  val("modal-welcome", "Hello! How can I help you today?");
  show("modal-overlay");
  byId("modal-bot-name").focus();
}

function closeModal() { hide("modal-overlay"); }

async function doCreateChatbot() {
  const name    = val("modal-bot-name").trim();
  const welcome = val("modal-welcome").trim();
  const color   = val("modal-color");
  hide("modal-error");

  if (!name) { showErr("modal-error", "Please enter a chatbot name."); return; }

  const resp = await apiFetch("/api/chatbots", {
    method: "POST",
    body: JSON.stringify({ name, welcome_message: welcome, color }),
  });
  if (!resp) return;
  const data = await resp.json();
  if (!resp.ok) { showErr("modal-error", data.detail || "Failed to create chatbot."); return; }

  closeModal();
  showToast(`Chatbot "${name}" created!`);
  loadChatbots();
}

// â”€â”€ Chatbot detail â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function openDetail(chatbotId) {
  currentBotId = chatbotId;
  hide("section-chatbots");
  show("section-detail");
  switchTab("docs", document.querySelector("#section-detail .tab"));
  await loadDocuments();
}

function backToChatbots() {
  currentBotId = "";
  hide("section-detail");
  show("section-chatbots");
  loadChatbots();
}

function switchTab(name, btn) {
  ["docs","embed","logs","leads","settings"].forEach(t => hide("tab-" + t));
  show("tab-" + name);
  document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");

  if (name === "embed")    loadEmbedCode();
  if (name === "logs")     { logsPage = 0; loadLogs(); }
  if (name === "leads")    { leadsPage = 0; loadLeads(); }
  if (name === "settings") loadSettings();
}

// â”€â”€ Documents â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadDocuments() {
  const resp = await apiFetch(`/api/chatbots/${currentBotId}/documents`);
  if (!resp) return;
  const data = await resp.json();
  const list = byId("docs-list");
  list.innerHTML = "";

  (data.documents || []).forEach(doc => {
    const badgeCls  = doc.status === "ready" ? "badge-green" : doc.status === "failed" ? "badge-red" : "badge-yellow";
    const icon = fileIcon(doc.filename);
    const row  = document.createElement("div");
    row.className = "doc-row";
    row.innerHTML = `
      <div class="doc-icon">${icon}</div>
      <div class="doc-info">
        <div class="doc-name">${esc(doc.filename)}</div>
        <div class="doc-meta">${doc.chunk_count || 0} chunks · ${fmtDate(doc.created_at)}</div>
      </div>
      <span class="badge ${badgeCls}">${esc(doc.status)}</span>
      <button class="btn btn-ghost btn-sm" onclick="deleteDoc('${esc(doc.doc_id)}')">🗑</button>`;
    list.appendChild(row);
  });

  if (!data.documents?.length) {
    list.innerHTML = "<p style='color:#94a3b8;padding:12px 0'>No documents yet. Upload files above.</p>";
  }
}

async function handleFileUpload(event) {
  const files = Array.from(event.target.files || []);
  for (const file of files) {
    const form = new FormData();
    form.append("chatbot_id", currentBotId);
    form.append("file", file);
    showToast(`Uploading ${file.name}...`);
    const resp = await apiFetch("/api/upload-document", { method: "POST", body: form });
    if (resp?.ok) {
      showToast(`${file.name} is being processed`);
    } else if (resp) {
      const err = await resp.json();
      showToast(`Error: ${err.detail || "Upload failed"}`);
    }
  }
  setTimeout(loadDocuments, 1500);
}

async function handleUrlIngest() {
  const url = val("url-input").trim();
  if (!url) return;
  const form = new FormData();
  form.append("chatbot_id", currentBotId);
  form.append("url", url);
  showToast("Adding URL...");
  const resp = await apiFetch("/api/ingest-url", { method: "POST", body: form });
  if (resp?.ok) {
    val("url-input", "");
    showToast("URL added - processing in background");
    setTimeout(loadDocuments, 2000);
  } else if (resp) {
    const err = await resp.json();
    showToast(`Error: ${err.detail || "Failed"}`);
  }
}

async function deleteDoc(docId) {
  if (!confirm("Delete this document and its vectors?")) return;
  const resp = await apiFetch(`/api/chatbots/${currentBotId}/documents/${docId}`, { method: "DELETE" });
  if (resp?.ok) { showToast("Document deleted"); loadDocuments(); }
}

async function rebuildKnowledgeBase() {
  if (!confirm("This will wipe and re-index the entire knowledge base. Continue?")) return;
  const resp = await apiFetch(`/api/chatbots/${currentBotId}/rebuild-knowledge-base`, { method: "POST" });
  if (resp?.ok) {
    const data = await resp.json();
    showToast(data.message || "Rebuild started");
    setTimeout(loadDocuments, 2000);
  }
}

// â”€â”€ Embed code â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadEmbedCode() {
  const resp = await apiFetch(`/api/embed-code/${currentBotId}`);
  if (!resp) return;
  const data = await resp.json();
  byId("embed-code-block").textContent = data.embed_code;
  byId("demo-link").href = `${API}/widget-demo?chatbot_id=${currentBotId}`;
}

async function copyEmbedCode() {
  const code = byId("embed-code-block").textContent;
  await navigator.clipboard.writeText(code).catch(() => {});
  showToast("Embed code copied to clipboard!");
}

// â”€â”€ Logs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadLogs() {
  const offset = logsPage * PAGE_SIZE;
  const resp   = await apiFetch(`/api/chatbots/${currentBotId}/logs?limit=${PAGE_SIZE}&offset=${offset}`);
  if (!resp) return;
  const data  = await resp.json();
  const tbody = byId("logs-tbody");
  tbody.innerHTML = "";

  (data.logs || []).forEach(log => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtDate(log.created_at)}</td>
      <td><code>${esc(log.session_id?.slice(0,8))}...</code></td>
      <td title="${esc(log.user_message)}">${esc(truncate(log.user_message, 60))}</td>
      <td title="${esc(log.bot_response)}">${esc(truncate(log.bot_response, 80))}</td>
      <td><span class="badge badge-green">${esc(log.provider || '-')}</span></td>`;
    tbody.appendChild(tr);
  });

  if (!data.logs?.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#94a3b8;padding:20px">No messages yet.</td></tr>`;
  }

  const pages = byId("logs-pagination");
  pages.innerHTML = "";
  const total = data.total || 0;
  if (total > PAGE_SIZE) {
    const totalPages = Math.ceil(total / PAGE_SIZE);
    for (let p = 0; p < totalPages; p++) {
      const b = document.createElement("button");
      b.className = "btn btn-sm " + (p === logsPage ? "btn-primary" : "btn-ghost");
      b.textContent = p + 1;
      b.onclick = () => { logsPage = p; loadLogs(); };
      pages.appendChild(b);
    }
  }
}

// â”€â”€ Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadSettings() {
  const resp = await apiFetch(`/api/chatbots/${currentBotId}`);
  if (!resp) return;
  const bot = await resp.json();
  val("setting-name",    bot.name || "");
  val("setting-welcome", bot.welcome_message || "");
  val("setting-color",   bot.color || "#2563eb");
  val("setting-color-hex", bot.color || "#2563eb");
  val("setting-domains", (bot.allowed_domains || []).join("\n"));
  const leadToggle = document.getElementById("setting-lead-form");
  if (leadToggle) leadToggle.checked = !!bot.lead_form_enabled;
  // Icon settings
  const iconType = bot.icon_type || "default";
  const radio = document.querySelector(`input[name="icon-type"][value="${iconType}"]`);
  if (radio) { radio.checked = true; updateIconRows(iconType); }
  if (iconType === "image" && bot.icon_value) { setIconPreview(bot.icon_value); }
}

function updateIconRows(type) {
  document.getElementById("icon-image-row").style.display = type === "image" ? "flex" : "none";
}

function setIconPreview(url) {
  const preview = document.getElementById("icon-preview");
  if (!preview) return;
  preview.src = url;
  preview.style.display = "block";
}

async function uploadChatbotIcon(input) {
  const file = input.files[0];
  if (!file) return;
  const status = document.getElementById("icon-upload-status");
  status.textContent = "Uploading…";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const resp = await apiFetch(`/api/chatbots/${currentBotId}/icon`, { method: "POST", body: formData });
    if (resp?.ok) {
      const data = await resp.json();
      setIconPreview(data.url + "?v=" + Date.now());
      status.textContent = "Icon uploaded successfully.";
    } else {
      status.textContent = "Upload failed. Please try again.";
    }
  } catch (e) {
    status.textContent = "Upload error: " + e.message;
  }
}

// Wire up radio buttons once DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("input[name='icon-type']").forEach(r => {
    r.addEventListener("change", () => updateIconRows(r.value));
  });
});

async function saveSettings() {
  const name    = val("setting-name").trim();
  const welcome = val("setting-welcome").trim();
  const color   = val("setting-color-hex").trim() || val("setting-color");
  const allowed_domains = val("setting-domains").split("\n").map(d => d.trim().toLowerCase()).filter(Boolean);
  const lead_form_enabled = document.getElementById("setting-lead-form")?.checked ? 1 : 0;
  const icon_type  = document.querySelector("input[name='icon-type']:checked")?.value || "default";
  const icon_value = icon_type === "image"
    ? (document.getElementById("icon-preview")?.src?.split("?")[0]?.replace(location.origin, "") || "")
    : "";
  const resp    = await apiFetch(`/api/chatbots/${currentBotId}`, {
    method: "PUT",
    body:   JSON.stringify({ name, welcome_message: welcome, color, allowed_domains, lead_form_enabled, icon_type, icon_value }),
  });
  if (resp?.ok) { showToast("Settings saved!"); }
  else          { showToast("Failed to save settings."); }
}

// ── Leads ─────────────────────────────────────────────────────────────────────

async function loadLeads() {
  const offset = leadsPage * PAGE_SIZE;
  const resp   = await apiFetch(`/api/chatbots/${currentBotId}/leads?limit=${PAGE_SIZE}&offset=${offset}`);
  if (!resp) return;
  if (!resp.ok) { showToast(`Failed to load leads (${resp.status})`); return; }
  const data  = await resp.json();
  const tbody = byId("leads-tbody");
  tbody.innerHTML = "";

  const heading = byId("leads-count-heading");
  if (heading) heading.textContent = `${data.total || 0} Lead${(data.total || 0) !== 1 ? "s" : ""} Captured`;

  (data.leads || []).forEach(lead => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtDate(lead.created_at)}</td>
      <td><code>${esc((lead.session_id || "").slice(0, 8))}...</code></td>
      <td>${esc(lead.name || "—")}</td>
      <td>${esc(lead.mobile)}</td>
      <td>${esc(lead.email)}</td>
      <td title="${esc(lead.requirement)}">${esc(truncate(lead.requirement, 60))}</td>`;
    tbody.appendChild(tr);
  });

  if (!(data.leads || []).length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:20px">No leads captured yet.</td></tr>`;
  }

  const pages = byId("leads-pagination");
  pages.innerHTML = "";
  const total = data.total || 0;
  if (total > PAGE_SIZE) {
    const totalPages = Math.ceil(total / PAGE_SIZE);
    for (let p = 0; p < totalPages; p++) {
      const b = document.createElement("button");
      b.className = "btn btn-sm " + (p === leadsPage ? "btn-primary" : "btn-ghost");
      b.textContent = p + 1;
      b.onclick = () => { leadsPage = p; loadLeads(); };
      pages.appendChild(b);
    }
  }
}

async function exportLeadsCSV() {
  const resp = await apiFetch(`/api/chatbots/${currentBotId}/leads?limit=10000&offset=0`);
  if (!resp) return;
  const data  = await resp.json();
  const leads = data.leads || [];
  if (!leads.length) { showToast("No leads to export."); return; }

  const header = ["Date", "Session ID", "Name", "Mobile", "Email", "Requirement"];
  const rows   = leads.map(l => [
    fmtDate(l.created_at), l.session_id, l.name || "", l.mobile, l.email,
    l.requirement.replace(/\n/g, " "),
  ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(","));

  const csv  = [header.join(","), ...rows].join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `leads_${currentBotId}_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast("Leads exported!");
}

async function confirmDeleteChatbot() {
  if (!confirm("Permanently delete this chatbot and ALL its data? This cannot be undone.")) return;
  const resp = await apiFetch(`/api/chatbots/${currentBotId}`, { method: "DELETE" });
  if (resp?.ok) { showToast("Chatbot deleted"); backToChatbots(); }
  else          { showToast("Failed to delete chatbot."); }
}

// â”€â”€ Analytics â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadAnalyticsBotList() {
  const resp = await apiFetch("/api/chatbots");
  if (!resp) return;
  const data   = await resp.json();
  const select = byId("analytics-bot-select");
  select.innerHTML = "<option value=''>Select chatbot...</option>";
  (data.chatbots || []).forEach(bot => {
    const opt = document.createElement("option");
    opt.value = bot.chatbot_id;
    opt.textContent = bot.name;
    select.appendChild(opt);
  });
}

async function loadAnalytics() {
  const botId = byId("analytics-bot-select").value;
  if (!botId) return;
  const resp  = await apiFetch(`/api/chatbots/${botId}/analytics`);
  if (!resp)  return;
  const data  = await resp.json();

  byId("stats-grid").innerHTML = `
    <div class="stat-card"><div class="big">${data.total_messages}</div><div class="lbl">Total Messages</div></div>
    <div class="stat-card"><div class="big">${data.unique_sessions}</div><div class="lbl">Unique Sessions</div></div>
    <div class="stat-card"><div class="big">${data.documents}</div><div class="lbl">Documents</div></div>
    <div class="stat-card"><div class="big">${data.total_leads ?? 0}</div><div class="lbl">Leads Captured</div></div>`;

  const daily = data.daily_messages || [];
  const chartCard = byId("daily-chart-card");
  chartCard.style.display = daily.length ? "" : "none";
  if (daily.length) {
    const maxCount = Math.max(...daily.map(d => d.count), 1);
    const existing = chartCard.querySelector(".chart-container");
    if (existing) existing.remove();
    const chartDiv = document.createElement("div");
    chartDiv.className = "chart-container";
    chartDiv.style.marginTop = "12px";
    daily.slice(0, 15).forEach(d => {
      const pct = Math.round((d.count / maxCount) * 100);
      chartDiv.innerHTML += `
        <div class="chart-bar-row">
          <span class="chart-day">${d.day}</span>
          <div class="chart-bar-bg"><div class="chart-bar-fill" style="width:${pct}%"></div></div>
          <span class="chart-count">${d.count}</span>
        </div>`;
    });
    chartCard.appendChild(chartDiv);
  }

  const providers = data.providers || {};
  const pCard = byId("providers-card");
  pCard.style.display = Object.keys(providers).length ? "" : "none";
  if (Object.keys(providers).length) {
    const maxP = Math.max(...Object.values(providers), 1);
    byId("providers-list").innerHTML = Object.entries(providers).map(([name, count]) => `
      <div class="provider-row">
        <span class="provider-name">${esc(name)}</span>
        <div class="provider-bar">
          <div class="provider-fill" style="width:${Math.round(count/maxP*100)}%"></div>
        </div>
        <span class="provider-cnt">${count}</span>
      </div>`).join("");
  }
}

// â”€â”€ DOM helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function byId(id)    { return document.getElementById(id); }
function show(el)    { (typeof el==="string"?byId(el):el)?.classList.remove("hidden"); }
function hide(el)    { (typeof el==="string"?byId(el):el)?.classList.add("hidden"); }

function val(id, set) {
  const el = byId(id);
  if (!el) return "";
  if (set !== undefined) { el.value = set; return set; }
  return el.value;
}

function showErr(id, msg) {
  const el = byId(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function fileIcon(name) {
  const ext = (name || "").split(".").pop().toLowerCase();
  const map = { pdf: "PDF", docx: "DOC", doc: "DOC", txt: "TXT", md: "MD" };
  return map[ext] || "FILE";
}

function fmtDate(iso) {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function truncate(str, n) {
  if (!str) return "";
  return str.length > n ? str.slice(0, n) + "..." : str;
}

function toggleSidebar() {
  byId("sidebar").classList.toggle("open");
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeModal(); }
});
