/* Lab2Startup desktop app — vanilla JS SPA on top of the FastAPI backend. */
"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  boot: null,
  runs: [],
  selectedRunId: null,
  bundle: null,
  selectedReportId: null,
  tab: "top",
  topN: 10,
  jobId: null,
  jobTimer: null,
  tracesCache: {}, // runId -> {traces, summary}
  auditCache: {}, // runId -> audit payload
  filters: {
    viewMode: "researchers",
    minScore: 40,
    recommendation: "All",
    conference: "All",
    year: "All",
    topic: "All",
    thesisFit: "All",
    europe: "All",
    onlyWithResults: false,
    showDevTools: false,
  },
};

const RECOMMENDATION_BADGES = {
  take_meeting: ["green", "🟢 Take meeting"],
  monitor_monthly: ["yellow", "🟡 Monitor monthly"],
  add_to_watchlist: ["orange", "🟠 Watchlist"],
  ignore_for_now: ["gray", "⚪ Low priority"],
};

const FIT_COLORS = { strong: "green", moderate: "blue", weak: "orange", unclear: "gray" };

const SCORE_COMPONENTS = [
  ["Research quality", "research_quality", 20,
    "Best paper score among the candidate's publications: conference tier (NeurIPS = 12, others = 8), recency bonus (+6 for 2024+, +4 for 2023+, +2 older), plus up to +2 from Semantic Scholar citations. Capped at 20."],
  ["Applied relevance", "applied_relevance", 20,
    "Highest topic score across the candidate's papers. Default topics: AI agents 18, robotics 17, biotech AI 19; unknown topics = 10. Fund profiles can override via topic_scores."],
  ["Team continuity", "team_continuity", 15,
    "Coauthor network size as a proxy for team-formation potential: 6+ coauthors = 15, 4–5 = 12, 2–3 = 9, 1 = 6, solo = 3."],
  ["Project momentum", "open_source_or_project_momentum", 15,
    "Points from commercialization signals only: GitHub URL +8, OpenReview/arXiv +5, otherwise +7/+5/+3 by evidence strength. Capped at 15."],
  ["Signal strength", "commercialization_signal_strength", 20,
    "Founder/commercialization evidence from attached signals. Each signal maps type × strength to points; total uses max(best single signal, sum of half-points). Capped at 20."],
  ["Recency", "recency", 10, "Most recent paper year: 2024+ = 10, 2023 = 7, older = 4."],
];

const SIGNAL_TYPE_POINTS = {
  confirmed_founder: { high: 20, medium: 16, low: 12 },
  possible_founder: { high: 14, medium: 11, low: 8 },
  commercialization: { high: 12, medium: 9, low: 5 },
};

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  return response.json();
}

function setLoading(visible, text) {
  $("#loading-overlay").classList.toggle("hidden", !visible);
  if (text) $("#loading-text").textContent = text;
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function recBadge(action) {
  const [color, label] = RECOMMENDATION_BADGES[action] || ["gray", titleCase(action)];
  return `<span class="badge ${color}">${esc(label)}</span>`;
}

function recLabel(action) {
  const labels = state.boot?.recommendation_labels || {};
  return labels[action] || titleCase(action);
}

// Minimal markdown renderer for report content (headings, bold, links, lists, tables).
function mdToHtml(md) {
  const inline = (text) => esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/_([^_]+)_/g, "<i>$1</i>");

  const lines = String(md || "").split("\n");
  const out = [];
  let listOpen = false;
  let tableRows = [];

  const flushList = () => { if (listOpen) { out.push("</ul>"); listOpen = false; } };
  const flushTable = () => {
    if (!tableRows.length) return;
    const cells = tableRows.map((row) => row.split("|").slice(1, -1).map((cell) => cell.trim()));
    const body = cells.filter((row) => !row.every((cell) => /^:?-{2,}:?$/.test(cell)));
    const [head, ...rest] = body;
    let html = '<div class="table-wrap"><table><thead><tr>';
    html += head.map((cell) => `<th>${inline(cell)}</th>`).join("");
    html += "</tr></thead><tbody>";
    for (const row of rest) html += `<tr>${row.map((cell) => `<td class="wrap">${inline(cell)}</td>`).join("")}</tr>`;
    html += "</tbody></table></div>";
    out.push(html);
    tableRows = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\|.*\|$/.test(line.trim())) { flushList(); tableRows.push(line.trim()); continue; }
    flushTable();
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) { flushList(); out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); continue; }
    if (/^-\s+/.test(line)) {
      if (!listOpen) { out.push("<ul>"); listOpen = true; }
      out.push(`<li>${inline(line.replace(/^-\s+/, ""))}</li>`);
      continue;
    }
    flushList();
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  flushList();
  flushTable();
  return out.join("\n");
}

function metricRow(pairs) {
  return `<div class="metric-row">${pairs.map(([label, value]) =>
    `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`).join("")}</div>`;
}

function hbarChart(rows, maxValue) {
  const max = maxValue || Math.max(...rows.map((row) => row.value), 1);
  return `<div class="hbar-chart">${rows.map((row) => `
    <div class="hbar-row">
      <div class="hbar-label" title="${esc(row.label)}">${esc(row.label)}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.max(1, (100 * row.value) / max)}%"></div></div>
      <div class="hbar-value">${esc(row.value)}${row.suffix || ""}</div>
    </div>`).join("")}</div>`;
}

function makeTable(container, columns, rows, { rowIds = null, selectedId = null, onSelect = null } = {}) {
  const clickable = Boolean(rowIds && onSelect);
  let html = '<div class="table-wrap"><table><thead><tr>';
  html += columns.map((col) => `<th>${esc(col)}</th>`).join("");
  html += "</tr></thead><tbody>";
  rows.forEach((row, index) => {
    const id = rowIds ? rowIds[index] : null;
    const classes = [clickable ? "clickable" : "", id && id === selectedId ? "selected" : ""].join(" ").trim();
    html += `<tr${classes ? ` class="${classes}"` : ""} data-index="${index}">`;
    html += columns.map((col) => `<td>${row[col] ?? "—"}</td>`).join("");
    html += "</tr>";
  });
  html += "</tbody></table></div>";
  container.innerHTML = html;
  if (clickable) {
    container.querySelectorAll("tbody tr").forEach((tr) => {
      tr.addEventListener("click", () => onSelect(rowIds[Number(tr.dataset.index)]));
    });
  }
}

function expander(title, bodyHtml, open = false) {
  return `<details class="expander"${open ? " open" : ""}><summary>${esc(title)}</summary><div class="expander-body">${bodyHtml}</div></details>`;
}

// ---------------------------------------------------------------------------
// Derived data helpers
// ---------------------------------------------------------------------------

function researcherIdFromReportId(reportId) {
  return reportId.startsWith("report_researcher_") ? reportId.replace(/^report_/, "") : null;
}

function bundleMaps() {
  const bundle = state.bundle;
  return {
    researchersById: Object.fromEntries((bundle.researchers || []).map((r) => [r.id, r])),
    clustersById: Object.fromEntries((bundle.clusters || []).map((c) => [c.id, c])),
  };
}

function confYearLabel(ctx) {
  if (!ctx || (!ctx.conferences.length && !ctx.years.length)) return "Unknown conference / year";
  if (ctx.conferences.length === 1 && ctx.years.length === 1) return `${ctx.conferences[0]} ${ctx.years[0]}`;
  const parts = [];
  if (ctx.conferences.length) parts.push(ctx.conferences.join(", "));
  if (ctx.years.length) parts.push(ctx.years.join(", "));
  return parts.join(" · ");
}

