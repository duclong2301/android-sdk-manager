const $ = (id) => document.getElementById(id);

const model = {
  status: null,
  catalog: null,
  selected: new Set(),
  search: "",
  type: "recommended",
  activeTab: "available",
  lastBusy: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmtDate(seconds) {
  if (!seconds) return "Never updated";
  return new Date(seconds * 1000).toLocaleString();
}

async function api(path, options = {}) {
  const init = { ...options };
  if (init.body && typeof init.body !== "string") {
    init.body = JSON.stringify(init.body);
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  const res = await fetch(path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function setBadge(el, text, tone) {
  el.textContent = text;
  el.className = `badge ${tone}`;
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function installedSet() {
  const packages = model.status?.installed?.packages || [];
  return new Set(packages);
}

function versionParts(value) {
  const parts = String(value || "").match(/\d+/g);
  return parts ? parts.map(Number) : [0];
}

function compareVersionsDesc(a, b) {
  const left = versionParts(a);
  const right = versionParts(b);
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (right[index] || 0) - (left[index] || 0);
    if (diff) return diff;
  }
  return 0;
}

function packageType(path) {
  if (path === "platform-tools" || path === "emulator" || path.startsWith("cmdline-tools")) return "Tools";
  if (path.startsWith("platforms;")) return "Platforms";
  if (path.startsWith("build-tools;")) return "Build Tools";
  if (path.startsWith("system-images;")) return "System Images";
  if (path.startsWith("ndk;")) return "NDK";
  if (path.startsWith("extras;")) return "Extras";
  return "Other";
}

function packageName(path) {
  const android = path.match(/^platforms;android-(.+)$/);
  if (android) return `Android SDK Platform ${android[1]}`;
  const buildTools = path.match(/^build-tools;(.+)$/);
  if (buildTools) return `Android SDK Build-Tools ${buildTools[1]}`;
  if (path === "platform-tools") return "Android SDK Platform-Tools";
  if (path === "emulator") return "Android Emulator";
  if (path.startsWith("cmdline-tools")) return "Android SDK Command-line Tools";
  return path;
}

function packageSort(a, b) {
  const ranks = {
    Tools: 0,
    Platforms: 1,
    "Build Tools": 2,
    "System Images": 3,
    NDK: 4,
    Extras: 5,
    Other: 6,
  };
  const typeDiff = (ranks[a.type] ?? 9) - (ranks[b.type] ?? 9);
  if (typeDiff) return typeDiff;
  return compareVersionsDesc(a.path, b.path) || a.path.localeCompare(b.path);
}

function catalogMap() {
  return new Map((model.catalog?.packages || []).map((pkg) => [pkg.path, pkg]));
}

function renderStatus() {
  const status = model.status;
  if (!status) return;

  $("sdkRoot").value = status.sdkRoot;
  $("folderStatus").textContent = status.sdkRootExists ? "Available" : "Will be created";
  $("javaStatus").textContent = status.java.ok ? status.java.version : "JDK not found";
  $("toolsStatus").textContent = status.sdkmanager.ok ? "sdkmanager found" : "Not installed";
  $("envBlock").textContent = status.env.join("\n");

  if (status.java.ok && status.sdkmanager.ok) {
    setBadge($("overallBadge"), "Ready", "ok");
  } else if (status.sdkmanager.ok) {
    setBadge($("overallBadge"), "JDK required", "warn");
  } else {
    setBadge($("overallBadge"), "Setup required", "warn");
  }

  renderPackages();
}

function renderSelected() {
  const list = $("selectedList");
  $("selectedCount").textContent = String(model.selected.size);
  if (!model.selected.size) {
    list.className = "selected-list empty";
    list.textContent = "No packages selected.";
    return;
  }
  list.className = "selected-list";
  list.innerHTML = [...model.selected]
    .sort()
    .map((path) => `
      <div class="selected-item">
        <code>${escapeHtml(path)}</code>
        <button class="mini-remove" data-remove="${escapeHtml(path)}" title="Remove">x</button>
      </div>
    `)
    .join("");
}

function packageMatches(pkg) {
  const query = model.search.trim().toLowerCase();
  const recommended = new Set(model.catalog?.recommended || []);
  if (model.activeTab === "available" && model.type === "recommended" && !recommended.has(pkg.path)) return false;
  if (model.type !== "recommended" && model.type !== "all" && pkg.type !== model.type) return false;
  if (!query) return true;
  return `${pkg.path} ${pkg.name} ${pkg.revision} ${pkg.type}`.toLowerCase().includes(query);
}

function renderPackages() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === model.activeTab);
  });
  if (model.activeTab === "installed") {
    renderInstalledPackages();
    return;
  }

  const list = $("packageList");
  if (!model.catalog) {
    list.innerHTML = `<div class="empty-state">No catalog loaded yet. Click “Load catalog”.</div>`;
    return;
  }
  const installed = installedSet();
  const rows = model.catalog.packages.slice().sort(packageSort).filter(packageMatches).slice(0, 350);
  $("catalogMeta").textContent = `${model.catalog.packages.length} packages, updated ${fmtDate(model.catalog.fetchedAt)}.`;
  if (!rows.length) {
    list.innerHTML = `<div class="empty-state">No matching packages found.</div>`;
    return;
  }
  list.innerHTML = rows
    .map((pkg) => {
      const checked = model.selected.has(pkg.path) ? "checked" : "";
      const isInstalled = installed.has(pkg.path);
      const size = pkg.size ? `${Math.round(pkg.size / 1024 / 1024)} MB` : "";
      return `
        <label class="package-row">
          <input type="checkbox" data-package="${escapeHtml(pkg.path)}" ${checked} />
          <span class="package-main">
            <strong>${escapeHtml(pkg.name)}</strong>
            <code>${escapeHtml(pkg.path)}</code>
          </span>
          <span class="package-meta">
            ${isInstalled ? `<span class="pill installed">Installed</span>` : ""}
            <span class="pill">${escapeHtml(pkg.type)}</span>
            ${pkg.revision ? `<span class="pill">rev ${escapeHtml(pkg.revision)}</span>` : ""}
            ${size ? `<span class="pill">${size}</span>` : ""}
          </span>
        </label>
      `;
    })
    .join("");
}

