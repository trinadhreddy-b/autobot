/**
 * Multi-Tenant AI Chatbot — Embeddable Widget
 * ============================================
 * Drop-in embed script.  Add this to any webpage:
 *
 *   <script src="https://yourdomain.com/chatbot.js"
 *           data-chatbot-id="YOUR_CHATBOT_ID"
 *           data-api-endpoint="https://yourdomain.com">
 *   </script>
 *
 * The widget auto-initialises on DOMContentLoaded.
 */

(function () {
  "use strict";

  // ── Read config from the script tag ─────────────────────────────────────────
  const scriptTag = document.currentScript ||
    (function () {
      const tags = document.querySelectorAll('script[data-chatbot-id]');
      return tags[tags.length - 1];
    })();

  const CHATBOT_ID   = scriptTag?.getAttribute("data-chatbot-id")  || "";
  const API_ENDPOINT = (scriptTag?.getAttribute("data-api-endpoint") || "http://localhost:8000").replace(/\/$/, "");

  if (!CHATBOT_ID) {
    console.error("[ChatBot] data-chatbot-id is required.");
    return;
  }

  // ── State ────────────────────────────────────────────────────────────────────
  let sessionId    = "session_" + Math.random().toString(36).slice(2);
  let isOpen       = false;
  let isTyping     = false;
  let config       = {
    name:            "Support Assistant",
    welcome_message: "Hello! How can I help you today?",
    color:           "#2563eb",
  };

  // ── Helpers ──────────────────────────────────────────────────────────────────

  function esc(str) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function generateMsgId() {
    return "msg_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7);
  }

  function injectCSS() {
    if (document.getElementById("cb-styles")) return;
    const link = document.createElement("link");
    link.id   = "cb-styles";
    link.rel  = "stylesheet";
    link.href = API_ENDPOINT + "/static/chatbot.css";
    document.head.appendChild(link);
  }

  // ── Fetch chatbot config ─────────────────────────────────────────────────────

  async function loadConfig() {
    try {
      const res = await fetch(`${API_ENDPOINT}/api/chatbot-config/${CHATBOT_ID}`);
      if (res.ok) {
        config = await res.json();
      }
    } catch (e) {
      console.warn("[ChatBot] Could not load config, using defaults.", e);
    }
  }

  // ── Build DOM ────────────────────────────────────────────────────────────────

  function buildWidget() {
    // Bubble button
    const bubble = document.createElement("button");
    bubble.id        = "cb-bubble";
    bubble.className = "cb-bubble";
    bubble.setAttribute("aria-label", "Open chat");
    bubble.style.backgroundColor = config.color;
    bubble.innerHTML = `
      <svg class="cb-icon-chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <svg class="cb-icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
      </svg>`;

    // Chat window
    const win = document.createElement("div");
    win.id        = "cb-window";
    win.className = "cb-window cb-hidden";
    win.setAttribute("role", "dialog");
    win.setAttribute("aria-label", config.name);
    win.innerHTML = `
      <div class="cb-header" style="background:${config.color}">
        <div class="cb-header-info">
          <div class="cb-avatar" style="background:${lightenColor(config.color, 40)}">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
            </svg>
          </div>
          <div>
            <div class="cb-header-name">${esc(config.name)}</div>
            <div class="cb-header-status"><span class="cb-dot"></span> Online</div>
          </div>
        </div>
        <button class="cb-close-btn" aria-label="Close chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div id="cb-messages" class="cb-messages" role="log" aria-live="polite"></div>
      <div id="cb-typing" class="cb-typing-indicator cb-hidden">
        <span></span><span></span><span></span>
      </div>
      <div class="cb-input-area">
        <textarea id="cb-input" class="cb-input" rows="1"
          placeholder="Type a message…" aria-label="Message input"
          maxlength="2000"></textarea>
        <button id="cb-send" class="cb-send-btn" style="background:${config.color}" aria-label="Send">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>
      <div class="cb-footer">Powered by <strong>AI ChatBot Platform</strong></div>`;

    document.body.appendChild(bubble);
    document.body.appendChild(win);
    return { bubble, win };
  }

  // ── Color utilities ──────────────────────────────────────────────────────────

  function lightenColor(hex, amount) {
    hex = hex.replace("#", "");
    if (hex.length === 3) hex = hex.split("").map(c => c + c).join("");
    const num = parseInt(hex, 16);
    const r = Math.min(255, (num >> 16) + amount);
    const g = Math.min(255, ((num >> 8) & 0xff) + amount);
    const b = Math.min(255, (num & 0xff) + amount);
    return `#${[r, g, b].map(v => v.toString(16).padStart(2, "0")).join("")}`;
  }

  // ── Message rendering ─────────────────────────────────────────────────────────

  function addMessage(text, role, id) {
    const msgs = document.getElementById("cb-messages");
    if (!msgs) return;

    const wrapper = document.createElement("div");
    wrapper.className = `cb-msg-wrapper cb-${role}`;
    if (id) wrapper.id = id;

    const bubble = document.createElement("div");
    bubble.className = `cb-msg cb-msg-${role}`;
    if (role === "bot")  bubble.style.setProperty("--cb-accent", config.color);
    if (role === "user") bubble.style.backgroundColor = config.color;

    // Convert newlines to <br>, simple markdown bold
    let html = esc(text)
      .replace(/\n/g, "<br>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>");
    bubble.innerHTML = html;

    const time = document.createElement("div");
    time.className = "cb-msg-time";
    time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    wrapper.appendChild(bubble);
    wrapper.appendChild(time);
    msgs.appendChild(wrapper);
    scrollToBottom();
  }

  function scrollToBottom() {
    const msgs = document.getElementById("cb-messages");
    if (msgs) msgs.scrollTop = msgs.scrollHeight;
  }

  function showTyping(show) {
    const el = document.getElementById("cb-typing");
    if (!el) return;
    if (show) {
      el.classList.remove("cb-hidden");
      scrollToBottom();
    } else {
      el.classList.add("cb-hidden");
    }
    isTyping = show;
  }

  // ── Chat logic ───────────────────────────────────────────────────────────────

  async function sendMessage() {
    const input = document.getElementById("cb-input");
    if (!input) return;
    const text = input.value.trim();
    if (!text || isTyping) return;

    input.value = "";
    resizeInput(input);
    addMessage(text, "user");
    showTyping(true);

    const sendBtn = document.getElementById("cb-send");
    if (sendBtn) sendBtn.disabled = true;

    try {
      const res = await fetch(`${API_ENDPOINT}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          chatbot_id: CHATBOT_ID,
          message:    text,
          session_id: sessionId,
        }),
      });

      showTyping(false);

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        addMessage(err.detail || "Sorry, something went wrong. Please try again.", "bot");
        return;
      }

      const data = await res.json();
      addMessage(data.answer, "bot");

    } catch (e) {
      showTyping(false);
      addMessage("I'm temporarily unavailable. Please try again later.", "bot");
      console.error("[ChatBot] Network error:", e);
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  // ── Input auto-resize ─────────────────────────────────────────────────────────

  function resizeInput(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }

  // ── Toggle open/close ─────────────────────────────────────────────────────────

  function toggleWindow() {
    isOpen = !isOpen;
    const win    = document.getElementById("cb-window");
    const bubble = document.getElementById("cb-bubble");
    if (!win || !bubble) return;

    if (isOpen) {
      win.classList.remove("cb-hidden");
      win.classList.add("cb-visible");
      bubble.classList.add("cb-open");
      bubble.querySelector(".cb-badge")?.remove();
      document.getElementById("cb-input")?.focus();
    } else {
      win.classList.remove("cb-visible");
      win.classList.add("cb-hidden");
      bubble.classList.remove("cb-open");
    }
  }

  // ── Event wiring ──────────────────────────────────────────────────────────────

  function bindEvents() {
    const bubble  = document.getElementById("cb-bubble");
    const closeBtn = document.querySelector(".cb-close-btn");
    const sendBtn = document.getElementById("cb-send");
    const input   = document.getElementById("cb-input");

    bubble?.addEventListener("click", toggleWindow);
    closeBtn?.addEventListener("click", toggleWindow);
    sendBtn?.addEventListener("click", sendMessage);

    input?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    input?.addEventListener("input", () => resizeInput(input));

    // Close on outside click
    document.addEventListener("click", (e) => {
      const win    = document.getElementById("cb-window");
      const bubble = document.getElementById("cb-bubble");
      if (isOpen && win && bubble &&
          !win.contains(e.target) && !bubble.contains(e.target)) {
        toggleWindow();
      }
    });
  }

  // ── Boot ──────────────────────────────────────────────────────────────────────

  async function init() {
    injectCSS();
    await loadConfig();
    buildWidget();
    bindEvents();

    // Show welcome message after short delay
    setTimeout(() => {
      addMessage(config.welcome_message, "bot");
    }, 600);

    // Show a subtle notification badge after 3s to invite opening
    setTimeout(() => {
      const bubble = document.getElementById("cb-bubble");
      if (bubble && !isOpen) {
        const badge = document.createElement("span");
        badge.className = "cb-badge";
        badge.textContent = "1";
        bubble.appendChild(badge);
      }
    }, 3000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

})();
