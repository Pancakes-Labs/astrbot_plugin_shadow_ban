// AstrBot 会在返回 HTML 时自动注入 bridge SDK，无需手动引入。
const bridge = window.AstrBotPluginPage;

// 页面文案回退（英文 locale 下的默认值）。
const DEFAULTS = {
  title: "Shadow Ban Dashboard",
  heading: "Shadow Ban Dashboard",
  desc: "Monitor the shadow ban status and recent interceptions.",
  labelTotal: "Blocked users",
  labelBlocks: "Total blocks",
  labelRecent: "Recent records",
  labelSwitch: "Master switch",
  switchesTitle: "Active scope",
  switchesHint: "Click to toggle",
  listTitle: "Block list",
  listEmpty: "No blocked users",
  listNoMatch: "No matching entries",
  listSearch: "Search IDs or platforms",
  addPlaceholder: "e.g. 123456 or aiocqhttp:123456",
  add: "Add",
  remove: "Remove",
  recentTitle: "Recent interceptions",
  thTime: "Time",
  thScope: "Type",
  thPlatform: "Platform",
  thUser: "User",
  thContent: "Content",
  recentEmpty: "No records yet",
  recentNoMatch: "No matching records",
  refresh: "Refresh",
  clearRecent: "Clear records",
  on: "On",
  off: "Off",
  scopeGroup: "Group chat",
  scopePrivate: "Private chat",
  scopeCommand: "Allow command LLM",
  descMaster: "Affects all interception behaviour",
  descGroup: "Apply the block list in group chats",
  descPrivate: "Apply the block list in private chats",
  descCommand: "Do not block command-triggered LLM calls",
  filterAll: "All",
  filterGroup: "Group",
  filterPrivate: "Private",
  filterSearch: "Search records",
  versionLabel: "Version",
  uptimeLabel: "Uptime",
  lastLabel: "Last block",
  never: "Never",
  globalTag: "GLOBAL",
  totalSub: "{global} global · {platform} platform",
  blocksSub: "{recent} kept / {capacity}",
  recentSub: "Newest first",
  switchSub: "Shadow mode",
  clearTitle: "Clear interception records?",
  clearDesc: "This removes the in-memory and persisted record history.",
  clearExtra: "Also reset the total block counter",
  cancel: "Cancel",
  confirmClear: "Clear",
  toastAdded: "Added {count} entr(y/ies)",
  toastRemoved: "Removed {count} entr(y/ies)",
  toastExists: "Entry already exists",
  toastMissing: "Entry not found",
  toastOn: "Enabled: {name}",
  toastOff: "Disabled: {name}",
  toastCleared: "Records cleared",
  toastRefresh: "Refreshed",
  toastError: "Operation failed: {message}",
  toastSaveFailed: "Updated in memory, but saving to config failed",
  installFailed: "Failed to reach the plugin API.",
};

const $ = (id) => document.getElementById(id);

const state = {
  status: null,
  recent: [],
  scopeFilter: "all",
  listQuery: "",
  recordQuery: "",
  loading: false,
  reloadTimer: null,
};

function t(key, fallback) {
  try {
    return bridge.t(`pages.dashboard.${key}`, fallback);
  } catch {
    return fallback;
  }
}

function fmt(template, vars) {
  return String(template).replace(/\{(\w+)\}/g, (whole, key) =>
    key in vars ? String(vars[key]) : whole,
  );
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}

