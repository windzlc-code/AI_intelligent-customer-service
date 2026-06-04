const state = {
  bot: null,
  users: [],
  admins: [],
  conversations: [],
  selectedConversationId: null,
  conversationSignature: "",
  messageSignature: "",
};

const titles = {
  overview: ["概览", "单个 Telegram Bot，同时服务用户端和人工端。"],
  bot: ["Bot 配置", "只需要保存 Bot Token，Webhook 信息由系统自动处理。"],
  users: ["用户 ID", "只有添加到这里的 Telegram ID 才能使用用户端。"],
  admins: ["管理员 ID", "只有添加到这里的 Telegram ID 才能使用 /admin 人工端。"],
  conversations: ["会话记录", "查看用户进入人工模式后的消息记录。"],
};

function el(id) { return document.getElementById(id); }
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}

function formatTimestamp(value) {
  const seconds = Number(value || 0);
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    cache: "no-store",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options
  });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("unauthorized");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
  return data;
}

function readTelegramId(inputId) {
  const raw = el(inputId).value.trim();
  if (!/^[1-9]\d*$/.test(raw)) {
    throw new Error("请输入有效的 Telegram ID，必须是大于 0 的整数。");
  }
  return Number(raw);
}

function setPage(page) {
  if (!titles[page]) page = "overview";
  document.querySelectorAll(".nav button").forEach((btn) => btn.classList.toggle("active", btn.dataset.page === page));
  document.querySelectorAll(".page").forEach((node) => node.classList.toggle("active", node.id === `page-${page}`));
  el("pageTitle").textContent = titles[page][0];
  el("pageSubtitle").textContent = titles[page][1];
  location.hash = page;
  if (page === "conversations") {
    refreshConversations().catch((err) => console.error(err));
  }
}

async function loadAll() {
  const [me, bot, users, admins, conversations] = await Promise.all([
    api("/api/me"),
    api("/api/admin/bot-config"),
    api("/api/admin/users"),
    api("/api/admin/admins"),
    api("/api/admin/conversations"),
  ]);
  el("meLabel").textContent = `${me.username} / ${me.role}`;
  Object.assign(state, {bot, users, admins, conversations});
  render();
}

function render() {
  el("kpiUsers").textContent = state.users.length;
  el("kpiAdmins").textContent = state.admins.length;
  el("kpiConversations").textContent = state.conversations.length;
  renderBot();
  renderUsers();
  renderAdmins();
  renderConversations();
}

function renderBot() {
  if (el("bot_token")) {
    el("bot_token").value = "";
    el("bot_token").placeholder = state.bot?.bot_token_masked || "123456:ABC...";
  }
  if (el("handoff_timeout_minutes")) {
    el("handoff_timeout_minutes").value = Number(state.bot?.handoff_timeout_minutes || 30);
  }
  if (el("conversation_retention_days")) {
    el("conversation_retention_days").value = Number(state.bot?.conversation_retention_days ?? 30);
  }
}

function renderUsers() {
  el("usersBody").innerHTML = state.users.map((item) => `
    <tr>
      <td><code>${esc(item.telegram_id)}</code></td>
      <td>${esc(item.remark_name)}</td>
      <td>${esc(item.latest_name || item.username || "-")}</td>
      <td>${item.latest_name || item.username ? "已 /start" : "等待 /start"}</td>
      <td>${item.is_enabled ? "启用" : "停用"}</td>
      <td class="actions">
        <button class="${item.is_enabled ? "ghost" : ""}" data-toggle-user="${item.telegram_id}">
          ${item.is_enabled ? "停用" : "启用"}
        </button>
        <button class="danger" data-delete-user="${item.telegram_id}">删除</button>
      </td>
    </tr>
  `).join("");
}

function renderAdmins() {
  el("adminsBody").innerHTML = state.admins.map((item) => `
    <tr>
      <td><code>${esc(item.telegram_id)}</code></td>
      <td>${esc(item.display_name)}</td>
      <td>${esc(item.latest_name || item.username || "-")}</td>
      <td>${item.is_enabled ? "启用" : "停用"}</td>
      <td class="actions">
        <button class="${item.is_enabled ? "ghost" : ""}" data-toggle-admin="${item.telegram_id}">
          ${item.is_enabled ? "停用" : "启用"}
        </button>
        <button class="danger" data-delete-admin="${item.telegram_id}">删除</button>
      </td>
    </tr>
  `).join("");
}

function displayName(item) {
  return item.latest_name || item.remark_name || item.username || item.telegram_user_id;
}

function conversationSignature(items) {
  return JSON.stringify(items.map((item) => [
    item.id,
    item.telegram_user_id,
    item.status,
    item.claimed_by_admin_id,
    item.updated_at,
    item.remark_name,
    item.latest_name,
    item.username,
  ]));
}

function renderConversations() {
  state.conversationSignature = conversationSignature(state.conversations);
  const selectedExists = state.conversations.some((item) => Number(item.id) === Number(state.selectedConversationId));
  if (!selectedExists) {
    state.selectedConversationId = null;
    el("messageList").innerHTML = "<p>请选择一个会话查看消息。</p>";
  }
  el("conversationList").innerHTML = state.conversations.map((item) => `
    <div class="conversation-item ${Number(item.id) === Number(state.selectedConversationId) ? "active" : ""}" data-conversation-id="${item.id}">
      <strong>#${item.id} ${esc(displayName(item))}</strong>
      <div>Telegram ID: <code>${esc(item.telegram_user_id)}</code></div>
      <div>更新时间: ${esc(formatTimestamp(item.updated_at))}</div>
      <div>状态: ${esc(item.status)}</div>
    </div>
  `).join("") || "<p>暂无会话。</p>";
}

