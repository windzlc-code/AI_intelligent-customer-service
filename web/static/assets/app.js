const state = {
  bot: null,
  users: [],
  admins: [],
  replies: [],
  conversations: [],
};

const titles = {
  overview: ["概览", "单个 Telegram Bot，同时服务用户端和人工端。"],
  bot: ["Bot 配置", "只需要保存 Bot Token，Webhook 信息由系统自动处理。"],
  users: ["用户 ID", "只有添加到这里的 Telegram ID 才能使用用户端。"],
  admins: ["管理员 ID", "只有添加到这里的 Telegram ID 才能使用 /admin 人工端。"],
  replies: ["预制话术", "配置用户端菜单按钮和固定回复。"],
  conversations: ["会话记录", "查看用户进入人工模式后的消息记录。"],
};

function el(id) { return document.getElementById(id); }
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
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
  document.querySelectorAll(".nav button").forEach((btn) => btn.classList.toggle("active", btn.dataset.page === page));
  document.querySelectorAll(".page").forEach((node) => node.classList.toggle("active", node.id === `page-${page}`));
  el("pageTitle").textContent = titles[page][0];
  el("pageSubtitle").textContent = titles[page][1];
  location.hash = page;
}

async function loadAll() {
  const [me, bot, users, admins, replies, conversations] = await Promise.all([
    api("/api/me"),
    api("/api/admin/bot-config"),
    api("/api/admin/users"),
    api("/api/admin/admins"),
    api("/api/admin/preset-replies"),
    api("/api/admin/conversations"),
  ]);
  el("meLabel").textContent = `${me.username} / ${me.role}`;
  Object.assign(state, {bot, users, admins, replies, conversations});
  render();
}

function render() {
  el("kpiUsers").textContent = state.users.length;
  el("kpiAdmins").textContent = state.admins.length;
  el("kpiReplies").textContent = state.replies.length;
  el("kpiConversations").textContent = state.conversations.length;
  renderBot();
  renderUsers();
  renderAdmins();
  renderReplies();
  renderConversations();
}

function renderBot() {
  if (el("bot_token")) {
    el("bot_token").value = "";
    el("bot_token").placeholder = state.bot?.bot_token_masked || "123456:ABC...";
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
      <td><button class="danger" data-delete-user="${item.telegram_id}">删除</button></td>
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
      <td><button class="danger" data-delete-admin="${item.telegram_id}">删除</button></td>
    </tr>
  `).join("");
}

function renderReplies() {
  el("replyList").innerHTML = state.replies.map((item, index) => `
    <div class="reply-row" data-reply-index="${index}">
      <input data-field="button_text" value="${esc(item.button_text)}" placeholder="按钮文字">
      <input data-field="reply_text" value="${esc(item.reply_text)}" placeholder="回复内容">
      <input data-field="sort_order" type="number" value="${esc(item.sort_order)}">
      <label class="check"><input data-field="is_enabled" type="checkbox" ${item.is_enabled ? "checked" : ""}> 启用</label>
      <button class="danger" data-remove-reply="${index}">删除</button>
    </div>
  `).join("");
}

function displayName(item) {
  return item.latest_name || item.remark_name || item.username || item.telegram_user_id;
}

function renderConversations() {
  el("conversationList").innerHTML = state.conversations.map((item) => `
    <div class="conversation-item" data-conversation-id="${item.id}">
      <strong>#${item.id} ${esc(displayName(item))}</strong>
      <div>Telegram ID: <code>${esc(item.telegram_user_id)}</code></div>
      <div>状态: ${esc(item.status)}</div>
    </div>
  `).join("") || "<p>暂无会话。</p>";
}

async function showMessages(conversationId) {
  const messages = await api(`/api/admin/conversations/${conversationId}/messages`);
  el("messageList").innerHTML = messages.map((item) => `
    <div class="message ${esc(item.direction)}">
      <strong>${esc(item.sender_display_name || item.direction)}</strong>
      <span>${esc(item.message_type)}</span>
      <p>${esc(item.text || `[${item.message_type}]`)}</p>
    </div>
  `).join("") || "<p>暂无消息。</p>";
}

document.querySelectorAll(".nav button").forEach((btn) => btn.addEventListener("click", () => setPage(btn.dataset.page)));
el("refreshBtn").addEventListener("click", loadAll);
el("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", {method: "POST"});
  location.href = "/login";
});

el("botForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {bot_token: el("bot_token").value.trim()};
  state.bot = await api("/api/admin/bot-config", {method: "PUT", body: JSON.stringify(payload)});
  renderBot();
  el("botMsg").textContent = "已保存";
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
  if (target.dataset.deleteUser) {
    await api(`/api/admin/users/${target.dataset.deleteUser}`, {method: "DELETE"});
    await loadAll();
  }
  if (target.dataset.deleteAdmin) {
    await api(`/api/admin/admins/${target.dataset.deleteAdmin}`, {method: "DELETE"});
    await loadAll();
  }
  if (target.dataset.removeReply) {
    state.replies.splice(Number(target.dataset.removeReply), 1);
    renderReplies();
  }
  const conversation = target.closest("[data-conversation-id]");
  if (conversation) showMessages(conversation.dataset.conversationId);
});

el("addReplyBtn").addEventListener("click", () => {
  state.replies.push({button_text: "", reply_text: "", sort_order: (state.replies.length + 1) * 10, is_enabled: true});
  renderReplies();
});

el("saveRepliesBtn").addEventListener("click", async () => {
  const items = [...document.querySelectorAll(".reply-row")].map((row) => {
    const item = {};
    row.querySelectorAll("[data-field]").forEach((node) => {
      item[node.dataset.field] = node.type === "checkbox" ? node.checked : node.value;
    });
    item.sort_order = Number(item.sort_order || 0);
    return item;
  });
  state.replies = await api("/api/admin/preset-replies", {method: "PUT", body: JSON.stringify({items})});
  renderReplies();
});

setPage(location.hash.replace("#", "") || "overview");
loadAll().catch((err) => console.error(err));