function renderInstalledPackages() {
  const list = $("packageList");
  const installed = [...installedSet()];
  const updatedAt = model.status?.installed?.updatedAt;
  $("catalogMeta").textContent = `${installed.length} installed packages, updated ${fmtDate(updatedAt)}.`;
  if (!installed.length) {
    list.innerHTML = `<div class="empty-state">No installed packages loaded yet. Click “Refresh installed”.</div>`;
    return;
  }
  const byPath = catalogMap();
  const rows = installed
    .map((path) => {
      const fromCatalog = byPath.get(path);
      return fromCatalog || {
        path,
        name: packageName(path),
        type: packageType(path),
        revision: "",
        size: null,
      };
    })
    .sort(packageSort)
    .filter(packageMatches)
    .slice(0, 350);
  if (!rows.length) {
    list.innerHTML = `<div class="empty-state">No matching installed packages found.</div>`;
    return;
  }
  list.innerHTML = rows
    .map((pkg) => {
      const checked = model.selected.has(pkg.path) ? "checked" : "";
      return `
        <label class="package-row">
          <input type="checkbox" data-package="${escapeHtml(pkg.path)}" ${checked} />
          <span class="package-main">
            <strong>${escapeHtml(pkg.name)}</strong>
            <code>${escapeHtml(pkg.path)}</code>
          </span>
          <span class="package-meta">
            <span class="pill installed">Installed</span>
            <span class="pill">${escapeHtml(pkg.type)}</span>
            ${pkg.revision ? `<span class="pill">rev ${escapeHtml(pkg.revision)}</span>` : ""}
          </span>
        </label>
      `;
    })
    .join("");
}

function renderState(state) {
  const badge = $("jobBadge");
  const busy = Boolean(state.busy);
  if (busy) {
    setBadge(badge, state.job || "Running", "warn");
  } else if (state.ok === true) {
    setBadge(badge, "Completed", "ok");
  } else if (state.ok === false) {
    setBadge(badge, "Failed", "bad");
  } else {
    setBadge(badge, "Idle", "neutral");
  }

  const logs = state.logs || [];
  $("logOutput").textContent = logs.length
    ? logs.map((line) => `[${line.time}] ${line.message}`).join("\n")
    : "Ready.";
  $("logOutput").scrollTop = $("logOutput").scrollHeight;

  document.querySelectorAll("button").forEach((button) => {
    if (["refreshStatus", "copyEnv", "copyLog"].includes(button.id)) return;
    button.disabled = busy;
  });

  if (model.lastBusy && !busy) {
    refreshStatus();
    if (state.ok) loadCatalog(false);
  }
  model.lastBusy = busy;
}