async function showMessages(conversationId, rerenderList = true) {
  state.selectedConversationId = Number(conversationId);
  const messages = await api(`/api/admin/conversations/${conversationId}/messages`);
  const signature = JSON.stringify(messages.map((item) => [item.id, item.direction, item.message_type, item.text, item.sender_display_name]));
  if (signature !== state.messageSignature) {
    state.messageSignature = signature;
    el("messageList").innerHTML = messages.map((item) => `
      <div class="message ${esc(item.direction)}">
        <strong>${esc(item.sender_display_name || item.direction)}</strong>
        <span>${esc(item.message_type)} · 发送时间：${esc(formatTimestamp(item.created_at))}</span>
        <p>${esc(item.text || `[${item.message_type}]`)}</p>
      </div>
    `).join("") || "<p>暂无消息。</p>";
  }
  if (rerenderList) renderConversations();
}

async function refreshConversations() {
  const conversations = await api("/api/admin/conversations");
  const signature = conversationSignature(conversations);
  state.conversations = conversations;
  el("kpiConversations").textContent = state.conversations.length;
  if (signature !== state.conversationSignature) {
    renderConversations();
  }
  if (state.selectedConversationId) {
    await showMessages(state.selectedConversationId, false);
  }
}

document.querySelectorAll(".nav button").forEach((btn) => btn.addEventListener("click", () => setPage(btn.dataset.page)));
el("refreshBtn").addEventListener("click", loadAll);
el("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", {method: "POST"});
  location.href = "/login";
});

el("botForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const timeoutMinutes = Number(el("handoff_timeout_minutes").value || 30);
  const retentionDays = Number(el("conversation_retention_days").value ?? 30);
  if (!Number.isInteger(timeoutMinutes) || timeoutMinutes < 1 || timeoutMinutes > 1440) {
    el("botMsg").textContent = "请输入 1 到 1440 之间的自动断开分钟数。";
    return;
  }
  if (!Number.isInteger(retentionDays) || retentionDays < 0 || retentionDays > 3650) {
    el("botMsg").textContent = "请输入 0 到 3650 之间的会话记录保留天数。";
    return;
  }
  const payload = {
    bot_token: el("bot_token").value.trim(),
    handoff_timeout_minutes: timeoutMinutes,
    conversation_retention_days: retentionDays
  };
  state.bot = await api("/api/admin/bot-config", {method: "PUT", body: JSON.stringify(payload)});
  renderBot();
  el("botMsg").textContent = "已保存";
});

el("cleanupConversationsBtn").addEventListener("click", async () => {
  const result = await api("/api/admin/conversations/cleanup", {method: "POST"});
  el("botMsg").textContent = `已清除 ${result.deleted || 0} 条老旧会话记录。`;
  await refreshConversations();
});

el("userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const telegramId = readTelegramId("userTelegramId");
  await api("/api/admin/users", {method: "POST", body: JSON.stringify({
    telegram_id: telegramId,
    remark_name: el("userRemark").value,
    is_enabled: el("userEnabled").checked
  })});
  form.reset();
  el("userEnabled").checked = true;
  await loadAll();
});

el("adminForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const telegramId = readTelegramId("adminTelegramId");
  await api("/api/admin/admins", {method: "POST", body: JSON.stringify({
    telegram_id: telegramId,
    display_name: el("adminName").value,
    is_enabled: el("adminEnabled").checked
  })});
  form.reset();
  el("adminEnabled").checked = true;
  await loadAll();
});

document.body.addEventListener("click", async (event) => {
  const target = event.target;
  if (target.dataset.toggleUser) {
    const item = state.users.find((row) => String(row.telegram_id) === String(target.dataset.toggleUser));
    if (!item) return;
    await api(`/api/admin/users/${item.telegram_id}`, {method: "PUT", body: JSON.stringify({
      telegram_id: Number(item.telegram_id),
      remark_name: item.remark_name || "",
      is_enabled: !item.is_enabled
    })});
    await loadAll();
  }
  if (target.dataset.toggleAdmin) {
    const item = state.admins.find((row) => String(row.telegram_id) === String(target.dataset.toggleAdmin));
    if (!item) return;
    await api(`/api/admin/admins/${item.telegram_id}`, {method: "PUT", body: JSON.stringify({
      telegram_id: Number(item.telegram_id),
      display_name: item.display_name || "",
      is_enabled: !item.is_enabled
    })});
    await loadAll();
  }
  if (target.dataset.deleteUser) {
    await api(`/api/admin/users/${target.dataset.deleteUser}`, {method: "DELETE"});
    await loadAll();
  }
  if (target.dataset.deleteAdmin) {
    await api(`/api/admin/admins/${target.dataset.deleteAdmin}`, {method: "DELETE"});
    await loadAll();
  }
  const conversation = target.closest("[data-conversation-id]");
  if (conversation) showMessages(conversation.dataset.conversationId);
});

setPage(location.hash.replace("#", "") || "overview");
loadAll().catch((err) => console.error(err));

setInterval(() => {
  if ((location.hash.replace("#", "") || "overview") === "conversations") {
    refreshConversations().catch((err) => console.error(err));
  }
}, 3000);
