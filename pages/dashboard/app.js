// AstrBot 会在返回 HTML 时自动注入 bridge SDK，无需手动引入。
const bridge = window.AstrBotPluginPage;

// 页面文案回退（英文 locale 下的默认值）。
const DEFAULTS = {
  title: "Shadow Ban Dashboard",
  heading: "Shadow Ban Dashboard",
  desc: "Monitor the shadow ban status and recent interceptions.",
  labelTotal: "Blocked users",
  labelBlocks: "Total blocks",
  labelSwitch: "Master switch",
  switchesTitle: "Active scope",
  listTitle: "Block list",
  listEmpty: "No blocked users",
  recentTitle: "Recent interceptions",
  thTime: "Time",
  thScope: "Type",
  thPlatform: "Platform",
  thUser: "User",
  thContent: "Content",
  recentEmpty: "No records yet",
  refresh: "Refresh",
  on: "On",
  off: "Off",
  scopeGroup: "Group chat",
  scopePrivate: "Private chat",
  scopeCommand: "Allow command LLM",
};

const $ = (id) => document.getElementById(id);

function t(key, fallback) {
  return bridge.t(`pages.dashboard.${key}`, fallback);
}

function renderStatic() {
  const locale = bridge.getLocale() || "zh-CN";
  document.documentElement.lang = locale;
  document.title = t("title", DEFAULTS.title);
  $("heading").textContent = t("heading", DEFAULTS.heading);
  $("desc").textContent = t("desc", DEFAULTS.desc);
  $("label-total").textContent = t("labelTotal", DEFAULTS.labelTotal);
  $("label-blocks").textContent = t("labelBlocks", DEFAULTS.labelBlocks);
  $("label-switch").textContent = t("labelSwitch", DEFAULTS.labelSwitch);
  $("switches-title").textContent = t("switchesTitle", DEFAULTS.switchesTitle);
  $("list-title").textContent = t("listTitle", DEFAULTS.listTitle);
  $("list-empty").textContent = t("listEmpty", DEFAULTS.listEmpty);
  $("recent-title").textContent = t("recentTitle", DEFAULTS.recentTitle);
  $("th-time").textContent = t("thTime", DEFAULTS.thTime);
  $("th-scope").textContent = t("thScope", DEFAULTS.thScope);
  $("th-platform").textContent = t("thPlatform", DEFAULTS.thPlatform);
  $("th-user").textContent = t("thUser", DEFAULTS.thUser);
  $("th-content").textContent = t("thContent", DEFAULTS.thContent);
  $("recent-empty").textContent = t("recentEmpty", DEFAULTS.recentEmpty);
  $("refresh").textContent = t("refresh", DEFAULTS.refresh);
}

function renderSwitches(status) {
  const items = [
    [t("scopeGroup", DEFAULTS.scopeGroup), status.enable_group],
    [t("scopePrivate", DEFAULTS.scopePrivate), status.enable_private],
    [t("scopeCommand", DEFAULTS.scopeCommand), status.allow_command_llm],
  ];
  const ul = $("switches");
  ul.innerHTML = "";
  for (const [label, on] of items) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `dot${on ? " on" : ""}`;
    const text = document.createElement("span");
    text.textContent = label;
    li.append(dot, text);
    ul.appendChild(li);
  }
}

function renderBlockList(status) {
  const box = $("block-list");
  box.innerHTML = "";
  const entries = [];

  for (const id of status.global_ids || []) {
    entries.push(id);
  }
  for (const [platform, ids] of Object.entries(status.platform_ids || {})) {
    for (const id of ids) {
      entries.push(`${platform}:${id}`);
    }
  }

  if (entries.length === 0) {
    const span = document.createElement("span");
    span.className = "empty";
    span.id = "list-empty";
    span.textContent = t("listEmpty", DEFAULTS.listEmpty);
    box.appendChild(span);
    return;
  }

  for (const entry of entries) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = entry;
    box.appendChild(tag);
  }
}

function renderRecent(data) {
  const body = $("recent-body");
  body.innerHTML = "";
  const items = data.items || [];

  if (items.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty";
    td.id = "recent-empty";
    td.textContent = t("recentEmpty", DEFAULTS.recentEmpty);
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    const cells = [
      item.time,
      item.scope,
      item.platform,
      item.user_id,
      item.content,
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value ?? "";
      if (index === 4) {
        td.className = "content";
        td.title = value ?? "";
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  }
}

async function load() {
  try {
    const [status, recent] = await Promise.all([
      bridge.apiGet("dashboard/status"),
      bridge.apiGet("dashboard/recent", { limit: 30 }),
    ]);

    $("total-users").textContent = status.total_blocked_users ?? 0;
    $("block-count").textContent = status.block_count ?? 0;
    $("switch-state").textContent = status.enable
      ? t("on", DEFAULTS.on)
      : t("off", DEFAULTS.off);
    $("switch-state").style.color = status.enable
      ? "var(--ok)"
      : "var(--off)";

    renderSwitches(status);
    renderBlockList(status);
    renderRecent(recent);
  } catch (error) {
    console.error("Failed to load shadow ban status:", error);
  }
}

// 等待 bridge 就绪并获取初始上下文。
await bridge.ready();
renderStatic();
bridge.onContext(() => {
  renderStatic();
  load();
});

$("refresh").addEventListener("click", load);

await load();

// 每 10 秒自动刷新一次最近记录。
setInterval(load, 10000);