async function refreshStatus() {
  try {
    model.status = await api("/api/status");
    renderStatus();
  } catch (err) {
    setBadge($("overallBadge"), "Error", "bad");
    $("logOutput").textContent = err.message;
  }
}

async function loadCatalog(showErrors = true) {
  try {
    $("catalogMeta").textContent = "Loading SDK catalog...";
    model.catalog = await api("/api/catalog");
    renderPackages();
  } catch (err) {
    if (showErrors) $("packageList").innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

async function startJob(path, body = {}) {
  const result = await api(path, { method: "POST", body });
  if (!result.started) {
    throw new Error("Another task is already running.");
  }
  await pollState();
}

async function pollState() {
  try {
    renderState(await api("/api/state"));
  } catch (err) {
    $("logOutput").textContent = err.message;
  }
}

function selectedPackages() {
  return [...model.selected].sort();
}

function wireEvents() {
  $("refreshStatus").addEventListener("click", refreshStatus);
  $("saveSdkRoot").addEventListener("click", async () => {
    await api("/api/config", { method: "POST", body: { sdkRoot: $("sdkRoot").value.trim() } });
    await refreshStatus();
  });
  $("browseSdkRoot").addEventListener("click", async () => {
    const result = await api("/api/select-sdk-folder", {
      method: "POST",
      body: { sdkRoot: $("sdkRoot").value.trim() },
    });
    if (result.selected) {
      $("sdkRoot").value = result.config.sdkRoot;
      await refreshStatus();
    }
  });
  $("installTools").addEventListener("click", () => startJob("/api/install-tools").catch(alert));
  $("acceptLicenses").addEventListener("click", () => startJob("/api/accept-licenses").catch(alert));
  $("listInstalled").addEventListener("click", () => startJob("/api/list-installed").catch(alert));
  $("openFolder").addEventListener("click", () => api("/api/open-sdk-folder", { method: "POST" }).catch(alert));
  $("refreshCatalog").addEventListener("click", () => loadCatalog(true));
  $("copyEnv").addEventListener("click", async () => {
    await copyText($("envBlock").textContent);
  });
  $("copyLog").addEventListener("click", async () => {
    const button = $("copyLog");
    await copyText($("logOutput").textContent);
    const previous = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => {
      button.textContent = previous;
    }, 1200);
  });

  $("search").addEventListener("input", (event) => {
    model.search = event.target.value;
    renderPackages();
  });
  $("typeFilter").addEventListener("change", (event) => {
    model.type = event.target.value;
    renderPackages();
  });
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      model.activeTab = button.dataset.tab;
      renderPackages();
    });
  });
  $("packageList").addEventListener("change", (event) => {
    const path = event.target?.dataset?.package;
    if (!path) return;
    if (event.target.checked) model.selected.add(path);
    else model.selected.delete(path);
    renderSelected();
  });
  $("selectedList").addEventListener("click", (event) => {
    const path = event.target?.dataset?.remove;
    if (!path) return;
    model.selected.delete(path);
    renderSelected();
    renderPackages();
  });
  $("installSelected").addEventListener("click", () => {
    const packages = selectedPackages();
    if (!packages.length) return alert("No packages selected.");
    startJob("/api/install-packages", {
      packages,
      acceptLicenses: $("autoLicenses").checked,
    }).catch(alert);
  });
  $("uninstallSelected").addEventListener("click", () => {
    const packages = selectedPackages();
    if (!packages.length) return alert("No packages selected.");
    const ok = confirm(`Uninstall ${packages.length} selected packages?`);
    if (ok) startJob("/api/uninstall-packages", { packages }).catch(alert);
  });
}

wireEvents();
refreshStatus();
pollState();
setInterval(pollState, 1200);