function passesThesisFilter(researcherId) {
  const { thesisFit, europe } = state.filters;
  if (thesisFit === "All" && europe === "All") return true;
  const assessment = (state.bundle.thesis_fit || {})[researcherId];
  if (!assessment) return thesisFit === "All" && (europe === "All" || europe === "Unknown");
  if (thesisFit === "Strong only" && assessment.fit_level !== "strong") return false;
  if (thesisFit === "Strong+Moderate" && !["strong", "moderate"].includes(assessment.fit_level)) return false;
  if (europe === "Yes" && assessment.europe_nexus !== "yes") return false;
  if (europe === "Unknown" && assessment.europe_nexus !== "unclear") return false;
  return true;
}

function filterResearcherReports({ minScore = null, recommendation = null, useThesis = true } = {}) {
  const f = state.filters;
  const min = minScore === null ? f.minScore : minScore;
  const rec = recommendation === undefined ? null : (recommendation === null ? (f.recommendation === "All" ? null : f.recommendation) : recommendation);
  const { researchersById } = bundleMaps();
  const contexts = state.bundle.contexts || {};

  const reports = (state.bundle.reports || []).filter((report) => {
    if (!report.id.startsWith("report_researcher_")) return false;
    if (report.startup_likelihood_score < min) return false;
    if (rec && report.recommendation !== rec) return false;
    const rid = researcherIdFromReportId(report.id);
    if (!rid || !researchersById[rid]) return false;
    const ctx = contexts[rid] || { conferences: [], years: [], topics: [] };
    if (f.conference !== "All" && !ctx.conferences.some((c) => c.toLowerCase() === f.conference.toLowerCase())) return false;
    if (f.year !== "All" && !ctx.years.includes(Number(f.year))) return false;
    if (f.topic !== "All" && !ctx.topics.some((t) => t.toLowerCase() === f.topic.toLowerCase())) return false;
    if (useThesis && state.boot?.fund?.thesis_fit && state.bundle.thesis_fit && !passesThesisFilter(rid)) return false;
    return true;
  });

  return reports.sort((a, b) =>
    b.startup_likelihood_score - a.startup_likelihood_score ||
    a.researcher_or_cluster.localeCompare(b.researcher_or_cluster));
}

function filterClusterReports() {
  const f = state.filters;
  const { clustersById } = bundleMaps();
  const rec = f.recommendation === "All" ? null : f.recommendation;
  return (state.bundle.reports || []).filter((report) => {
    if (!report.id.startsWith("report_cluster_")) return false;
    if (report.startup_likelihood_score < f.minScore) return false;
    if (rec && report.recommendation !== rec) return false;
    const cluster = clustersById[report.id.replace(/^report_/, "")];
    if (f.topic !== "All" && cluster && cluster.topic.toLowerCase() !== f.topic.toLowerCase()) return false;
    return true;
  }).sort((a, b) =>
    b.startup_likelihood_score - a.startup_likelihood_score ||
    a.researcher_or_cluster.localeCompare(b.researcher_or_cluster));
}

