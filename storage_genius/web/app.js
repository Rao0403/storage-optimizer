const state = { dashboard: null, busy: false };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value.toFixed(0)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value;
  for (const unit of units) {
    scaled /= 1024;
    if (scaled < 1024 || unit === "TB") return `${scaled.toFixed(scaled >= 100 ? 0 : 1)} ${unit}`;
  }
  return `${value} B`;
}

function formatDate(value) {
  if (!value) return "not observed";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function showNotice(message, kind = "") {
  const notice = $("#notice");
  notice.textContent = message;
  notice.className = `notice visible ${kind}`;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => { notice.textContent = ""; notice.className = "notice"; }, 6000);
}

function setBusy(busy) {
  state.busy = busy;
  $$("button").forEach((button) => { button.disabled = busy && !button.classList.contains("nav-item"); });
}

function drive(root) {
  return state.dashboard?.summary?.drives?.find((item) => item.root.toUpperCase() === root) || null;
}

function renderSummary() {
  const summary = state.dashboard.summary;
  const cDrive = drive("C:\\");
  const queue = summary.queue || {};
  const apps = summary.apps || {};
  const hotspots = summary.hotspots || {};
  $("#c-free").textContent = cDrive ? formatBytes(cDrive.free_bytes) : "--";
  $("#c-free-meta").textContent = cDrive ? `${cDrive.free_percent}% free of ${formatBytes(cDrive.total_bytes)}` : "C: drive unavailable";
  $("#c-meter").style.width = cDrive ? `${Math.max(2, Math.min(100, 100 - cDrive.free_percent))}%` : "0";
  $("#reclaim-total").textContent = formatBytes(hotspots.reclaimable_bytes);
  $("#queue-pending").textContent = queue.pending || 0;
  $("#queue-meta").textContent = `${queue.approved || 0} approved, ${queue.executed || 0} executed`;
  $("#app-review-count").textContent = apps.review_count || 0;
  $("#app-count-meta").textContent = `${apps.count || 0} user-facing apps inventoried`;
  const latest = summary.latest_scans?.hotspot;
  $("#hotspot-scan-time").textContent = latest ? `Scanned ${formatDate(latest.completed_at || latest.started_at)}` : "Not scanned";

  const categories = hotspots.categories || [];
  const maxSize = Math.max(...categories.map((item) => Number(item.size_bytes || 0)), 1);
  $("#category-bars").className = categories.length ? "category-bars" : "category-bars empty-state";
  $("#category-bars").innerHTML = categories.length ? categories.slice(0, 6).map((item) => `
    <div class="category-row">
      <div class="category-label"><strong>${escapeHtml(item.category)}</strong><span>${formatBytes(item.size_bytes)}</span></div>
      <div class="category-track"><span style="--bar-width:${Math.max(5, Number(item.size_bytes || 0) / maxSize * 100)}%"></span></div>
    </div>`).join("") : "Run a hotspot scan to map the drive.";
}

function actionTag(action, stateValue = "") {
  const value = action || stateValue || "review";
  const className = value.includes("delete") || value.includes("uninstall") ? "tag tag-coral" : value.includes("active") || value === "executed" ? "tag tag-blue" : "tag";
  return `<span class="${className}">${escapeHtml(value.replaceAll("_", " "))}</span>`;
}

function renderHotspots() {
  const findings = state.dashboard.hotspots || [];
  const rows = findings.slice(0, 8).map((item) => `<tr>
    <td><strong>${escapeHtml(item.category)}</strong><small>${escapeHtml(item.path)}</small></td>
    <td>${formatBytes(item.size_bytes)}</td>
    <td>${formatBytes(item.reclaimable_bytes)}</td>
    <td>${actionTag(item.action_type_hint)}</td>
    <td>${actionTag(item.confidence)}</td>
  </tr>`).join("");
  const table = `<table><thead><tr><th>Finding</th><th>Observed</th><th>Predicted saving</th><th>Action hint</th><th>Confidence</th></tr></thead><tbody>${rows}</tbody></table>`;
  $("#top-hotspots").innerHTML = findings.length ? table : `<div class="empty-state">No hotspot findings yet.</div>`;

  const fullRows = findings.map((item) => `<tr>
    <td><strong>${escapeHtml(item.category)}</strong><small>${escapeHtml(item.path)}</small></td>
    <td>${escapeHtml(item.item_type)}</td><td>${formatBytes(item.size_bytes)}</td><td>${formatBytes(item.reclaimable_bytes)}</td>
    <td>${actionTag(item.action_type_hint)}</td><td>${actionTag(item.confidence)}</td>
  </tr>`).join("");
  $("#hotspot-table").innerHTML = findings.length ? `<table><thead><tr><th>Finding</th><th>Type</th><th>Observed</th><th>Predicted saving</th><th>Action</th><th>Confidence</th></tr></thead><tbody>${fullRows}</tbody></table>` : `<div class="empty-state">Run a hotspot scan to populate this view.</div>`;
}

