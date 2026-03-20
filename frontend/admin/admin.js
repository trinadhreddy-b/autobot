const API = (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")
  ? "http://localhost:8000"
  : window.location.origin;

let adminToken = sessionStorage.getItem("admin_token") || "";

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  if (adminToken) {
    showScreen("app");
    loadClients();
  } else {
    showScreen("request");
  }
});

// ── Screen navigation ─────────────────────────────────────────────────────────

function showScreen(name) {
  ["request", "verify", "app"].forEach(s => {
    const el = document.getElementById("screen-" + s);
    if (el) el.classList.toggle("hidden", s !== name);
  });
}

// ── OTP flow ──────────────────────────────────────────────────────────────────

async function requestOTP() {
  const btn = document.getElementById("send-otp-btn");
  hideEl("req-error"); hideEl("req-success");
  btn.disabled = true; btn.textContent = "Sending...";
  try {
    const resp = await fetch(API + "/api/admin/request-otp", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) { showErr("req-error", data.detail || "Failed to send OTP"); return; }
    showEl("req-success");
    document.getElementById("req-success").textContent = "OTP sent! Check your email.";
    setTimeout(() => showScreen("verify"), 1000);
  } catch (e) {
    showErr("req-error", "Network error. Is the server running?");
  } finally {
    btn.disabled = false; btn.textContent = "Send OTP to My Email";
  }
}

async function verifyOTP() {
  const otp = document.getElementById("otp-input").value.trim();
  hideEl("verify-error");
  if (!otp || otp.length !== 6) { showErr("verify-error", "Enter the 6-digit OTP."); return; }
  const btn = document.getElementById("verify-btn");
  btn.disabled = true; btn.textContent = "Verifying...";
  try {
    const resp = await fetch(API + "/api/admin/verify-otp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp }),
    });
    const data = await resp.json();
    if (!resp.ok) { showErr("verify-error", data.detail || "Invalid OTP"); return; }
    adminToken = data.token;
    sessionStorage.setItem("admin_token", adminToken);
    showScreen("app");
    loadClients();
  } catch (e) {
    showErr("verify-error", "Network error.");
  } finally {
    btn.disabled = false; btn.textContent = "Verify & Sign In";
  }
}

function doLogout() {
  adminToken = "";
  sessionStorage.removeItem("admin_token");
  showScreen("request");
}

// ── API helper ────────────────────────────────────────────────────────────────

async function adminFetch(path, opts = {}) {
  const headers = { "Content-Type": "application/json", "Authorization": `Bearer ${adminToken}`, ...(opts.headers || {}) };
  const resp = await fetch(API + path, { ...opts, headers });
  if (resp.status === 401) { doLogout(); return null; }
  return resp;
}

// ── Clients ───────────────────────────────────────────────────────────────────

async function loadClients() {
  const resp = await adminFetch("/api/admin/clients");
  if (!resp) return;
  const data = await resp.json();
  const clients = data.clients || [];
  const heading = document.getElementById("clients-heading");
  heading.textContent = `All Clients (${clients.length})`;
  const tbody = document.getElementById("clients-tbody");
  if (!clients.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">No clients yet. Create one above.</td></tr>`;
    return;
  }
  tbody.innerHTML = clients.map(c => `
    <tr>
      <td><strong>${esc(c.name)}</strong></td>
      <td>${esc(c.email)}</td>
      <td>${esc(c.company || "—")}</td>
      <td style="color:#64748b;font-size:12px">${esc(c.created_at.slice(0,10))}</td>
      <td>${c.must_change_password
        ? '<span class="badge-warn">Must change</span>'
        : '<span class="badge-ok">Changed</span>'}</td>
      <td>
        <button class="btn btn-danger btn-sm" onclick="deleteClient('${c.client_id}', '${esc(c.name)}')">Delete</button>
      </td>
    </tr>
  `).join("");
}

async function createClient() {
  const name     = document.getElementById("c-name").value.trim();
  const email    = document.getElementById("c-email").value.trim();
  const company  = document.getElementById("c-company").value.trim();
  const password = document.getElementById("c-password").value.trim();
  hideEl("create-error"); hideEl("create-success");
  if (!name || !email || !password) { showErr("create-error", "Name, email, and password are required."); return; }
  if (password.length < 8) { showErr("create-error", "Password must be at least 8 characters."); return; }

  const resp = await adminFetch("/api/admin/clients", {
    method: "POST",
    body: JSON.stringify({ name, email, company, password }),
  });
  if (!resp) return;
  const data = await resp.json();
  if (!resp.ok) { showErr("create-error", data.detail || "Failed to create client."); return; }

  showEl("create-success");
  document.getElementById("create-success").textContent = `Client "${name}" created. Share credentials: ${email} / ${password}`;
  document.getElementById("c-name").value = "";
  document.getElementById("c-email").value = "";
  document.getElementById("c-company").value = "";
  document.getElementById("c-password").value = "";
  loadClients();
}

async function deleteClient(clientId, name) {
  if (!confirm(`Delete client "${name}"? This will permanently remove all their chatbots and data.`)) return;
  const resp = await adminFetch(`/api/admin/clients/${clientId}`, { method: "DELETE" });
  if (!resp) return;
  if (!resp.ok) { const d = await resp.json(); showToast("Error: " + (d.detail || "Delete failed")); return; }
  showToast(`Client "${name}" deleted.`);
  loadClients();
}

function generatePassword() {
  const chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$";
  let pwd = "";
  for (let i = 0; i < 12; i++) pwd += chars[Math.floor(Math.random() * chars.length)];
  document.getElementById("c-password").value = pwd;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(str) {
  const d = document.createElement("div");
  d.appendChild(document.createTextNode(String(str ?? "")));
  return d.innerHTML;
}

function showErr(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function hideEl(id) { document.getElementById(id)?.classList.add("hidden"); }
function showEl(id) { document.getElementById(id)?.classList.remove("hidden"); }

function showToast(msg, duration = 3000) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), duration);
}