function filteredReports() {
  return state.filters.viewMode === "researchers" ? filterResearcherReports({}) : filterClusterReports();
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

function runLabel(run) {
  const created = run.created_at ? run.created_at.slice(0, 10) : "unknown";
  const time = run.created_at && run.created_at.length >= 16 ? ` ${run.created_at.slice(11, 16)} UTC` : "";
  const stats = run.paper_count !== null && run.paper_count !== undefined ? `, ${run.paper_count} papers` : "";
  return `${run.conference} ${run.year} — ${created}${time} (${run.status}${stats})`;
}

function completeRuns() {
  return state.runs.filter((run) => run.status === "complete");
}

function displayRuns() {
  const complete = completeRuns();
  if (!state.filters.onlyWithResults) return complete;
  const withResults = complete.filter((run) => run.has_results);
  return withResults.length ? withResults : complete;
}

function pickPreferredRunId(runs) {
  if (!runs.length) return null;
  if (runs.some((run) => run.id === state.selectedRunId)) return state.selectedRunId;
  let best = runs[0];
  for (const run of runs) {
    if ((run.paper_count || 0) > (best.paper_count || 0) ||
        ((run.paper_count || 0) === (best.paper_count || 0) && run.created_at > best.created_at)) {
      best = run;
    }
  }
  return best.id;
}

// ---------------------------------------------------------------------------
// Sidebar rendering
// ---------------------------------------------------------------------------

function renderSidebar() {
  const boot = state.boot;
  const fund = boot.fund;

  $("#brand-caption").textContent = `Founder signal monitoring · mode: ${boot.mode}`;

  $("#warnings").innerHTML = (boot.warnings || [])
    .map((warning) => `<div class="error-box">${esc(warning)}</div>`).join("");

  let fundHtml = "";
  if (fund) {
    const description = fund.description.length > 180 ? `${fund.description.slice(0, 180)}…` : fund.description;
    const scopeRows = fund.conferences.map((c) =>
      `<div><b>${esc(c.name)}</b> ${esc(c.priority)} · ${esc(c.sources.join(" / "))}</div>`).join("");
    fundHtml = `
      <div><b>Fund:</b> ${esc(fund.name)}</div>
      <p class="muted small">${esc(description)}</p>
      ${expander(`Conferences in scope (${fund.conferences.length})`, `<div class="kv-list small">${scopeRows}</div>`)}
    `;
  }
  $("#fund-info").innerHTML = fundHtml;

  renderRunSelect();
  renderNewRunForm();
  renderFilterOptions();

  $("#dev-refresh-section").classList.toggle("hidden", boot.is_production);
  $("#new-run-section").classList.toggle("hidden", !boot.is_production);
  $("#thesis-filters").classList.toggle("hidden", !(fund && fund.thesis_fit));
  $("#integrations").innerHTML = `Active sources: ${esc((boot.active_integrations || []).join(", "))}`;
}

function renderRunSelect() {
  const select = $("#run-select");
  const runs = displayRuns();
  const hint = $("#run-empty-hint");

  if (!runs.length) {
    select.innerHTML = '<option value="">No stored runs yet</option>';
    hint.textContent = state.boot.is_production
      ? "No stored runs yet. Start one below or run: python run_pipeline.py --conference NeurIPS --year 2024"
      : "Development mode — data loads live from the pipeline.";
    hint.classList.remove("hidden");
    return;
  }
  hint.classList.add("hidden");

  if (!runs.some((run) => run.id === state.selectedRunId)) {
    state.selectedRunId = pickPreferredRunId(runs);
  }
  select.innerHTML = runs.map((run) =>
    `<option value="${esc(run.id)}"${run.id === state.selectedRunId ? " selected" : ""}>${esc(runLabel(run))}</option>`).join("");

  const complete = completeRuns();
  if (state.filters.onlyWithResults && !complete.some((run) => run.has_results) && complete.length) {
    hint.textContent = "No runs with papers yet — showing all complete runs.";
    hint.classList.remove("hidden");
  }
}

function newRunTargets() {
  const fund = state.boot.fund;
  const conferences = fund ? fund.conferences.map((c) => c.name) : ["NeurIPS"];
  const scope = $("#run-scope").value;
  if (scope === "single") {
    const value = $("#run-conference")?.value;
    return value ? [value] : conferences.slice(0, 1);
  }
  if (scope === "high") {
    return fund ? fund.high_priority_conferences : conferences.slice(0, 4);
  }
  return Array.from(document.querySelectorAll("#run-target-wrap input[type=checkbox]:checked")).map((el) => el.value);
}

function renderNewRunForm() {
  const fund = state.boot.fund;
  const conferences = fund ? fund.conferences.map((c) => c.name) : ["NeurIPS"];
  const wrap = $("#run-target-wrap");
  const scope = $("#run-scope").value;

  if (scope === "single") {
    wrap.innerHTML = `<label class="field"><span>Conference</span>
      <select id="run-conference">${conferences.map((name) => {
        const label = fund ? (fund.conferences.find((c) => c.name === name)?.priority
          ? `${name} (${fund.conferences.find((c) => c.name === name).sources.join("/")}, ${fund.conferences.find((c) => c.name === name).priority})` : name) : name;
        return `<option value="${esc(name)}">${esc(label)}</option>`;
      }).join("")}</select></label>`;
    $("#run-conference").addEventListener("change", renderPaperSourceOptions);
  } else if (scope === "high") {
    const targets = fund ? fund.high_priority_conferences : conferences.slice(0, 4);
    wrap.innerHTML = `<p class="muted small">Will run: ${esc(targets.slice(0, 8).join(", "))}${targets.length > 8 ? "…" : ""}</p>`;
  } else {
    const defaults = new Set(fund ? fund.high_priority_conferences.slice(0, 3) : conferences.slice(0, 3));
    wrap.innerHTML = `<div class="checkbox-list">${conferences.map((name) =>
      `<label class="check-row"><input type="checkbox" value="${esc(name)}"${defaults.has(name) ? " checked" : ""}/><span>${esc(name)}</span></label>`).join("")}</div>`;
  }
  renderPaperSourceOptions();
}

function renderPaperSourceOptions() {
  const fund = state.boot.fund;
  const targets = newRunTargets();
  const entry = fund && targets.length ? fund.conferences.find((c) => c.name === targets[0]) : null;
  const sources = entry ? entry.sources : ["openreview", "openalex", "json"];
  $("#paper-source").innerHTML = sources.map((source) => `<option value="${esc(source)}">${esc(source)}</option>`).join("");
}

function renderFilterOptions() {
  const options = state.bundle?.options || { conferences: [], years: [], topics: [] };
  const recEntries = Object.entries(state.boot.recommendation_labels || {});
  $("#filter-recommendation").innerHTML = ['<option value="All">All</option>',
    ...recEntries.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`)].join("");
  const fill = (sel, values, current) => {
    $(sel).innerHTML = ['<option value="All">All</option>',
      ...values.map((v) => `<option value="${esc(v)}"${String(v) === String(current) ? " selected" : ""}>${esc(v)}</option>`)].join("");
  };
  fill("#filter-conference", options.conferences, state.filters.conference);
  fill("#filter-year", options.years, state.filters.year);
  fill("#filter-topic", options.topics, state.filters.topic);
  $("#filter-recommendation").value = state.filters.recommendation;
}

function renderDatasetStats() {
  const bundle = state.bundle;
  if (!bundle) { $("#dataset-stats").innerHTML = ""; return; }
  $("#dataset-stats").innerHTML = `
    Papers: <b>${bundle.papers.length}</b> ·
    Researchers: <b>${bundle.researchers.length}</b> ·
    Signals: <b>${bundle.signals.length}</b>`;
}

// ---------------------------------------------------------------------------
// Main rendering
// ---------------------------------------------------------------------------

function renderMain() {
  renderDatasetStats();
  renderContextHeader();
  $("#tabs [data-tab=dev]").classList.toggle("hidden", !state.filters.showDevTools);
  if (state.tab === "dev" && !state.filters.showDevTools) state.tab = "top";
  document.querySelectorAll("#tabs button").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === state.tab));
  $("#tab-top").classList.toggle("hidden", state.tab !== "top");
  $("#tab-explore").classList.toggle("hidden", state.tab !== "explore");
  $("#tab-dev").classList.toggle("hidden", state.tab !== "dev");

  const bundle = state.bundle;
  if (!bundle || (!bundle.papers.length && !bundle.reports.length)) {
    renderEmptyState();
    return;
  }

  if (state.tab === "top") renderTopTab();
  else if (state.tab === "explore") renderExploreTab();
  else renderDevTab();
}

function activeRun() {
  return state.bundle?.run || null;
}

function renderContextHeader() {
  const bundle = state.bundle;
  const container = $("#context-header");
  if (!bundle) { container.innerHTML = ""; return; }
  const run = activeRun();
  const papers = bundle.papers;
  const f = state.filters;
  const conference = f.conference !== "All" ? f.conference : (run ? run.conference : (papers[0]?.conference || "—"));
  const year = f.year !== "All" ? f.year : (run ? run.year : (papers[0]?.year || "—"));

  const filterBits = [];
  if (f.conference !== "All") filterBits.push(`conference <b>${esc(f.conference)}</b>`);
  if (f.year !== "All") filterBits.push(`year <b>${esc(f.year)}</b>`);
  if (f.topic !== "All") filterBits.push(`topic <b>${esc(f.topic)}</b>`);
  const scope = run
    ? `Stored run <b>${esc(run.conference)} ${run.year}</b> via <code>${esc(run.paper_source)}</code>`
    : `Live dataset · papers from <b>${esc(conference)} ${esc(year)}</b>`;

  let diffHtml = "";
  if (f.viewMode === "researchers") diffHtml = diffPanelHtml();

  container.innerHTML = `
    ${metricRow([["Conference", conference], ["Year", year], ["Researchers", bundle.researchers.length], ["Papers in run", papers.length]])}
    <p class="caption">${scope}${filterBits.length ? ` · Filtered by ${filterBits.join(", ")}` : ""}</p>
    ${diffHtml}
  `;
  const diffTableWrap = $("#diff-table-holder");
  if (diffTableWrap) renderDiffTable(diffTableWrap);
}

function diffPanelHtml() {
  const diff = state.bundle.diff;
  let body;
  if (!diff) {
    body = '<p class="caption">No diff computed for this run yet.</p>';
  } else if (!diff.prior_run_id) {
    body = '<div class="info-box">First run for this conference — no prior comparison.</div>';
  } else {
    const s = diff.summary;
    body = `
      ${metricRow([["Total changes", s.total_deltas], ["New take meeting", s.new_take_meeting], ["New researchers", s.new_researchers], ["Score increases", s.score_increases]])}
      <p class="caption">Compared to prior run <code>${esc(diff.prior_run_id)}</code></p>
      ${diff.deltas.length
        ? '<p class="caption">Click a row to open that candidate in Explore &amp; details.</p><div id="diff-table-holder"></div>'
        : '<div class="success-box">No material changes vs the prior run.</div>'}
    `;
  }
  return expander("Changes since last run", body, Boolean(diff && diff.prior_run_id && diff.deltas.length));
}

function renderDiffTable(container) {
  const diff = state.bundle.diff;
  const rows = diff.deltas.map((delta) => ({
    Name: esc(delta.name),
    Change: `<span class="badge blue">${esc(titleCase(delta.change_type))}</span>`,
    Detail: `<span class="wrap">${esc((delta.detail || "").slice(0, 120))}</span>`,
    Before: esc(delta.before ?? "—"),
    After: esc(delta.after ?? "—"),
  }));
  const rowIds = diff.deltas.map((delta) => `report_${delta.researcher_id}`);
  makeTable(container, ["Name", "Change", "Detail", "Before", "After"], rows, {
    rowIds,
    selectedId: state.selectedReportId,
    onSelect: (reportId) => { state.selectedReportId = reportId; state.tab = "explore"; renderMain(); },
  });
}

function renderEmptyState() {
  const container = state.tab === "explore" ? $("#tab-explore") : $("#tab-top");
  const complete = completeRuns();
  const run = state.runs.find((r) => r.id === state.selectedRunId) || null;

  if (state.boot.is_production && !complete.length) {
    container.innerHTML = `<div class="info-box">No pipeline data yet. Use <b>Run pipeline &amp; save</b> in the sidebar, or from the CLI:<br/><code>python run_pipeline.py --conference NeurIPS --year 2024</code></div>`;
    return;
  }

  let html = '<div class="warning-box">No papers or candidates in the current dataset.</div>';
  if (run) {
    html += `<p><b>Selected run:</b> ${esc(run.conference)} ${run.year} (${esc(run.status)}, ${esc(run.paper_source)})</p>`;
    if (run.error_message) html += `<div class="error-box">${esc(run.error_message)}</div>`;
    else if (!run.paper_count) html += '<div class="info-box">This run completed but returned <b>0 papers</b> (empty fetch, fund filter, or no matching authors).</div>';
  }
  const withResults = complete.filter((r) => r.has_results);
  if (withResults.length && (!run || !run.has_results)) {
    html += `<div class="success-box"><b>${withResults.length}</b> stored run(s) have paper data. Turn on <b>Only show runs with results</b> in the sidebar, or pick one from <b>Stored run</b>.</div>`;
  }
  if (complete.length) {
    html += '<div class="card"><h3>Recent stored runs</h3><div id="empty-runs-table"></div></div>';
  }
  container.innerHTML = html;
  if (complete.length) {
    makeTable($("#empty-runs-table"), ["Conference", "Year", "Status", "Papers", "Researchers", "Created"],
      complete.slice(0, 15).map((r) => ({
        Conference: esc(r.conference), Year: r.year, Status: esc(r.status),
        Papers: r.paper_count ?? "—", Researchers: r.researcher_count ?? "—",
        Created: r.created_at ? r.created_at.slice(0, 10) : "—",
      })));
  }
}

function filterMissHtml() {
  const total = (state.bundle.reports || []).filter((r) => r.id.startsWith("report_researcher_")).length;
  const aboveMin = (state.bundle.reports || []).filter((r) =>
    r.id.startsWith("report_researcher_") && r.startup_likelihood_score >= state.filters.minScore).length;
  const afterMeta = filterResearcherReports({ minScore: 0, useThesis: false }).length;
  return `
    <div class="warning-box">No candidates match the current sidebar filters.</div>
    <div class="info-box"><b>${total}</b> researchers in this run · <b>${aboveMin}</b> at or above min score <b>${state.filters.minScore}</b> · <b>${afterMeta}</b> after conference/year/topic filters.<br/>
    Try lowering <b>Minimum score</b> to <b>0</b>, set filters to <b>All</b>, or check that Perplexity signals finished for this run.</div>`;
}

// ---------------------------------------------------------------------------
// Top prospects tab
// ---------------------------------------------------------------------------

function leaderboardEntries(topN) {
  const ranked = filterResearcherReports({ minScore: 0, recommendation: undefined, useThesis: false });
  const { researchersById } = bundleMaps();
  const contexts = state.bundle.contexts || {};
  const regions = state.bundle.regions || {};
  return ranked.slice(0, topN).map((report, index) => {
    const rid = researcherIdFromReportId(report.id);
    return {
      rank: index + 1,
      report,
      researcher: rid ? researchersById[rid] : null,
      ctx: rid ? contexts[rid] : null,
      region: rid ? regions[rid] : null,
      topSignal: topSignalSummary(report),
    };
  });
}

function topSignalSummary(report) {
  if (!report.signals || !report.signals.length) return { label: "No signals", url: null };
  const typePriority = { confirmed_founder: 0, possible_founder: 1, commercialization: 2, no_signal: 3 };
  const strengthPriority = { high: 0, medium: 1, low: 2 };
  const best = [...report.signals].sort((a, b) =>
    (typePriority[a.signal_type] ?? 9) - (typePriority[b.signal_type] ?? 9) ||
    (strengthPriority[a.evidence_strength] ?? 9) - (strengthPriority[b.evidence_strength] ?? 9))[0];
  return { label: titleCase(best.signal_type), url: best.source_url };
}

function renderTopTab() {
  const container = $("#tab-top");
  if (state.filters.viewMode === "clusters") {
    const reports = filterClusterReports();
    let html = '<div class="info-box">Switch to <b>Researchers</b> in the sidebar to see the highest-potential leaderboard.</div>';
    html += '<div class="card"><h3>Top clusters</h3><div id="cluster-top-table"></div></div>';
    container.innerHTML = html;
    makeTable($("#cluster-top-table"), ["Name", "Score", "Recommendation", "Signals"],
      reports.slice(0, 15).map((report) => ({
        Name: esc(report.researcher_or_cluster),
        Score: report.startup_likelihood_score,
        Recommendation: recBadge(report.recommendation),
        Signals: report.signals.length,
      })), {
        rowIds: reports.slice(0, 15).map((report) => report.id),
        selectedId: state.selectedReportId,
        onSelect: (id) => { state.selectedReportId = id; state.tab = "explore"; renderMain(); },
      });
    return;
  }

  const allResearcherReports = (state.bundle.reports || []).filter((r) => r.id.startsWith("report_researcher_"));
  if (!allResearcherReports.length) {
    container.innerHTML = '<div class="warning-box">This run has no researcher scores yet.</div>';
    return;
  }

  const entries = leaderboardEntries(state.topN);
  if (!entries.length) {
    container.innerHTML = '<div class="warning-box">No researcher scores available for this run (check conference/year/topic filters).</div>';
    return;
  }

  const recCounts = {};
  for (const report of allResearcherReports) {
    recCounts[report.recommendation] = (recCounts[report.recommendation] || 0) + 1;
  }

  const meetingReady = filterResearcherReports({ minScore: 0, recommendation: "take_meeting", useThesis: false });

  let html = `<h3 style="margin:4px 0 2px">Highest potential researchers</h3>
    <p class="caption">Ranked by startup likelihood score — research quality, applied relevance, team network, enrichment signals, and recency.</p>
    <label class="field" style="max-width:280px"><span>Show top N researchers: <b>${state.topN}</b></span>
      <input type="range" id="top-n-slider" min="5" max="25" step="1" value="${state.topN}" /></label>`;

  html += metricRow([
    ["Take meeting", recCounts.take_meeting || 0],
    ["Monitor monthly", recCounts.monitor_monthly || 0],
    ["Watchlist", recCounts.add_to_watchlist || 0],
    ["Top score", entries[0].report.startup_likelihood_score],
  ]);

  const medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
  html += `<div class="podium">${entries.slice(0, 3).map((entry) => `
    <div class="card">
      <div class="rank">${medals[entry.rank] || "▫️"} #${entry.rank}</div>
      <div class="name">${esc(entry.report.researcher_or_cluster)}</div>
      <div class="score">${entry.report.startup_likelihood_score}</div>
      <p class="caption">${esc(confYearLabel(entry.ctx))} · ${esc(entry.researcher?.affiliation || "Unknown")}${entry.region ? ` · ${esc(entry.region)}` : ""} · ${esc(entry.topSignal.label)}</p>
      ${recBadge(entry.report.recommendation)}
      <div style="margin-top:10px"><button class="linkish" data-view-report="${esc(entry.report.id)}">View in Explore tab →</button></div>
    </div>`).join("")}</div>`;

  html += hbarChart(entries.map((entry) => ({
    label: entry.report.researcher_or_cluster,
    value: entry.report.startup_likelihood_score,
  })), 100);

  if (meetingReady.length) {
    const names = meetingReady.slice(0, 5).map((report) => report.researcher_or_cluster).join(", ");
    html += `<div class="success-box"><b>Meeting-ready (${meetingReady.length}):</b> ${esc(names)}</div>`;
  }

  html += '<div class="card"><h3>Full leaderboard</h3><p class="caption">Click a row to open the quick view below.</p><div id="leaderboard-table"></div></div>';
  html += '<div id="quick-view"></div>';
  container.innerHTML = html;

  $("#top-n-slider").addEventListener("input", (event) => {
    state.topN = Number(event.target.value);
    renderTopTab();
  });
  container.querySelectorAll("[data-view-report]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.selectedReportId = btn.dataset.viewReport;
      state.tab = "explore";
      renderMain();
    });
  });

  const rowIds = entries.map((entry) => entry.report.id);
  if (!rowIds.includes(state.selectedReportId)) state.selectedReportId = rowIds[0];
  makeTable($("#leaderboard-table"),
    ["Rank", "Name", "Score", "Conference / year", "Affiliation", "Region", "Role", "Recommendation", "Signals", "Top signal"],
    entries.map((entry) => ({
      Rank: entry.rank,
      Name: `<b>${esc(entry.report.researcher_or_cluster)}</b>`,
      Score: entry.report.startup_likelihood_score,
      "Conference / year": esc(confYearLabel(entry.ctx)),
      Affiliation: esc(entry.researcher?.affiliation || "—"),
      Region: esc(entry.region || "—"),
      Role: esc(entry.researcher?.role || "—"),
      Recommendation: recBadge(entry.report.recommendation),
      Signals: entry.report.signals.length,
      "Top signal": esc(entry.topSignal.label),
    })), {
      rowIds,
      selectedId: state.selectedReportId,
      onSelect: (id) => { state.selectedReportId = id; renderTopTab(); },
    });

  renderQuickView($("#quick-view"), entries.find((entry) => entry.report.id === state.selectedReportId));
}

function linkButtonsHtml(researcherId) {
  const links = (state.bundle.links || {})[researcherId] || {};
  const buttons = [["GitHub", links.github], ["LinkedIn", links.linkedin], ["OpenReview", links.openreview], ["Website", links.website]]
    .filter(([, url]) => url)
    .map(([name, url]) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(name)} ↗</a>`);
  if (!buttons.length) return '<p class="caption">No GitHub or LinkedIn profile found yet — rerun with Perplexity enrichment.</p>';
  return `<div class="link-buttons">${buttons.join("")}</div>`;
}

function renderQuickView(container, entry) {
  if (!entry) { container.innerHTML = ""; return; }
  const { report, researcher, ctx, region } = entry;
  let body = "";
  if (researcher) {
    body += `<div class="kv-list">
      <div><b>Recommendation</b> ${recBadge(report.recommendation)}</div>
      <div><b>Conference / year</b> ${esc(confYearLabel(ctx))}</div>
      <div><b>Affiliation</b> ${esc(researcher.affiliation)}</div>
      <div><b>Region</b> ${esc(region || "Unknown")}</div>
      <div><b>Role</b> ${esc(researcher.role)}</div>
      <div><b>Signals</b> ${report.signals.length}</div>
    </div>${linkButtonsHtml(researcher.id)}`;
  } else {
    body += `<div class="kv-list"><div><b>Recommendation</b> ${recBadge(report.recommendation)}</div><div><b>Signals</b> ${report.signals.length}</div></div>`;
  }
  if (report.signals.length) {
    body += "<ul>" + report.signals.slice(0, 3).map((signal) => `
      <li><b>${esc(titleCase(signal.signal_type))}</b> — ${esc(signal.description.slice(0, 160))}${signal.description.length > 160 ? "…" : ""}
      <a href="${esc(signal.source_url)}" target="_blank" rel="noopener">Source ↗</a></li>`).join("") + "</ul>";
  } else if (researcher) {
    body += '<p class="caption">No commercialization signals detected for this researcher in this run.</p>';
  }
  body += '<p class="caption">Open <b>Explore &amp; details</b> for the full report and score breakdown.</p>';
  container.innerHTML = `<div class="card"><h3>Quick view — ${esc(report.researcher_or_cluster)} (${report.startup_likelihood_score}/100)</h3>${body}</div>`;
}

// ---------------------------------------------------------------------------
// Explore tab
// ---------------------------------------------------------------------------

function renderExploreTab() {
  const container = $("#tab-explore");
  const reports = filteredReports();
  if (!reports.length) {
    container.innerHTML = filterMissHtml() + '<p class="caption">Adjust sidebar filters to explore individual candidate reports.</p>';
    return;
  }

  const { researchersById } = bundleMaps();
  const contexts = state.bundle.contexts || {};
  const regions = state.bundle.regions || {};
  const thesisMap = state.bundle.thesis_fit || null;
  const isResearchers = state.filters.viewMode === "researchers";

  const avg = (reports.reduce((sum, report) => sum + report.startup_likelihood_score, 0) / reports.length).toFixed(1);
  const meetings = reports.filter((report) => report.recommendation === "take_meeting").length;

  let html = metricRow([
    ["Candidates", reports.length],
    ["Top score", reports[0].startup_likelihood_score],
    ["Avg score", avg],
    ["Take meeting", meetings],
  ]);
  html += '<div class="card"><h3>Ranked candidates</h3><p class="caption">Click a row to open the full report below.</p><div id="candidates-table"></div></div>';
  html += '<div id="candidate-detail"></div>';
  container.innerHTML = html;

  const rowIds = reports.map((report) => report.id);
  if (!rowIds.includes(state.selectedReportId)) state.selectedReportId = rowIds[0];

  const columns = isResearchers
    ? ["Name", "Score", "Priority", "Recommendation", "Signals", "Conference / year", "Affiliation", "Region", ...(thesisMap ? ["Thesis fit"] : [])]
    : ["Name", "Score", "Priority", "Recommendation", "Signals"];

  makeTable($("#candidates-table"), columns, reports.map((report) => {
    const row = {
      Name: `<b>${esc(report.researcher_or_cluster)}</b>`,
      Score: report.startup_likelihood_score,
      Priority: esc(titleCase(report.priority_band)),
      Recommendation: recBadge(report.recommendation),
      Signals: report.signals.length,
    };
    if (isResearchers) {
      const rid = researcherIdFromReportId(report.id);
      const researcher = rid ? researchersById[rid] : null;
      row["Conference / year"] = esc(confYearLabel(rid ? contexts[rid] : null));
      row.Affiliation = esc(researcher?.affiliation || "—");
      row.Region = esc((rid && regions[rid]) || "—");
      if (thesisMap) {
        const assessment = rid ? thesisMap[rid] : null;
        row["Thesis fit"] = assessment ? `<span class="badge ${FIT_COLORS[assessment.fit_level] || "gray"}">${esc(titleCase(assessment.fit_level))}</span>` : "—";
      }
    }
    return row;
  }), {
    rowIds,
    selectedId: state.selectedReportId,
    onSelect: (id) => { state.selectedReportId = id; renderExploreTab(); },
  });

  renderCandidateDetail($("#candidate-detail"), reports.find((report) => report.id === state.selectedReportId));
}

function renderCandidateDetail(container, report) {
  if (!report) { container.innerHTML = ""; return; }
  const { researchersById } = bundleMaps();
  const rid = researcherIdFromReportId(report.id);
  const researcher = rid ? researchersById[rid] : null;
  const ctx = rid ? (state.bundle.contexts || {})[rid] : null;
  const region = rid ? (state.bundle.regions || {})[rid] : null;
  const assessment = rid && state.bundle.thesis_fit ? state.bundle.thesis_fit[rid] : null;

  let html = '<div class="card">';
  html += `<h3 style="font-size:18px">${esc(report.researcher_or_cluster)}</h3>`;
  if (researcher) {
    const subtitle = [researcher.affiliation || "Unknown affiliation", researcher.role || "Unknown role"];
    if (region) subtitle.push(region);
    html += `<p>${esc(subtitle.join(" · "))}</p>`;
    const yearsLabel = ctx && ctx.years.length === 1 ? ctx.years[0]
      : ctx && ctx.years.length ? `${ctx.years[ctx.years.length - 1]}–${ctx.years[0]}` : "—";
    html += metricRow([
      ["Conference / year", confYearLabel(ctx).split(" · ")[0].slice(0, 40)],
      [ctx && ctx.years.length > 1 ? "Paper years" : "Paper year", yearsLabel],
      ["Papers in run", ctx ? ctx.paper_count : "—"],
    ]);
    if (ctx && ctx.topics.length) {
      html += `<p class="caption">Research topics: ${esc(ctx.topics.slice(0, 4).join(", "))}${ctx.topics.length > 4 ? "…" : ""}</p>`;
    }
    if (assessment) {
      html += `<p><b>Backtrace fit:</b> <span class="badge ${FIT_COLORS[assessment.fit_level] || "gray"}">${esc(titleCase(assessment.fit_level))}</span>
        · <b>EU:</b> ${esc(titleCase(assessment.europe_nexus))} · <b>Layer:</b> ${esc(titleCase(assessment.infra_layer))}</p>`;
      if (assessment.reasons && assessment.reasons.length) {
        html += `<p class="caption">${esc(assessment.reasons.slice(0, 3).join(" · "))}</p>`;
      }
    }
    html += linkButtonsHtml(researcher.id);
  } else {
    html += metricRow([["Score", report.startup_likelihood_score]]);
  }
  html += "</div>";

  // Score breakdown chart
  const breakdown = report.score_breakdown;
  html += '<div class="card"><h3>Score breakdown</h3>';
  html += hbarChart(SCORE_COMPONENTS.map(([label, field, max]) => ({
    label: `${label} (max ${max})`,
    value: breakdown[field],
  })), 20);
  const rawTotal = breakdown.startup_likelihood_score;
  const penalty = rawTotal - report.startup_likelihood_score;
  if (penalty > 0) {
    html += `<p class="caption">Component sum: <b>${rawTotal}</b>. Final score after identity penalty (−${penalty}): <b>${report.startup_likelihood_score}</b>.</p>`;
  }
  html += "</div>";

  // Full report markdown
  html += `<div class="card"><h3>Report</h3><div class="markdown">${mdToHtml(report.markdown || "")}</div></div>`;

  // Methodology + signals expanders
  html += expander("How startup likelihood is calculated", methodologyHtml());
  html += expander(`Score component detail — ${report.researcher_or_cluster} (${report.startup_likelihood_score}/100)`,
    SCORE_COMPONENTS.map(([label, field, max, description]) => {
      const value = breakdown[field];
      const pct = max ? Math.round((100 * value) / max) : 0;
      return `<p><b>${esc(label)}: ${value}/${max}</b> (${pct}% of max)<br/><span class="muted">${esc(description)}</span></p>`;
    }).join(""));
  html += expander(`Sources & detected signals (${report.signals.length})`, signalsHtml(report.signals));

  if (state.filters.showDevTools && state.bundle.thesis_fit) {
    const sample = Object.fromEntries(Object.entries(state.bundle.thesis_fit).slice(0, 5));
    html += expander("Thesis fit (raw)", `<pre class="json">${esc(JSON.stringify(sample, null, 2))}</pre>`);
  }
  if (state.filters.showDevTools && rid && activeRun() && state.bundle.agentic_enabled) {
    html += `<div id="trace-expander" data-researcher="${esc(rid)}"></div>`;
  }

  container.innerHTML = html;

  const traceHolder = $("#trace-expander");
  if (traceHolder) renderResearcherTrace(traceHolder, rid);
}

function signalSourceLabel(signalId) {
  if (signalId.startsWith("agent_")) return "agent";
  if (signalId.startsWith("perplexity_")) return "perplexity";
  if (signalId.startsWith("github_")) return "github";
  if (signalId.startsWith("mock_")) return "mock";
  return "other";
}

function signalsHtml(signals) {
  if (!signals.length) return '<div class="info-box">No public founder or commercialization signals attached to this candidate.</div>';
  let html = '<p class="caption">Signal sources: <b>agent</b> (LangGraph investigation), <b>perplexity</b> (Sonar), <b>github</b>, or <b>mock</b>.</p>';
  html += signals.map((signal, index) => {
    const points = (SIGNAL_TYPE_POINTS[signal.signal_type] || {})[signal.evidence_strength] || 0;
    const host = signal.source_url.includes("://") ? signal.source_url.split("/")[2] : signal.source_url;
    const body = `<div class="kv-list">
        <div><b>Source</b> ${esc(signalSourceLabel(signal.id))}</div>
        <div><b>Type</b> ${esc(titleCase(signal.signal_type))}</div>
        <div><b>Evidence strength</b> ${esc(signal.evidence_strength)}</div>
        <div><b>Scoring weight</b> ${points} pts</div>
        ${signal.date_found ? `<div><b>Date found</b> ${esc(signal.date_found)}</div>` : ""}
      </div>
      <p>${esc(signal.description)}</p>
      <p><a href="${esc(signal.source_url)}" target="_blank" rel="noopener">${esc(signal.source_url)}</a></p>`;
    return expander(`${index + 1}. [${signalSourceLabel(signal.id)}] ${titleCase(signal.signal_type)} — ${host}`, body);
  }).join("");
  return html;
}

function methodologyHtml() {
  const fund = state.boot.fund;
  let html = `<p>Each researcher (or cluster) gets a <b>rule-based score from 0–100</b>. Six components are summed, then a small <b>identity penalty</b> may apply when profile matching is uncertain (−3 medium, −6 low confidence).</p><h4>Components (max 100 before penalty)</h4>`;
  html += SCORE_COMPONENTS.map(([label, , max, description]) =>
    `<p><b>${esc(label)}</b> (0–${max})<br/><span class="muted">${esc(description)}</span></p>`).join("");
  html += '<h4>Signal type × evidence strength</h4><div class="table-wrap"><table><thead><tr><th>Signal type</th><th>Evidence</th><th>Points</th></tr></thead><tbody>';
  for (const [type, strengths] of Object.entries(SIGNAL_TYPE_POINTS)) {
    for (const [strength, points] of Object.entries(strengths)) {
      html += `<tr><td>${esc(type.replaceAll("_", " "))}</td><td>${esc(strength)}</td><td>${points}</td></tr>`;
    }
  }
  html += "</tbody></table></div>";
  html += `<h4>Priority bands &amp; recommendations</h4>
    <div class="table-wrap"><table><thead><tr><th>Score</th><th>Band</th><th>VC action</th></tr></thead><tbody>
    <tr><td>80+</td><td>High priority</td><td>Take meeting</td></tr>
    <tr><td>60–79</td><td>Monitor closely</td><td>Monitor monthly</td></tr>
    <tr><td>40–59</td><td>Weak signal</td><td>Add to watchlist</td></tr>
    <tr><td>&lt;40</td><td>Low priority</td><td>Ignore for now</td></tr>
    </tbody></table></div>`;
  if (fund && Object.keys(fund.topic_scores || {}).length) {
    html += `<h4>Fund overrides (${esc(fund.name)})</h4><div class="table-wrap"><table><thead><tr><th>Topic</th><th>Score</th></tr></thead><tbody>`;
    html += Object.entries(fund.topic_scores).sort(([a], [b]) => a.localeCompare(b))
      .map(([topic, score]) => `<tr><td>${esc(topic)}</td><td>${score}</td></tr>`).join("");
    html += "</tbody></table></div>";
    html += `<p class="caption">${esc(fund.description.slice(0, 400))}</p>`;
  }
  html += '<p class="caption"><b>Clusters:</b> member researcher scores are averaged per component; team continuity and signal strength get a +2 boost (capped at their maxima).</p>';
  return html;
}

// ---------------------------------------------------------------------------
// Dev tools: agent traces + enrichment audit
// ---------------------------------------------------------------------------

async function loadTraces(runId) {
  if (!state.tracesCache[runId]) {
    state.tracesCache[runId] = await api(`/api/runs/${encodeURIComponent(runId)}/traces`);
  }
  return state.tracesCache[runId];
}

async function renderResearcherTrace(container, researcherId) {
  const run = activeRun();
  if (!run) return;
  try {
    const { traces } = await loadTraces(run.id);
    const row = traces.find((trace) => trace.researcher_id === researcherId);
    if (!row) {
      container.innerHTML = '<p class="caption">No agent investigation trace for this candidate in the selected run.</p>';
      return;
    }
    const title = `Investigation trace — ${row.researcher_name || researcherId} (${row.tier || "standard"}, ${row.steps_used ?? "—"}/${row.max_steps ?? "—"} steps, ${row.status || "unknown"})`;
    container.innerHTML = expander(title, '<div class="trace-body"><p class="caption">Loading trace…</p></div>');
    const details = container.querySelector("details");
    let loaded = false;
    details.addEventListener("toggle", async () => {
      if (!details.open || loaded) return;
      loaded = true;
      const body = container.querySelector(".trace-body");
      try {
        const trace = await api(`/api/traces/${encodeURIComponent(row.id)}`);
        let html = "";
        if (row.summary) html += `<p><b>Summary:</b> ${esc(row.summary)}</p>`;
        if (row.error_message) html += `<div class="error-box">${esc(row.error_message)}</div>`;
        if (trace.timeline && trace.timeline.length) {
          html += "<h4>Step timeline</h4><ul>" + trace.timeline.map((step) =>
            `<li><b>Step ${esc(step.step)}:</b> <code>${esc(step.action)}</code> — ${esc(step.detail)}</li>`).join("") + "</ul>";
        } else {
          html += `<p class="caption">${row.status === "failed" ? "No step timeline available (investigation failed before output)." : "No step timeline in stored response."}</p>`;
        }
        html += metricRow([
          ["Tool calls", row.tool_calls_count || 0],
          ["Tokens", (row.input_tokens || 0) + (row.output_tokens || 0)],
          ["Est. cost", row.estimated_cost_usd != null ? `$${row.estimated_cost_usd.toFixed(3)}` : "—"],
        ]);
        if (trace.response_json) {
          html += `<button class="linkish" id="download-trace">Download full trace JSON</button>`;
        }
        body.innerHTML = html;
        const downloadBtn = body.querySelector("#download-trace");
        if (downloadBtn) {
          downloadBtn.addEventListener("click", () => {
            const blob = new Blob([typeof trace.response_json === "string" ? trace.response_json : JSON.stringify(trace.response_json)], { type: "application/json" });
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = `${row.id || "trace"}.json`;
            link.click();
            URL.revokeObjectURL(link.href);
          });
        }
      } catch (error) {
        body.innerHTML = `<div class="error-box">Could not load trace: ${esc(error.message)}</div>`;
      }
    });
  } catch (error) {
    container.innerHTML = `<div class="error-box">Could not load traces: ${esc(error.message)}</div>`;
  }
}

async function renderDevTab() {
  const container = $("#tab-dev");
  const run = activeRun();
  if (!run) {
    container.innerHTML = '<div class="info-box">Diagnostics are available for stored runs only.</div>';
    return;
  }
  container.innerHTML = '<div class="card"><h3>Enrichment audit</h3><div id="audit-panel"><p class="caption">Loading…</p></div></div><div class="card"><h3>Investigation traces</h3><div id="traces-panel"><p class="caption">Loading…</p></div></div>';

  // Enrichment audit
  try {
    if (!state.auditCache[run.id]) {
      state.auditCache[run.id] = await api(`/api/runs/${encodeURIComponent(run.id)}/enrichment-audit`);
    }
    renderAuditPanel($("#audit-panel"), state.auditCache[run.id]);
  } catch (error) {
    $("#audit-panel").innerHTML = `<div class="error-box">${esc(error.message)}</div>`;
  }

  // Traces
  try {
    const payload = await loadTraces(run.id);
    renderTracesPanel($("#traces-panel"), payload);
  } catch (error) {
    $("#traces-panel").innerHTML = `<div class="error-box">${esc(error.message)}</div>`;
  }
}

function renderAuditPanel(container, payload) {
  if (!payload.available) {
    container.innerHTML = '<div class="info-box">No enrichment audit saved for this run. Re-run the pipeline to capture before/after researcher snapshots.</div>';
    return;
  }
  const audit = payload.audit;
  const summary = payload.summary || {};
  const enrichedLines = summary.enriched_profile_lines || [];
  const investigated = summary.investigated_profile_names || [];

  let html = "";
  if (summary.enrichment_worked && enrichedLines.length) {
    html += `<div class="success-box">Enrichment updated ${enrichedLines.length} profile(s): ${esc(enrichedLines.slice(0, 5).join("; "))}${enrichedLines.length > 5 ? " …" : ""}</div>`;
  } else if (summary.enrichment_worked) {
    html += '<div class="success-box">Enrichment produced results for this run.</div>';
  } else {
    html += '<div class="warning-box">Enrichment did not resolve affiliations or add signals for this run. Most researchers were likely skipped by caps or low identity confidence.</div>';
  }
  if (investigated.length) {
    html += `<p class="caption">Investigated by ${esc(audit.mode)}: ${esc(investigated.slice(0, 15).join(", "))}${investigated.length > 15 ? " …" : ""}</p>`;
  }
  html += metricRow([
    ["Mode", audit.mode],
    ["Targeted", audit.targeted_count],
    ["Affiliations resolved", audit.affiliation_resolved_count],
    ["Still unknown", audit.still_unknown_count],
  ]);

  const statusCounts = summary.status_counts || {};
  if (Object.keys(statusCounts).length) {
    html += "<h4>Status counts</h4>" + hbarChart(Object.entries(statusCounts).map(([status, count]) => ({ label: status, value: count })));
  }
  const skipCounts = summary.skip_reason_counts || {};
  if (Object.keys(skipCounts).length) {
    html += "<h4>Skip reasons</h4>" + hbarChart(Object.entries(skipCounts).map(([reason, count]) => ({ label: reason, value: count })));
  }
  if (audit.config_summary) {
    html += expander("Enrichment config", `<pre class="json">${esc(JSON.stringify(audit.config_summary, null, 2))}</pre>`);
  }
  html += '<h4>All researcher enrichment records</h4><div id="audit-records"></div>';
  container.innerHTML = html;

  makeTable($("#audit-records"),
    ["Name", "Status", "Skip reason", "Pre affiliation", "Post affiliation", "Pre role", "Post role", "Signals", "Tier"],
    (audit.records || []).slice(0, 100).map((record) => ({
      Name: esc(record.name),
      Status: esc(record.status),
      "Skip reason": esc(record.skip_reason || ""),
      "Pre affiliation": esc(record.pre_affiliation),
      "Post affiliation": esc(record.post_affiliation),
      "Pre role": esc(record.pre_role),
      "Post role": esc(record.post_role),
      Signals: record.signal_count,
      Tier: esc(record.investigation_tier || ""),
    })));
}

function renderTracesPanel(container, payload) {
  if (!state.bundle.agentic_enabled) {
    container.innerHTML = '<p class="caption">No agent traces (Sonar mode was used for this run).</p>';
    return;
  }
  const traces = payload.traces || [];
  if (!traces.length) {
    container.innerHTML = '<div class="info-box">Agentic mode was enabled but no investigation traces were stored for this run.</div>';
    return;
  }
  const summary = payload.summary || {};
  const tokens = (summary.total_input_tokens || 0) + (summary.total_output_tokens || 0);
  const cost = summary.estimated_cost_usd != null ? `~$${summary.estimated_cost_usd.toFixed(2)}` : "—";
  container.innerHTML = `<p class="caption">${summary.trace_count || traces.length} investigations · ${tokens.toLocaleString()} tokens · ${cost} est.</p><div id="traces-table"></div>`;
  makeTable($("#traces-table"), ["Researcher", "Tier", "Steps", "Tools", "Status", "Signals", "Cost (est.)"],
    traces.map((trace) => ({
      Researcher: esc(trace.researcher_name || trace.researcher_id),
      Tier: esc(trace.tier || "—"),
      Steps: `${trace.steps_used ?? "—"}/${trace.max_steps ?? "—"}`,
      Tools: trace.tool_calls_count || 0,
      Status: esc(trace.status || "—"),
      Signals: trace.signals_emitted || 0,
      "Cost (est.)": trace.estimated_cost_usd != null ? `$${trace.estimated_cost_usd.toFixed(3)}` : "—",
    })));
}

// ---------------------------------------------------------------------------
// Pipeline jobs
// ---------------------------------------------------------------------------

async function startPipeline() {
  const targets = newRunTargets();
  if (!targets.length) return;
  const btn = $("#run-pipeline-btn");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const job = await api("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify({
        conferences: targets,
        year: Number($("#run-year").value) || 2024,
        paper_source: $("#paper-source").value || null,
      }),
    });
    state.jobId = job.id;
    renderJobProgress(job);
    state.jobTimer = setInterval(pollJob, 2000);
  } catch (error) {
    btn.disabled = false;
    btn.textContent = "Run pipeline & save";
    $("#job-progress").classList.remove("hidden");
    $("#job-progress").innerHTML = `<div class="error-box">${esc(error.message)}</div>`;
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/pipeline/jobs/${state.jobId}`);
    renderJobProgress(job);
    if (job.status !== "running") {
      clearInterval(state.jobTimer);
      state.jobTimer = null;
      state.jobId = null;
      const btn = $("#run-pipeline-btn");
      btn.disabled = false;
      btn.textContent = "Run pipeline & save";
      await onJobFinished(job);
    }
  } catch (error) {
    clearInterval(state.jobTimer);
    state.jobTimer = null;
    $("#job-progress").innerHTML += `<div class="error-box">${esc(error.message)}</div>`;
    const btn = $("#run-pipeline-btn");
    btn.disabled = false;
    btn.textContent = "Run pipeline & save";
  }
}

function renderJobProgress(job) {
  const holder = $("#job-progress");
  holder.classList.remove("hidden");
  const done = job.items.filter((item) => ["complete", "failed"].includes(item.status)).length;
  const pct = Math.round((100 * done) / job.items.length);
  let html = `<div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>`;
  html += job.items.map((item) => {
    let note = "";
    if (item.status === "complete") note = `${item.paper_count ?? "?"} papers`;
    if (item.status === "failed") note = (item.error || "failed").slice(0, 60);
    return `<div class="job-item"><span class="job-dot ${esc(item.status)}"></span><span><b>${esc(item.conference)}</b> ${esc(item.status)}${note ? ` — ${esc(note)}` : ""}</span></div>`;
  }).join("");
  holder.innerHTML = html;
}

async function onJobFinished(job) {
  const completed = job.items.filter((item) => item.status === "complete");
  const withData = completed.filter((item) => (item.paper_count || 0) > 0);
  const failures = job.items.filter((item) => item.status === "failed");
  const holder = $("#job-progress");

  if (!completed.length && failures.length) {
    holder.innerHTML += `<div class="error-box">All pipeline runs failed: ${esc(failures[0].error || "unknown error")}<br/><span class="small">OpenReview fetch failures are often 429 rate limits — wait a few minutes and retry, or increase LAB2STARTUP_OPENREVIEW_REQUEST_DELAY.</span></div>`;
    return;
  }

  state.runs = await api("/api/runs");
  const preferredId = withData.length ? withData[0].run_id : completed[completed.length - 1].run_id;
  state.selectedRunId = preferredId;
  if (withData.length) state.filters.onlyWithResults = true;
  $("#only-with-results").checked = state.filters.onlyWithResults;

  let summary = "";
  if (failures.length) {
    summary += `<div class="warning-box">${failures.length} run(s) failed: ${esc(failures.map((item) => item.conference).join(", "))}</div>`;
  }
  if (completed.length > 1) {
    summary += `<div class="success-box">Saved ${completed.length} run(s)${withData.length ? `, ${withData.length} with papers` : ""}.</div>`;
  } else if (completed.length) {
    summary += withData.length
      ? '<div class="success-box">Run saved with paper data.</div>'
      : '<div class="warning-box">Run saved but returned 0 papers.</div>';
  }
  holder.innerHTML += summary;

  renderRunSelect();
  await loadBundle();
}

// ---------------------------------------------------------------------------
// Bundle loading + boot
// ---------------------------------------------------------------------------

async function loadBundle(force = false) {
  setLoading(true, "Loading pipeline data…");
  try {
    const params = new URLSearchParams();
    if (state.selectedRunId) params.set("run_id", state.selectedRunId);
    if (force) params.set("force_refresh", "true");
    state.bundle = await api(`/api/bundle?${params.toString()}`);
    state.selectedReportId = null;
    renderFilterOptions();
    renderMain();
  } catch (error) {
    $("#tab-top").innerHTML = `<div class="error-box">Failed to load data: ${esc(error.message)}</div>`;
  } finally {
    setLoading(false);
  }
}

function bindEvents() {
  $("#run-select").addEventListener("change", (event) => {
    state.selectedRunId = event.target.value || null;
    loadBundle();
  });
  $("#only-with-results").addEventListener("change", (event) => {
    state.filters.onlyWithResults = event.target.checked;
    const before = state.selectedRunId;
    renderRunSelect();
    if (state.selectedRunId !== before) loadBundle();
  });
  $("#run-scope").addEventListener("change", renderNewRunForm);
  $("#run-pipeline-btn").addEventListener("click", startPipeline);
  $("#refresh-btn").addEventListener("click", async () => {
    await api("/api/refresh", { method: "POST" });
    loadBundle(true);
  });

  document.querySelectorAll("#view-mode button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#view-mode button").forEach((b) => b.classList.toggle("active", b === btn));
      state.filters.viewMode = btn.dataset.value;
      state.selectedReportId = null;
      renderMain();
    });
  });

  $("#min-score").addEventListener("input", (event) => {
    state.filters.minScore = Number(event.target.value);
    $("#min-score-value").textContent = event.target.value;
    renderMain();
  });
  const simpleFilters = [
    ["#filter-recommendation", "recommendation"],
    ["#filter-conference", "conference"],
    ["#filter-year", "year"],
    ["#filter-topic", "topic"],
    ["#filter-thesis-fit", "thesisFit"],
    ["#filter-europe", "europe"],
  ];
  for (const [selector, key] of simpleFilters) {
    $(selector).addEventListener("change", (event) => {
      state.filters[key] = event.target.value;
      renderMain();
    });
  }
  $("#show-dev-tools").addEventListener("change", (event) => {
    state.filters.showDevTools = event.target.checked;
    renderMain();
  });

  document.querySelectorAll("#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      renderMain();
    });
  });
}

async function init() {
  setLoading(true, "Starting Lab2Startup…");
  try {
    state.boot = await api("/api/bootstrap");
    state.runs = state.boot.runs || [];
    const runs = displayRuns();
    state.selectedRunId = pickPreferredRunId(runs);
    const hash = location.hash.replace("#", "");
    if (["top", "explore", "dev"].includes(hash)) {
      state.tab = hash;
      if (hash === "dev") {
        state.filters.showDevTools = true;
        $("#show-dev-tools").checked = true;
      }
    }
    renderSidebar();
    bindEvents();
    await loadBundle();
  } catch (error) {
    setLoading(false);
    $("#tab-top").innerHTML = `<div class="error-box">Could not reach the Lab2Startup backend: ${esc(error.message)}</div>`;
  }
}

init();