function renderCaches() {
  const findings = state.dashboard.dev_caches || [];
  const rows = findings.map((item) => `<tr>
    <td><strong>${escapeHtml(item.ecosystem || item.details?.ecosystem || "cache")}</strong><small>${escapeHtml(item.path)}</small></td>
    <td>${formatBytes(item.size_bytes)}</td><td>${actionTag("delete cache")}</td>
    <td>${escapeHtml(item.details?.reclaim_method || "Recreateable package cache")}</td>
    <td>${escapeHtml(item.details?.expected_side_effects || "Packages may be downloaded again")}</td>
  </tr>`).join("");
  $("#cache-table").innerHTML = findings.length ? `<table><thead><tr><th>Cache target</th><th>Size</th><th>Action</th><th>Reclaim method</th><th>Expected side effects</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty-state">Run a developer cache scan to populate this view.</div>`;
}

function renderApps() {
  const apps = state.dashboard.apps || [];
  const rows = apps.slice(0, 60).map((item) => `<tr>
    <td><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(item.publisher || "Publisher unknown")}</small></td>
    <td>${formatBytes(item.estimated_installed_size_bytes)}</td>
    <td><span class="score">${item.usage_score}<span class="score-line"><span style="width:${Math.max(2, Math.min(100, item.usage_score))}%"></span></span></span></td>
    <td>${actionTag(item.candidate_action)}</td><td>${escapeHtml(item.usage_confidence)}</td><td>${formatDate(item.last_used)}</td>
  </tr>`).join("");
  $("#app-table").innerHTML = apps.length ? `<table><thead><tr><th>Application</th><th>Installed size</th><th>Usage score</th><th>Recommendation</th><th>Confidence</th><th>Last observed</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty-state">Run an app scan to populate this view.</div>`;
}

function queueActions(item) {
  if (item.state === "pending") return `<button class="button button-small button-primary" data-queue-action="approve" data-action-id="${item.id}">Approve</button><button class="text-button" data-queue-action="dismiss" data-action-id="${item.id}">Dismiss</button>`;
  if (item.state === "executed") return `<button class="button button-small button-quiet" data-queue-action="undo" data-action-id="${item.id}">Undo</button>`;
  if (item.state === "failed") return `<span class="tag tag-coral">needs review</span>`;
  return "";
}

function renderQueue() {
  const items = state.dashboard.queue || [];
  const preview = items.filter((item) => ["pending", "approved"].includes(item.state)).slice(0, 4);
  $("#queue-preview").className = preview.length ? "queue-preview" : "queue-preview empty-state";
  $("#queue-preview").innerHTML = preview.length ? preview.map((item) => `<div class="queue-item"><div class="queue-badge">${escapeHtml(item.action_type.slice(0, 2).toUpperCase())}</div><div><strong>${escapeHtml(item.human_summary)}</strong><span>${escapeHtml(item.state)} - action #${item.id}</span></div></div>`).join("") : "No queued actions. Scan recommendations will appear here.";
  const rows = items.map((item) => `<tr>
    <td><strong>#${item.id}</strong><small>${escapeHtml(item.human_summary)}</small></td>
    <td>${actionTag(item.action_type)}</td><td>${actionTag(item.state, item.state)}</td>
    <td><small>${escapeHtml(JSON.stringify(item.payload))}</small></td><td>${queueActions(item)}</td>
  </tr>`).join("");
  $("#queue-table").innerHTML = items.length ? `<table><thead><tr><th>Action</th><th>Type</th><th>State</th><th>Payload</th><th>Controls</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty-state">No actions in the queue.</div>`;
}

function render() {
  if (!state.dashboard) return;
  renderSummary();
  renderHotspots();
  renderCaches();
  renderApps();
  renderQueue();
  $("#last-refresh").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

async function getDashboard() {
  const response = await fetch("/api/dashboard", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not load dashboard data.");
  state.dashboard = payload;
  render();
}

async function post(path) {
  setBusy(true);
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Request failed.");
    if (payload.dashboard) state.dashboard = payload.dashboard;
    render();
    showNotice(payload.result?.output || payload.message || "Request completed.");
    return payload;
  } catch (error) {
    showNotice(error.message, "error");
    throw error;
  } finally {
    setBusy(false);
  }
}

async function runObservationScans() {
  setBusy(true);
  try {
    for (const [label, path] of [["hotspots", "/api/scans/hotspots"], ["developer caches", "/api/scans/dev-caches"], ["apps", "/api/scans/apps"]]) {
      showNotice(`Scanning ${label}...`);
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || `${label} scan failed.`);
      state.dashboard = payload.dashboard;
      render();
    }
    showNotice("Observation scans complete. No actions were executed.");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function switchView(view) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$('[data-section]').forEach((section) => section.classList.toggle("active", section.id === view));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-view]");
  if (nav) switchView(nav.dataset.view);
  const jump = event.target.closest("[data-jump]");
  if (jump) switchView(jump.dataset.jump);
  const scan = event.target.closest("[data-scan]");
  if (scan) await post(`/api/scans/${scan.dataset.scan}`);
  const queueAction = event.target.closest("[data-queue-action]");
  if (queueAction) await post(`/api/queue/${queueAction.dataset.actionId}/${queueAction.dataset.queueAction}`);
});

$("#refresh-button").addEventListener("click", async () => {
  setBusy(true);
  try { await getDashboard(); showNotice("Dashboard refreshed."); }
  catch (error) { showNotice(error.message, "error"); }
  finally { setBusy(false); }
});
$("#run-all-button").addEventListener("click", runObservationScans);
$("#execute-button").addEventListener("click", async () => {
  if (!window.confirm("Execute every approved action now? This can change files on disk.")) return;
  await post("/api/queue/execute");
});

getDashboard().catch((error) => showNotice(error.message, "error"));