// 把秒数格式化为 3d 4h / 5h 12m / 42s 形式。
function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${total % 60}s`;
  return `${total}s`;
}

function formatRelative(ts) {
  const value = Number(ts);
  if (!Number.isFinite(value) || value <= 0) {
    return t("never", DEFAULTS.never);
  }
  const diff = Math.max(0, Date.now() / 1000 - value);
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  const date = new Date(value * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getMonth() + 1}/${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

// ---------------------------------------------------------------- 静态文案

function renderStatic() {
  const locale = (bridge && bridge.getLocale && bridge.getLocale()) || "zh-CN";
  document.documentElement.lang = locale;
  document.title = t("title", DEFAULTS.title);

  const setText = (id, key, fallback) => {
    const el = $(id);
    if (el) el.textContent = t(key, fallback);
  };

  setText("heading", "heading", DEFAULTS.heading);
  setText("desc", "desc", DEFAULTS.desc);
  setText("label-total", "labelTotal", DEFAULTS.labelTotal);
  setText("label-blocks", "labelBlocks", DEFAULTS.labelBlocks);
  setText("label-recent", "labelRecent", DEFAULTS.labelRecent);
  setText("label-switch", "labelSwitch", DEFAULTS.labelSwitch);
  setText("switches-title", "switchesTitle", DEFAULTS.switchesTitle);
  setText("switches-hint", "switchesHint", DEFAULTS.switchesHint);
  setText("list-title", "listTitle", DEFAULTS.listTitle);
  setText("recent-title", "recentTitle", DEFAULTS.recentTitle);
  setText("th-time", "thTime", DEFAULTS.thTime);
  setText("th-scope", "thScope", DEFAULTS.thScope);
  setText("th-platform", "thPlatform", DEFAULTS.thPlatform);
  setText("th-user", "thUser", DEFAULTS.thUser);
  setText("th-content", "thContent", DEFAULTS.thContent);
  setText("refresh-label", "refresh", DEFAULTS.refresh);
  setText("clear-label", "clearRecent", DEFAULTS.clearRecent);
  setText("add-button", "add", DEFAULTS.add);
  setText("meta-version-label", "versionLabel", DEFAULTS.versionLabel);
  setText("meta-uptime-label", "uptimeLabel", DEFAULTS.uptimeLabel);
  setText("meta-last-label", "lastLabel", DEFAULTS.lastLabel);

  const search = $("list-search");
  if (search) search.placeholder = t("listSearch", DEFAULTS.listSearch);
  const recordSearch = $("record-search");
  if (recordSearch) recordSearch.placeholder = t("filterSearch", DEFAULTS.filterSearch);
  const addInput = $("add-input");
  if (addInput) addInput.placeholder = t("addPlaceholder", DEFAULTS.addPlaceholder);

  const clearBtn = $("clear-recent");
  if (clearBtn) clearBtn.title = t("clearRecent", DEFAULTS.clearRecent);

  // 语言切换后需要重建开关与筛选按钮的文案。
  const switches = $("switches");
  if (switches) switches.innerHTML = "";

  renderScopeFilter();
}

// ---------------------------------------------------------------- Toast

function toast(message, tone = "info", timeout = 3200) {
  const box = $("toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.tone = tone;
  el.textContent = message;
  box.appendChild(el);
  window.setTimeout(() => {
    el.classList.add("is-out");
    window.setTimeout(() => el.remove(), 260);
  }, timeout);
}

// ---------------------------------------------------------------- 确认弹窗

let modalResolve = null;

function openConfirm({ title, desc, confirmText, extraLabel }) {
  const modal = $("modal");
  $("modal-title").textContent = title;
  $("modal-desc").textContent = desc;
  $("modal-confirm").textContent = confirmText;
  $("modal-cancel").textContent = t("cancel", DEFAULTS.cancel);

  const extraWrap = $("modal-extra-wrap");
  const extra = $("modal-extra");
  if (extraLabel) {
    $("modal-extra-label").textContent = extraLabel;
    extraWrap.hidden = false;
    extra.checked = false;
  } else {
    extraWrap.hidden = true;
    extra.checked = false;
  }

  modal.hidden = false;
  $("modal-confirm").focus();

  return new Promise((resolve) => {
    modalResolve = (value) => {
      modal.hidden = true;
      modalResolve = null;
      resolve(value);
    };
  });
}

function closeConfirm(value) {
  if (modalResolve) modalResolve(value);
}

function bindModal() {
  $("modal-cancel").addEventListener("click", () => closeConfirm(null));
  $("modal-confirm").addEventListener("click", () =>
    closeConfirm({ extra: $("modal-extra").checked }),
  );
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) closeConfirm(null);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("modal").hidden) closeConfirm(null);
  });
}

// ---------------------------------------------------------------- 开关

const SWITCH_DEFS = [
  ["enable", "labelSwitch", DEFAULTS.labelSwitch, "descMaster", DEFAULTS.descMaster],
  ["enable_group", "scopeGroup", DEFAULTS.scopeGroup, "descGroup", DEFAULTS.descGroup],
  ["enable_private", "scopePrivate", DEFAULTS.scopePrivate, "descPrivate", DEFAULTS.descPrivate],
  [
    "allow_command_llm",
    "scopeCommand",
    DEFAULTS.scopeCommand,
    "descCommand",
    DEFAULTS.descCommand,
  ],
];

function renderSwitches(status) {
  const ul = $("switches");
  if (!ul) return;
  // 结构已存在时只更新状态，避免点击后整块重建导致闪烁。
  const existing = ul.querySelectorAll(".switch");
  if (existing.length === SWITCH_DEFS.length) {
    existing.forEach((li) => {
      const key = li.dataset.key;
      if (!key) return;
      const on = Boolean(status?.[key]);
      li.dataset.on = String(on);
      li.setAttribute("aria-checked", String(on));
      const dot = li.querySelector(".dot");
      if (dot) dot.className = `dot${on ? " on" : ""}`;
      // 点击处理闭包中的 on 已过期，改为按最新状态取反。
      li.onclick = () => toggleSetting(key, !Boolean(status?.[key]), li);
    });
    return;
  }

  ul.innerHTML = "";

  for (const [key, labelKey, labelFallback, descKey, descFallback] of SWITCH_DEFS) {
    const on = Boolean(status?.[key]);
    const li = document.createElement("li");
    li.className = "switch";
    li.dataset.key = key;
    li.dataset.on = String(on);
    li.setAttribute("role", "switch");
    li.setAttribute("aria-checked", String(on));
    li.tabIndex = 0;
    li.title = `${t(labelKey, labelFallback)}: ${t("on", DEFAULTS.on)} / ${t(
      "off",
      DEFAULTS.off,
    )}`;

    const dot = document.createElement("span");
    dot.className = `dot${on ? " on" : ""}`;

    const text = document.createElement("span");
    text.className = "switch-text";
    const label = document.createElement("span");
    label.className = "switch-label";
    label.textContent = t(labelKey, labelFallback);
    const sub = document.createElement("span");
    sub.className = "switch-desc";
    sub.textContent = t(descKey, descFallback);
    text.append(label, sub);

    const toggle = document.createElement("span");
    toggle.className = "toggle";

    li.append(dot, text, toggle);
    li.onclick = () => toggleSetting(key, !Boolean(status?.[key]), li);
    li.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        li.click();
      }
    });
    ul.appendChild(li);
  }
}

async function toggleSetting(key, next, li) {
  if (!bridge || typeof bridge.apiPost !== "function") return;
  li.setAttribute("aria-busy", "true");
  try {
    const result = await bridge.apiPost("dashboard/settings", { [key]: next });
    if (!result || result.ok === false) {
      throw new Error((result && result.message) || "unknown error");
    }
    if (state.status) {
      Object.assign(state.status, result.settings || {});
    }
    applyStatus();
    const name = t(
      SWITCH_DEFS.find((item) => item[0] === key)?.[1] || key,
      key,
    );
    toast(
      fmt(t(next ? "toastOn" : "toastOff", next ? DEFAULTS.toastOn : DEFAULTS.toastOff), {
        name,
      }),
      "success",
    );
    if (result.saved === false) {
      toast(t("toastSaveFailed", DEFAULTS.toastSaveFailed), "error", 5000);
    }
  } catch (error) {
    console.error("Failed to toggle setting:", error);
    toast(
      fmt(t("toastError", DEFAULTS.toastError), { message: error.message || error }),
      "error",
    );
    applyStatus();
  } finally {
    li.removeAttribute("aria-busy");
  }
}

// ---------------------------------------------------------------- 名单

function collectEntries(status) {
  const entries = [];
  for (const id of status?.global_ids || []) {
    entries.push({ raw: id, kind: t("globalTag", DEFAULTS.globalTag), platform: "" });
  }
  for (const [platform, ids] of Object.entries(status?.platform_ids || {})) {
    for (const id of ids) {
      entries.push({ raw: `${platform}:${id}`, kind: platform, platform });
    }
  }
  return entries;
}

function renderBlockList() {
  const box = $("block-list");
  const badge = $("list-count");
  if (!box) return;

  const entries = collectEntries(state.status);
  if (badge) badge.textContent = String(entries.length);

  const query = state.listQuery.trim().toLowerCase();
  const filtered = query
    ? entries.filter(
        (item) =>
          item.raw.toLowerCase().includes(query) ||
          item.kind.toLowerCase().includes(query),
      )
    : entries;

  box.innerHTML = "";

  if (entries.length === 0) {
    box.appendChild(emptyState(t("listEmpty", DEFAULTS.listEmpty), "🕶️"));
    return;
  }

  if (filtered.length === 0) {
    box.appendChild(emptyState(t("listNoMatch", DEFAULTS.listNoMatch), "🔍"));
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of filtered) {
    const tag = document.createElement("span");
    tag.className = "tag";

    const kind = document.createElement("span");
    kind.className = `tag-kind${item.platform ? " is-platform" : ""}`;
    kind.textContent = item.kind;

    const text = document.createElement("span");
    text.className = "tag-text";
    text.textContent = item.platform
      ? item.raw.slice(item.platform.length + 1)
      : item.raw;
    text.title = item.raw;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "tag-remove";
    remove.textContent = "×";
    remove.title = t("remove", DEFAULTS.remove);
    remove.setAttribute("aria-label", `${t("remove", DEFAULTS.remove)} ${item.raw}`);
    remove.addEventListener("click", () => removeEntry(item.raw, remove));

    tag.append(kind, text, remove);
    fragment.appendChild(tag);
  }
  box.appendChild(fragment);
}

function emptyState(message, icon) {
  const wrap = document.createElement("div");
  wrap.className = "empty-state";
  const emoji = document.createElement("span");
  emoji.className = "empty-icon";
  emoji.textContent = icon || "·";
  const label = document.createElement("span");
  label.className = "empty";
  label.textContent = message;
  wrap.append(emoji, label);
  return wrap;
}

async function removeEntry(entry, button) {
  if (!bridge || typeof bridge.apiPost !== "function") return;
  if (button) button.disabled = true;
  try {
    const result = await bridge.apiPost("dashboard/blocked", {
      action: "remove",
      values: [entry],
    });
    if (!result || result.ok === false) {
      throw new Error((result && result.message) || "unknown error");
    }
    const count = (result.removed || []).length;
    toast(
      count > 0
        ? fmt(t("toastRemoved", DEFAULTS.toastRemoved), { count })
        : t("toastMissing", DEFAULTS.toastMissing),
      count > 0 ? "success" : "info",
    );
    await reload({ silent: true });
  } catch (error) {
    console.error("Failed to remove entry:", error);
    toast(
      fmt(t("toastError", DEFAULTS.toastError), { message: error.message || error }),
      "error",
    );
    if (button) button.disabled = false;
  }
}

async function addEntries(values) {
  if (!bridge || typeof bridge.apiPost !== "function") return;
  const button = $("add-button");
  if (button) button.disabled = true;
  try {
    const result = await bridge.apiPost("dashboard/blocked", {
      action: "add",
      values,
    });
    if (!result || result.ok === false) {
      throw new Error((result && result.message) || "unknown error");
    }
    const added = (result.added || []).length;
    toast(
      added > 0
        ? fmt(t("toastAdded", DEFAULTS.toastAdded), { count: added })
        : t("toastExists", DEFAULTS.toastExists),
      added > 0 ? "success" : "info",
    );
    const input = $("add-input");
    if (input) input.value = "";
    await reload({ silent: true });
  } catch (error) {
    console.error("Failed to add entries:", error);
    toast(
      fmt(t("toastError", DEFAULTS.toastError), { message: error.message || error }),
      "error",
    );
  } finally {
    if (button) button.disabled = false;
  }
}

// ---------------------------------------------------------------- 记录

function renderScopeFilter() {
  const box = $("scope-filter");
  if (!box) return;
  const options = [
    ["all", "filterAll", DEFAULTS.filterAll],
    ["group", "filterGroup", DEFAULTS.filterGroup],
    ["private", "filterPrivate", DEFAULTS.filterPrivate],
  ];
  box.innerHTML = "";
  for (const [value, key, fallback] of options) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = t(key, fallback);
    chip.dataset.value = value;
    chip.setAttribute("aria-pressed", String(state.scopeFilter === value));
    chip.addEventListener("click", () => {
      state.scopeFilter = value;
      for (const node of box.querySelectorAll(".chip")) {
        node.setAttribute("aria-pressed", String(node.dataset.value === value));
      }
      renderRecent();
    });
    box.appendChild(chip);
  }
}

// 兼容新旧记录格式，统一得到 group / private。
function resolveScopeKey(item) {
  if (!item) return "group";
  if (item.scope_key === "private" || item.scope_key === "group") {
    return item.scope_key;
  }
  const scope = String(item.scope || "").toLowerCase();
  return scope === "私聊" || scope === "private" ? "private" : "group";
}

function renderRecent() {
  const body = $("recent-body");
  const usage = $("recent-usage");
  if (!body) return;

  const items = state.recent || [];
  const status = state.status || {};
  if (usage) {
    usage.textContent = `${items.length} / ${status.recent_capacity ?? 100}`;
  }

  const query = state.recordQuery.trim().toLowerCase();
  const filtered = items.filter((item) => {
    if (
      state.scopeFilter !== "all" &&
      resolveScopeKey(item) !== state.scopeFilter
    ) {
      return false;
    }
    if (!query) return true;
    return [item.time, item.scope, item.platform, item.user_id, item.content]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  });

  body.innerHTML = "";

  if (filtered.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty";
    td.id = "recent-empty";
    td.textContent =
      items.length === 0
        ? t("recentEmpty", DEFAULTS.recentEmpty)
        : t("recentNoMatch", DEFAULTS.recentNoMatch);
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of filtered) {
    const tr = document.createElement("tr");

    const timeCell = document.createElement("td");
    timeCell.className = "mono";
    timeCell.textContent = item.time || "";
    timeCell.title = item.time || "";

    const scopeCell = document.createElement("td");
    const scopeTag = document.createElement("span");
    const isPrivate = resolveScopeKey(item) === "private";
    scopeTag.className = `scope-tag ${isPrivate ? "is-private" : "is-group"}`;
    scopeTag.textContent = isPrivate
      ? t("scopePrivate", DEFAULTS.scopePrivate)
      : t("scopeGroup", DEFAULTS.scopeGroup);
    scopeCell.appendChild(scopeTag);

    const platformCell = document.createElement("td");
    const platformTag = document.createElement("span");
    platformTag.className = "platform-tag";
    platformTag.textContent = item.platform || "-";
    platformCell.appendChild(platformTag);

    const userCell = document.createElement("td");
    userCell.className = "mono";
    userCell.textContent = item.user_id || "-";
    if (item.group_id) {
      userCell.title = `group: ${item.group_id}`;
    }

    const contentCell = document.createElement("td");
    contentCell.className = "content";
    contentCell.textContent = item.content || "";
    contentCell.title = item.content || "";

    tr.append(timeCell, scopeCell, platformCell, userCell, contentCell);
    fragment.appendChild(tr);
  }
  body.appendChild(fragment);
}

// ---------------------------------------------------------------- 状态渲染

function applyStatus() {
  const status = state.status;
  if (!status) return;

  const total = $("total-users");
  if (total) total.textContent = num(status.total_blocked_users ?? 0);

  const blocks = $("block-count");
  if (blocks) blocks.textContent = num(status.block_count ?? 0);

  const recentCount = $("recent-count");
  if (recentCount) recentCount.textContent = num(status.recent_count ?? 0);

  const switchState = $("switch-state");
  if (switchState) {
    const on = Boolean(status.enable);
    switchState.textContent = on
      ? t("on", DEFAULTS.on)
      : t("off", DEFAULTS.off);
    switchState.style.color = on ? "var(--emerald)" : "var(--rose)";
  }

  const kpiSwitch = $("kpi-switch");
  if (kpiSwitch) kpiSwitch.dataset.on = String(Boolean(status.enable));

  const pill = $("status-pill");
  if (pill) {
    pill.dataset.on = String(Boolean(status.enable));
  }
  const statusText = $("status-text");
  if (statusText) {
    statusText.textContent = status.enable
      ? t("on", DEFAULTS.on)
      : t("off", DEFAULTS.off);
  }

  const totalSub = $("kpi-total-sub");
  if (totalSub) {
    totalSub.textContent = fmt(t("totalSub", DEFAULTS.totalSub), {
      global: status.global_count ?? status.global_ids?.length ?? 0,
      platform: status.platform_count ?? 0,
    });
  }

  const blocksSub = $("kpi-blocks-sub");
  if (blocksSub) {
    blocksSub.textContent = fmt(t("blocksSub", DEFAULTS.blocksSub), {
      recent: status.recent_count ?? 0,
      capacity: status.recent_capacity ?? 100,
    });
  }

  const recentSub = $("kpi-recent-sub");
  if (recentSub) {
    recentSub.textContent = t("recentSub", DEFAULTS.recentSub);
  }

  const switchSub = $("kpi-switch-sub");
  if (switchSub) {
    switchSub.textContent = t("switchSub", DEFAULTS.switchSub);
  }

  const version = $("meta-version");
  if (version) version.textContent = status.plugin_version || "-";

  const uptime = $("meta-uptime");
  if (uptime) uptime.textContent = formatUptime(status.uptime_seconds);

  const last = $("meta-last");
  if (last) last.textContent = formatRelative(status.last_block_ts);

  renderSwitches(status);
  renderBlockList();
  renderRecent();
}

// ---------------------------------------------------------------- 数据加载

async function fetchAll() {
  const [status, recent] = await Promise.all([
    bridge.apiGet("dashboard/status"),
    bridge.apiGet("dashboard/recent", { limit: 100 }),
  ]);
  state.status = status || {};
  state.recent = (recent && recent.items) || [];
}

async function reload({ silent = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  try {
    await fetchAll();
    applyStatus();
  } catch (error) {
    console.error("Failed to load shadow ban status:", error);
    if (!silent) {
      toast(
        `${t("installFailed", DEFAULTS.installFailed)} ${error.message || ""}`.trim(),
        "error",
        5000,
      );
    }
  } finally {
    state.loading = false;
  }
}

async function refreshWithFeedback() {
  const button = $("refresh");
  if (button) {
    button.classList.add("spinning");
    button.disabled = true;
  }
  try {
    await reload();
    toast(t("toastRefresh", DEFAULTS.toastRefresh), "info", 1600);
  } finally {
    if (button) {
      button.classList.remove("spinning");
      button.disabled = false;
    }
  }
}

async function clearRecent() {
  const choice = await openConfirm({
    title: t("clearTitle", DEFAULTS.clearTitle),
    desc: t("clearDesc", DEFAULTS.clearDesc),
    confirmText: t("confirmClear", DEFAULTS.confirmClear),
    extraLabel: t("clearExtra", DEFAULTS.clearExtra),
  });
  if (!choice) return;

  try {
    const result = await bridge.apiPost("dashboard/recent/clear", {
      reset_count: Boolean(choice.extra),
    });
    if (!result || result.ok === false) {
      throw new Error((result && result.message) || "unknown error");
    }
    toast(t("toastCleared", DEFAULTS.toastCleared), "success");
    await reload({ silent: true });
  } catch (error) {
    console.error("Failed to clear records:", error);
    toast(
      fmt(t("toastError", DEFAULTS.toastError), { message: error.message || error }),
      "error",
    );
  }
}

// ---------------------------------------------------------------- 初始化

function bindEvents() {
  $("refresh")?.addEventListener("click", refreshWithFeedback);

  $("add-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("add-input");
    const value = (input && input.value) || "";
    const values = value
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (values.length === 0) {
      if (input) input.focus();
      return;
    }
    addEntries(values);
  });

  $("list-search")?.addEventListener("input", (event) => {
    state.listQuery = event.target.value || "";
    renderBlockList();
  });

  $("record-search")?.addEventListener("input", (event) => {
    state.recordQuery = event.target.value || "";
    renderRecent();
  });

  $("clear-recent")?.addEventListener("click", clearRecent);

  // logo 加载失败时退化为内置图形占位，避免破图。
  const mark = document.querySelector(".brand-mark");
  const markImg = mark?.querySelector("img");
  if (markImg) {
    markImg.addEventListener("error", () => mark.classList.add("is-fallback"));
    if (markImg.complete && markImg.naturalWidth === 0) {
      mark.classList.add("is-fallback");
    }
  }

  // 切回页面时静默补一次数据。
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) reload({ silent: true });
  });
}

async function boot() {
  bindModal();
  bindEvents();

  if (!bridge) {
    console.error("AstrBotPluginPage bridge is missing.");
    renderStatic();
    return;
  }

  try {
    await bridge.ready();
  } catch (error) {
    console.error("Plugin bridge is not ready:", error);
  }

  renderStatic();

  if (typeof bridge.onContext === "function") {
    bridge.onContext(() => {
      renderStatic();
      reload({ silent: true });
    });
  }

  await reload();

  // 每 10 秒自动刷新；页面不可见时跳过，减少无谓请求。
  if (state.reloadTimer) window.clearInterval(state.reloadTimer);
  state.reloadTimer = window.setInterval(() => {
    if (!document.hidden) reload({ silent: true });
  }, 10000);
}

boot();
