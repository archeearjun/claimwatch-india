const FEEDS = {
  discovery: "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/discovery/latest.json",
  promises: "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/promises/candidates.json",
  evidence: "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/evidence/latest.json"
};

const state = {
  discovery: null,
  promises: null,
  evidence: null,
  discoveryFilter: "all",
  promiseYear: "all",
  promiseSearch: "",
  measurableOnly: false,
  promiseOutcome: "all",
  promiseLimit: 18
};

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  }[char]));
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-IN", withTime ? {
    dateStyle: "medium",
    timeStyle: "short"
  } : {
    dateStyle: "medium"
  }).format(date);
}

function timeAgo(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function humanize(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, char => char.toUpperCase());
}

function compactNumber(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en-IN", { notation: n >= 1000 ? "compact" : "standard" }).format(n);
}

function renderOutcomeBoard() {
  const summary = state.evidence?.summary || {};
  const outcomes = summary.promise_outcomes || {};

  const fulfilledByDeadline = Number(outcomes.fulfilled_by_deadline || 0);
  const targetReached = Number(outcomes.target_reached_evidence || 0);
  const unfulfilled = Number(outcomes.proven_unfulfilled_by_deadline || 0);
  const progress = Number(outcomes.progress_documented || 0);
  const overdue = Number(outcomes.deadline_passed_unresolved || 0);
  const deadlineFuture = Number(outcomes.deadline_not_reached || 0);
  const insufficient = Number(outcomes.insufficient_evidence || 0);
  const qualitative = Number(outcomes.not_machine_measurable || 0);
  const total = Number(outcomes.audited_total || 0);
  const other = deadlineFuture + insufficient + qualitative;

  const set = (id, value) => {
    const node = document.querySelector(id);
    if (node) node.textContent = compactNumber(value);
  };

  set("#outcome-fulfilled-deadline", fulfilledByDeadline);
  set("#outcome-target-reached", targetReached);
  set("#outcome-unfulfilled", unfulfilled);
  set("#outcome-progress", progress);
  set("#outcome-overdue", overdue);
  set("#outcome-other", other);

  const coverage = Number(summary.promise_coverage_pct || 0);
  const health = document.querySelector("#outcome-health");
  if (health) {
    health.innerHTML = `<span>Audit coverage</span><strong>${compactNumber(total)} checked · ${coverage.toFixed(1)}% of extracted candidates</strong>`;
  }

  document.querySelectorAll(".outcome-filter").forEach(button => {
    button.classList.toggle("active", button.dataset.outcomeFilter === state.promiseOutcome);
  });

  const bar = document.querySelector("#outcome-bar");
  if (!bar) return;

  if (!total) {
    bar.innerHTML = '<div class="outcome-empty">Waiting for audited promise outcomes…</div>';
    return;
  }

  const segments = [
    ["fulfilled", fulfilledByDeadline, "Fulfilled by deadline"],
    ["target", targetReached, "Target reached; deadline timing not proven"],
    ["unfulfilled", unfulfilled, "Proven not fulfilled by deadline"],
    ["progress", progress, "Progress documented"],
    ["overdue", overdue, "Deadline passed · unresolved"],
    ["other", other, "Other / insufficient"]
  ].filter(([, value]) => value > 0);

  bar.innerHTML = segments.map(([kind, value, label]) => {
    const pct = Math.max(1.5, (value / total) * 100);
    return `<button class="outcome-segment ${kind}" data-outcome-segment="${kind}" style="width:${pct}%" title="${escapeHtml(label)}: ${value}"><span>${value}</span></button>`;
  }).join("");
}

function promiseOutcomeLabel(status) {
  return ({
    fulfilled_by_deadline: "FULFILLED BY DEADLINE",
    target_reached_evidence: "TARGET REACHED · TIMING UNRESOLVED",
    proven_unfulfilled_by_deadline: "PROVEN NOT FULFILLED BY DEADLINE",
    progress_documented: "PROGRESS DOCUMENTED",
    deadline_passed_unresolved: "DEADLINE PASSED · UNRESOLVED",
    deadline_not_reached: "DEADLINE NOT REACHED",
    insufficient_evidence: "INSUFFICIENT EVIDENCE",
    not_machine_measurable: "NOT MACHINE-MEASURABLE"
  })[status] || humanize(status || "unaudited").toUpperCase();
}

function promiseOutcomeClass(status) {
  if (status === "proven_unfulfilled_by_deadline") return "danger";
  if (status === "fulfilled_by_deadline") return "strong";
  if (status === "target_reached_evidence") return "target";
  if (status === "progress_documented") return "action";
  if (status === "deadline_passed_unresolved") return "fact";
  return "pending";
}

async function loadJson(url) {
  const response = await fetch(`${url}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function instantCheck(claim) {
  const response = await fetch("/api/check", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ claim })
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Evidence API HTTP ${response.status}`);
  }
  return payload;
}

function instantResultHtml(result) {
  const signal = result?.signal || {};
  const evidence = (result?.evidence || []).slice(0, 5);
  const promises = (result?.promise_matches || []).slice(0, 4);
  const publishable = Boolean(signal.publishable_verdict);
  const verdict = result?.verdict || signal.verdict || "pending";

  return `
    <div class="instant-signal ${escapeHtml(signalClass(signal.level || "pending"))}">
      <strong>${publishable ? `Automated verdict · ${escapeHtml(verdictLabel(verdict))}` : escapeHtml(signal.label || "Verification pending")}</strong>
      <p>${escapeHtml(signal.reason || "ClaimWatch has not established a final verdict.")}</p>
      ${result?.cached ? `<small class="cache-note">Matched a previously checked claim · ${Math.round((result.cache_similarity || 0) * 100)}% similarity</small>` : ""}
    </div>
    ${signal?.proof ? `
      <div class="proof-box claim-proof">
        <span>STRUCTURED CLAIM PROOF</span>
        <div><b>Claimed</b><strong>${escapeHtml(signal.proof.claimed?.raw || signal.proof.claimed?.value)}</strong></div>
        <div><b>Official value</b><strong>${escapeHtml(signal.proof.observed?.raw || signal.proof.observed?.value)}</strong></div>
        <div><b>Rule</b><strong>${escapeHtml(humanize(signal.proof.operator || "eq"))}</strong></div>
        <a href="${escapeHtml(safeUrl(signal.proof.evidence_url))}" target="_blank" rel="noopener noreferrer">Open proof source ↗</a>
      </div>
    ` : ""}
    ${evidence.length ? `
      <div class="instant-evidence">
        <span class="instant-heading">EVIDENCE LEADS</span>
        ${evidence.map(item => `
          <a href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">
            <span class="tier ${escapeHtml(item.tier || "reporting")}">${escapeHtml(evidenceTierLabel(item.tier || "reporting"))}</span>
            <span>${escapeHtml(item.title || item.source || "Evidence source")}</span>
            <small>${Math.round((item.relevance || 0) * 100)}%</small>
          </a>
        `).join("")}
      </div>
    ` : '<div class="quiet-note">No strong evidence lead was found in the instant search.</div>'}
    ${promises.length ? `
      <div class="instant-promises">
        <span class="instant-heading">HISTORICAL PROMISE MATCHES</span>
        ${promises.map(item => `
          <a href="#promises">
            <b>${item.year}</b>
            <span>${escapeHtml(item.anchor || humanize(item.category))}</span>
            <small>${Math.round((item.similarity || 0) * 100)}%</small>
          </a>
        `).join("")}
      </div>
    ` : ""}
    <p class="instant-disclaimer">Instant retrieval is a triage step, not a final truth verdict. Comparable dates, units, populations and definitions must still line up.</p>
  `;
}

function kindLabel(kind) {
  return ({
    official_speech_or_video: "OFFICIAL SPEECH",
    youtube_video: "YOUTUBE",
    news: "NEWS / WEB"
  })[kind] || humanize(kind || "source").toUpperCase();
}

function kindClass(kind) {
  return ({
    official_speech_or_video: "official",
    youtube_video: "youtube",
    news: "news"
  })[kind] || "news";
}

function signalClass(level) {
  const value = String(level || "");
  if (value.includes("contradicted") || value.includes("conflict")) return "danger";
  if (value.includes("supported") || value.includes("target_evidence")) return "strong";
  if (value.includes("action_detected")) return "action";
  if (value.includes("structured")) return "strong";
  if (value.includes("primary")) return "primary";
  if (value.includes("fact")) return "fact";
  if (value.includes("reporting")) return "secondary";
  return "pending";
}

function verdictLabel(value) {
  return ({
    supported: "SUPPORTED",
    contradicted: "CONTRADICTED",
    insufficient: "INSUFFICIENT EVIDENCE",
    context: "MISSING CONTEXT",
    pending: "PENDING"
  })[value] || humanize(value || "pending").toUpperCase();
}

function candidateText(raw) {
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") return raw.text || "";
  return "";
}

function signalLabel(signal) {
  if (!signal) return "Pending research";
  if (typeof signal === "string") return humanize(signal);
  return signal.label || humanize(signal.level);
}

function evidenceTierLabel(tier) {
  return ({
    primary: "PRIMARY",
    secondary: "REPORTING",
    fact_check: "FACT-CHECK"
  })[tier] || humanize(tier).toUpperCase();
}

function renderMetrics() {
  const discovery = state.discovery || {};
  const promises = state.promises || {};
  const evidence = state.evidence || {};

  const items = discovery.items || [];
  const sourceCounts = items.reduce((acc, item) => {
    acc[item.kind] = (acc[item.kind] || 0) + 1;
    return acc;
  }, {});

  $("#metric-sources").textContent = compactNumber(items.length);
  $("#metric-sources-detail").textContent =
    `${sourceCounts.official_speech_or_video || 0} speeches · ${sourceCounts.youtube_video || 0} videos · ${sourceCounts.news || 0} news`;

  $("#metric-promises").textContent = compactNumber(promises.summary?.total || 0);
  const years = promises.summary?.by_year || {};
  $("#metric-promises-detail").textContent =
    `2014: ${years["2014"] || 0} · 2019: ${years["2019"] || 0} · 2024: ${years["2024"] || 0}`;

  const promisePackets = Number(evidence.summary?.promise_packets || 0);
  const totalPromises = Number(evidence.summary?.promise_total_candidates || promises.summary?.total || 0);
  const outcomes = evidence.summary?.promise_outcomes || {};
  const verdicts = evidence.summary?.claim_verdicts || {};

  $("#metric-audited-promises").textContent = compactNumber(promisePackets);
  $("#metric-audited-promises-detail").textContent =
    totalPromises ? `${Number(evidence.summary?.promise_coverage_pct || 0).toFixed(1)}% of ${compactNumber(totalPromises)}` : "Outcome evidence checked";

  $("#metric-unfulfilled").textContent =
    compactNumber(outcomes.proven_unfulfilled_by_deadline || 0);

  $("#metric-verdicts").textContent =
    compactNumber(evidence.summary?.publishable_auto_verdicts || 0);
  $("#metric-verdicts-detail").textContent =
    `Supported ${verdicts.supported || 0} · Contradicted ${verdicts.contradicted || 0}`;

  renderOutcomeBoard();

  const ready =
    Boolean(state.discovery) &&
    Boolean(state.promises) &&
    Boolean(state.evidence);

  $("#pipeline-state").textContent = ready
    ? "Discovery, memory and evidence feeds connected"
    : "Loading intelligence feeds…";
}

function renderDiscovery() {
  const feed = state.discovery;
  const root = $("#discovery-grid");
  const notices = $("#discovery-notices");

  if (!feed) {
    root.innerHTML = '<div class="loading-card">Discovery feed unavailable.</div>';
    return;
  }

  $("#discovery-updated").textContent = feed.generated_at
    ? `${timeAgo(feed.generated_at)} · ${formatDate(feed.generated_at, true)}`
    : "Waiting for scan";

  const errors = feed.errors || [];
  notices.innerHTML = errors.length
    ? errors.slice(0, 4).map(error => `
        <div class="notice">
          <strong>${escapeHtml(error.source || error.stage || "Scanner")}</strong>
          <span>${escapeHtml(error.error || "Scanner notice")}</span>
        </div>
      `).join("")
    : "";

  let items = feed.items || [];
  if (state.discoveryFilter !== "all") {
    items = items.filter(item => item.kind === state.discoveryFilter);
  }

  if (!items.length) {
    root.innerHTML = `
      <div class="empty-panel">
        <strong>No matching discoveries yet.</strong>
        <p>The scanner completed, but this filter has no current items.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = items.slice(0, 24).map(item => {
    const claims = item.candidate_claims || [];
    const promises = item.candidate_promises || [];
    const claim = candidateText(claims[0]);
    const sourceName = item.channel_title || item.source || "Source";
    return `
      <article class="source-card">
        <div class="source-card-top">
          <span class="source-kind ${kindClass(item.kind)}">${escapeHtml(kindLabel(item.kind))}</span>
          <span class="source-age">${escapeHtml(timeAgo(item.published_at))}</span>
        </div>
        <h3>${escapeHtml(item.title || "Untitled source")}</h3>
        <p class="source-byline">${escapeHtml(sourceName)} · ${escapeHtml(formatDate(item.published_at))}</p>
        ${claim ? `
          <div class="claim-preview">
            <span>CHECKABLE CANDIDATE</span>
            <p>${escapeHtml(claim)}</p>
          </div>
        ` : `
          <div class="quiet-note">No high-confidence factual sentence extracted from the available feed text.</div>
        `}
        <div class="source-card-footer">
          <span>${claims.length} factual candidate${claims.length === 1 ? "" : "s"}${promises.length ? ` · ${promises.length} promise candidate${promises.length === 1 ? "" : "s"}` : ""}</span>
          <a href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">Open source ↗</a>
        </div>
      </article>
    `;
  }).join("");
}

function renderEvidence() {
  const feed = state.evidence;
  const root = $("#evidence-list");
  const summaryRoot = $("#evidence-summary");

  if (!feed) {
    summaryRoot.innerHTML = "";
    root.innerHTML = '<div class="loading-card">Evidence feed unavailable.</div>';
    return;
  }

  const summary = feed.summary || {};
  summaryRoot.innerHTML = `
    <div><span>Claim packets</span><strong>${summary.claim_packets || 0}</strong></div>
    <div><span>Promise packets</span><strong>${summary.promise_packets || 0}</strong></div>
    <div><span>Primary candidates</span><strong>${summary.primary_candidates || 0}</strong></div>
    <div><span>Prior fact-checks</span><strong>${summary.prior_fact_checks || 0}</strong></div>
    <div><span>Auto-published verdicts</span><strong>${summary.publishable_auto_verdicts || 0}</strong></div>
  `;

  const packets = feed.claim_packets || [];
  if (!packets.length) {
    root.innerHTML = `
      <div class="empty-panel">
        <strong>No claim packets have been generated yet.</strong>
        <p>Discovery can be live while the evidence job is still waiting for its first successful run.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = packets.slice(0, 12).map(packet => {
    const source = packet.claim_source || {};
    const evidence = (packet.evidence || []).slice(0, 4);
    const matches = packet.promise_matches || [];
    const factChecks = (packet.fact_checks || []).slice(0, 3);
    const signal = packet.signal || {};
    const level = signal.level || "pending";
    const verdict = packet.verdict || signal.verdict || "pending";
    const publishable = Boolean(signal.publishable_verdict);

    return `
      <article class="evidence-card">
        <div class="evidence-card-head">
          <div>
            <span class="source-kind ${kindClass(source.kind)}">${escapeHtml(kindLabel(source.kind))}</span>
            <span class="source-byline">${escapeHtml(source.channel_title || source.source || "Source")} · ${escapeHtml(formatDate(source.published_at))}</span>
          </div>
          <span class="signal ${signalClass(level)}">${escapeHtml(signalLabel(signal))}</span>
        </div>

        <blockquote>${escapeHtml(packet.claim)}</blockquote>
        ${signal?.proof ? `
          <div class="proof-box claim-proof">
            <span>STRUCTURED CLAIM PROOF</span>
            <div><b>Claimed</b><strong>${escapeHtml(signal.proof.claimed?.raw || signal.proof.claimed?.value)}</strong></div>
            <div><b>Official value</b><strong>${escapeHtml(signal.proof.observed?.raw || signal.proof.observed?.value)}</strong></div>
            <div><b>Rule</b><strong>${escapeHtml(humanize(signal.proof.operator || "eq"))}</strong></div>
            <a href="${escapeHtml(safeUrl(signal.proof.evidence_url))}" target="_blank" rel="noopener noreferrer">Open proof source ↗</a>
          </div>
        ` : ""}

        ${matches.length ? `
          <div class="history-strip">
            <span>HISTORICAL MATCH</span>
            <div>
              ${matches.map(match => `
                <a href="#promises" data-jump-promise="${escapeHtml(match.id)}">
                  ${match.year} · ${escapeHtml(humanize(match.category))}
                  <small>${Math.round((match.similarity || 0) * 100)}% lexical overlap</small>
                </a>
              `).join("")}
            </div>
          </div>
        ` : ""}

        <div class="evidence-sources">
          ${evidence.length ? evidence.map(item => `
            <a class="evidence-row" href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">
              <span class="tier ${item.tier}">${escapeHtml(evidenceTierLabel(item.tier))}</span>
              <span class="evidence-title">${escapeHtml(item.title || item.source || "Evidence candidate")}</span>
              <span class="relevance">${Math.round((item.relevance || 0) * 100)}%</span>
            </a>
          `).join("") : '<div class="quiet-note">No sufficiently relevant evidence candidate found automatically yet.</div>'}
        </div>

        ${factChecks.length ? `
          <div class="factcheck-box">
            <span>PRIOR FACT-CHECKS · ATTRIBUTED</span>
            ${factChecks.map(fc => `
              <a href="${escapeHtml(safeUrl(fc.url))}" target="_blank" rel="noopener noreferrer">
                <strong>${escapeHtml(fc.source || "Fact-check publisher")}</strong>
                <em>${escapeHtml(fc.rating || "Review found")}</em>
              </a>
            `).join("")}
          </div>
        ` : ""}

        ${signal.reason ? `<p class="machine-reason">${escapeHtml(signal.reason)}</p>` : ""}
        <div class="evidence-card-foot">
          <a href="${escapeHtml(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">Original statement ↗</a>
          <span class="verdict-readout ${publishable ? signalClass(level) : "pending"}">
            ${publishable ? "Automated verdict" : "Evidence state"}:
            <b>${escapeHtml(verdictLabel(verdict))}</b>
            ${publishable ? "<small>strict verified-primary gate</small>" : ""}
          </span>
        </div>
      </article>
    `;
  }).join("");
}

function renderClaimMemory() {
  const summary = state.evidence?.summary?.claim_memory || {};
  const families = state.evidence?.claim_families || [];
  const summaryRoot = $("#claim-memory-summary");
  const root = $("#claim-family-list");

  if (!summaryRoot || !root) return;

  summaryRoot.innerHTML = `
    <div><span>Claim families</span><strong>${compactNumber(summary.families_total || 0)}</strong></div>
    <div><span>Total occurrences</span><strong>${compactNumber(summary.occurrences_total || 0)}</strong></div>
    <div><span>Repeated families</span><strong>${compactNumber(summary.repeated_families || 0)}</strong></div>
    <div><span>Repeated contradicted</span><strong>${compactNumber(summary.repeated_contradicted_families || 0)}</strong></div>
  `;

  const repeated = families
    .filter(family => Number(family.occurrence_count || 0) >= 2)
    .slice(0, 16);

  if (!repeated.length) {
    root.innerHTML = `
      <div class="empty-panel">
        <strong>No repeated claim family has been established yet.</strong>
        <p>The memory grows as the same factual proposition appears in later speeches.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = repeated.map(family => {
    const occurrences = family.occurrences || [];
    const verdict = family.current_verdict || "pending";
    return `
      <article class="claim-family-card">
        <div class="claim-family-head">
          <span class="occurrence-count">${family.occurrence_count || occurrences.length} occurrences</span>
          <span class="signal ${verdict === "contradicted" ? "danger" : verdict === "supported" ? "strong" : "pending"}">${escapeHtml(verdictLabel(verdict))}</span>
        </div>
        <blockquote>${escapeHtml(family.canonical_claim || "")}</blockquote>
        <div class="claim-family-meta">
          <span>First seen · ${escapeHtml(formatDate(family.first_seen))}</span>
          <span>Latest · ${escapeHtml(formatDate(family.last_seen))}</span>
          <span>Evidence state · ${escapeHtml(humanize(family.evidence_state || "pending"))}</span>
        </div>
        <details>
          <summary>Show occurrences</summary>
          <div class="occurrence-list">
            ${occurrences.slice().reverse().map(item => `
              <a href="${escapeHtml(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">
                <span>${escapeHtml(formatDate(item.published_at))}</span>
                <strong>${escapeHtml(item.source_title || item.source || "Source")}</strong>
                <small>${escapeHtml(item.text || "")}</small>
              </a>
            `).join("")}
          </div>
        </details>
      </article>
    `;
  }).join("");
}
function promiseEvidenceMap() {
  const map = new Map();
  for (const packet of state.evidence?.promise_packets || []) {
    map.set(packet.promise_id, packet);
  }
  return map;
}

function renderPromiseStats() {
  const summary = state.promises?.summary || {};
  const years = summary.by_year || {};
  $("#promise-year-stats").innerHTML = [2014, 2019, 2024].map(year => `
    <div>
      <span>${year}</span>
      <strong>${years[String(year)] || 0}</strong>
      <small>candidates</small>
    </div>
  `).join("") + `
    <div>
      <span>MEASURABLE</span>
      <strong>${summary.measurable || 0}</strong>
      <small>with numeric/deadline signals</small>
    </div>
    <div>
      <span>REPEATED LINKS</span>
      <strong>${summary.related_links || 0}</strong>
      <small>cross-election matches</small>
    </div>
  `;
}

function renderPromises() {
  const feed = state.promises;
  const root = $("#promise-list");
  const showMore = $("#show-more-promises");

  if (!feed) {
    root.innerHTML = '<div class="loading-card">Promise corpus unavailable.</div>';
    return;
  }

  const errors = feed.errors || [];
  $("#promise-health").innerHTML = errors.length
    ? `<span>Corpus status</span><strong>${errors.length} source error${errors.length === 1 ? "" : "s"}</strong>`
    : `<span>Corpus status</span><strong>${feed.summary?.total || 0} candidates · ${timeAgo(feed.generated_at)}</strong>`;

  renderPromiseStats();

  let items = feed.promises || [];
  if (state.promiseYear !== "all") {
    items = items.filter(item => String(item.year) === state.promiseYear);
  }
  if (state.measurableOnly) {
    items = items.filter(item => item.measurable);
  }

  const evidenceMap = promiseEvidenceMap();

  if (state.promiseOutcome !== "all") {
    items = items.filter(item => {
      const status = evidenceMap.get(item.id)?.outcome?.status || "unaudited";
      if (state.promiseOutcome === "other") {
        return ["deadline_not_reached","insufficient_evidence","not_machine_measurable","unaudited"].includes(status);
      }
      if (state.promiseOutcome === "target") return status === "target_reached_evidence";
      if (state.promiseOutcome === "fulfilled") return status === "fulfilled_by_deadline";
      if (state.promiseOutcome === "unfulfilled") return status === "proven_unfulfilled_by_deadline";
      if (state.promiseOutcome === "progress") return status === "progress_documented";
      if (state.promiseOutcome === "overdue") return status === "deadline_passed_unresolved";
      return true;
    });
  }

  if (state.promiseSearch.trim()) {
    const q = state.promiseSearch.trim().toLowerCase();
    items = items.filter(item => {
      const haystack = [
        item.anchor,
        item.category,
        ...(item.keywords || []),
        ...(item.numbers || []),
        ...(item.deadline_hints || [])
      ].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }

  const visible = items.slice(0, state.promiseLimit);

  if (!visible.length) {
    root.innerHTML = `
      <div class="empty-panel">
        <strong>No promise candidates match these filters.</strong>
        <p>Try another year, category keyword or turn off “measurable only”.</p>
      </div>
    `;
    showMore.classList.add("hidden");
    return;
  }

  root.innerHTML = visible.map(item => {
    const packet = evidenceMap.get(item.id);
    const evidenceCount = packet?.evidence?.length || 0;
    const relatedYears = [...new Set((item.related_promises || []).map(x => x.year))];
    const currentSignal = packet?.implementation_signal || "awaiting_evidence_scan";
    const outcome = packet?.outcome || { status: "unaudited" };
    const proof = outcome?.proof || null;
    const tags = [
      ...(item.numbers || []).slice(0, 2),
      ...(item.deadline_hints || []).slice(0, 1)
    ];

    return `
      <article class="promise-card" id="promise-${escapeHtml(item.id)}">
        <div class="promise-topline">
          <div>
            <span class="year-badge">${item.year}</span>
            <span class="category-badge">${escapeHtml(humanize(item.category))}</span>
          </div>
          <span class="promise-id">${escapeHtml(item.id)}</span>
        </div>
        <h3>${escapeHtml(item.anchor || item.keywords?.join(" · ") || "Promise candidate")}</h3>
        <p class="promise-source">Primary manifesto · page ${item.page} · source text fingerprint preserved</p>

        ${item.exact_text ? `
          <details class="promise-text">
            <summary>Read extracted manifesto text</summary>
            <p>${escapeHtml(item.exact_text)}</p>
          </details>
        ` : ""}

        <div class="promise-tags">
          ${item.measurable ? '<span class="mini-tag measurable">MEASURABLE SIGNAL</span>' : '<span class="mini-tag">QUALITATIVE</span>'}
          ${tags.map(tag => `<span class="mini-tag">${escapeHtml(tag)}</span>`).join("")}
          ${relatedYears.map(year => `<span class="mini-tag history">RELATED ${year}</span>`).join("")}
        </div>

        <div class="promise-evidence-line">
          <span>
            <b>${evidenceCount}</b> current evidence candidate${evidenceCount === 1 ? "" : "s"}
          </span>
          <span class="signal ${promiseOutcomeClass(outcome.status)}">${escapeHtml(promiseOutcomeLabel(outcome.status))}</span>
        </div>

        ${outcome?.reason ? `<p class="machine-reason">${escapeHtml(outcome.reason)}</p>` : ""}

        ${proof ? `
          <div class="proof-box">
            <span>STRUCTURED PROOF</span>
            <div><b>Target</b><strong>${escapeHtml(proof.target?.raw || proof.target?.value)}</strong></div>
            <div><b>Observed</b><strong>${escapeHtml(proof.observed?.raw || proof.observed?.value)}</strong></div>
            <div><b>Evidence year</b><strong>${escapeHtml(proof.evidence_year || "—")}</strong></div>
            <a href="${escapeHtml(safeUrl(proof.evidence_url))}" target="_blank" rel="noopener noreferrer">Open proof source ↗</a>
          </div>
        ` : ""}

        <div class="promise-card-foot">
          <a href="${escapeHtml(safeUrl(item.source_url))}" target="_blank" rel="noopener noreferrer">Manifesto record ↗</a>
          <a href="${escapeHtml(safeUrl(item.pdf_url))}" target="_blank" rel="noopener noreferrer">PDF ↗</a>
          <span>Evidence signal: <b>${escapeHtml(humanize(currentSignal))}</b></span>
        </div>
      </article>
    `;
  }).join("");

  if (items.length > state.promiseLimit) {
    showMore.classList.remove("hidden");
    showMore.textContent = `Show ${Math.min(18, items.length - state.promiseLimit)} more of ${items.length}`;
  } else {
    showMore.classList.add("hidden");
  }
}

function splitSentences(text) {
  return text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?।])\s+/)
    .map(sentence => sentence.trim())
    .filter(sentence => sentence.length >= 20);
}

function scoreSentence(sentence) {
  const lower = sentence.toLowerCase();
  let score = 0;
  const reasons = [];

  if (/\b\d+(?:\.\d+)?\s*(?:%|percent|crore|lakh|million|billion|km|years?|months?|days?|प्रतिशत|करोड़|करोड|लाख|साल|वर्ष|महीने?|दिन)?\b/iu.test(sentence)) {
    score += 3;
    reasons.push("number / quantity");
  }
  if (/\b(?:19|20)\d{2}\b/.test(sentence)) {
    score += 2;
    reasons.push("date / year");
  }
  if (/\b(doubled|tripled|increased|decreased|reduced|highest|lowest|more than|less than|never|always)\b/i.test(lower) || /(दोगुना|तीन गुना|बढ़ा|बढ़ी|बढ़े|घटा|घटी|कम हुआ|सबसे अधिक|सबसे कम|से अधिक|से कम)/u.test(sentence)) {
    score += 2;
    reasons.push("comparison");
  }
  if (/\b(created|built|provided|delivered|achieved|completed|launched|opened|closed|added|removed)\b/i.test(lower) || /(बनाया|बनाए|दिया|दिए|प्रदान किया|पूरा किया|शुरू किया|लॉन्च किया|खोला|जोड़ा)/u.test(sentence)) {
    score += 2;
    reasons.push("accomplishment");
  }
  if (/\b(will|promise|target|by \d{4}|within \d+)\b/i.test(lower) || /(हम करेंगे|हम बनाएंगे|हम देंगे|वादा|लक्ष्य|तक पूरा)/u.test(sentence)) {
    score += 1;
    reasons.push("commitment");
  }
  if (/\b(i think|i believe|should|better|worse|great|terrible)\b/i.test(lower) && score < 3) {
    score -= 2;
  }
  return { score, reasons };
}

function extractCandidates(text) {
  return splitSentences(text)
    .map(sentence => ({ sentence, ...scoreSentence(sentence) }))
    .filter(item => item.score >= 2)
    .sort((a, b) => b.score - a.score);
}

function renderCandidates(candidates) {
  const root = $("#candidate-list");
  const status = $("#extract-status");
  root.innerHTML = "";

  if (!candidates.length) {
    status.textContent = "No strong factual-claim candidates detected.";
    return;
  }

  status.textContent =
    `${candidates.length} candidate${candidates.length === 1 ? "" : "s"} detected. No truth verdict has been assigned.`;

  root.innerHTML = candidates.map((item, index) => `
    <article class="candidate-card">
      <span class="candidate-number">${String(index + 1).padStart(2, "0")}</span>
      <div>
        <p>${escapeHtml(item.sentence)}</p>
        <div class="candidate-reasons">
          ${item.reasons.map(reason => `<span>${escapeHtml(reason)}</span>`).join("")}
        </div>
        <div class="candidate-check-row">
          <button class="mini-check" data-instant-check="${encodeURIComponent(item.sentence)}">Check evidence now</button>
        </div>
        <div class="instant-result" data-instant-result></div>
      </div>
    </article>
  `).join("");
}

async function loadAll() {
  $("#pipeline-state").textContent = "Refreshing source intelligence…";
  const [discovery, promises, evidence] = await Promise.allSettled([
    loadJson(FEEDS.discovery),
    loadJson(FEEDS.promises),
    loadJson(FEEDS.evidence)
  ]);

  state.discovery = discovery.status === "fulfilled" ? discovery.value : null;
  state.promises = promises.status === "fulfilled" ? promises.value : null;
  state.evidence = evidence.status === "fulfilled" ? evidence.value : null;

  renderMetrics();
  renderDiscovery();
  renderEvidence();
  renderClaimMemory();
  renderPromises();

  const loaded = [state.discovery, state.promises, state.evidence].filter(Boolean).length;
  $("#pipeline-state").textContent =
    loaded === 3
      ? "Discovery, memory and evidence feeds connected"
      : `${loaded}/3 intelligence feeds available`;
}

$("#refresh-all").addEventListener("click", loadAll);

$("#candidate-list").addEventListener("click", async event => {
  const button = event.target.closest("[data-instant-check]");
  if (!button) return;

  const claim = decodeURIComponent(button.dataset.instantCheck || "");
  const card = button.closest(".candidate-card");
  const resultRoot = card?.querySelector("[data-instant-result]");
  if (!claim || !resultRoot) return;

  button.disabled = true;
  button.textContent = "Searching evidence…";
  resultRoot.innerHTML = '<div class="quiet-note">Searching official-source leads, reporting and historical promises…</div>';

  try {
    const result = await instantCheck(claim);
    resultRoot.innerHTML = instantResultHtml(result);
    button.textContent = "Refresh evidence";
  } catch (error) {
    resultRoot.innerHTML = `<div class="notice"><strong>Instant check unavailable</strong><span>${escapeHtml(error.message || error)}</span></div>`;
    button.textContent = "Try again";
  } finally {
    button.disabled = false;
  }
});


$$("[data-discovery-filter]").forEach(button => {
  button.addEventListener("click", () => {
    $$("[data-discovery-filter]").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    state.discoveryFilter = button.dataset.discoveryFilter;
    renderDiscovery();
  });
});

function setPromiseOutcomeFilter(value) {
  state.promiseOutcome = value || "all";
  state.promiseLimit = 18;
  renderOutcomeBoard();
  renderPromises();
}

$("#outcome-bar")?.addEventListener("click", event => {
  const button = event.target.closest("[data-outcome-segment]");
  if (!button) return;
  setPromiseOutcomeFilter(button.dataset.outcomeSegment || "all");
  document.querySelector("#promises")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

$$(".outcome-filter").forEach(button => {
  button.addEventListener("click", () => {
    setPromiseOutcomeFilter(button.dataset.outcomeFilter || "all");
  });
});
$$(".promise-year").forEach(button => {
  button.addEventListener("click", () => {
    $$$(".promise-year").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    state.promiseYear = button.dataset.year;
    state.promiseLimit = 18;
    renderPromises();
  });
});

$("#promise-search").addEventListener("input", event => {
  state.promiseSearch = event.target.value;
  state.promiseLimit = 18;
  renderPromises();
});

$("#measurable-only").addEventListener("change", event => {
  state.measurableOnly = event.target.checked;
  state.promiseLimit = 18;
  renderPromises();
});

$("#show-more-promises").addEventListener("click", () => {
  state.promiseLimit += 18;
  renderPromises();
});

$("#evidence-list").addEventListener("click", event => {
  const link = event.target.closest("[data-jump-promise]");
  if (!link) return;
  const id = link.dataset.jumpPromise;
  setTimeout(() => {
    const target = document.getElementById(`promise-${id}`);
    if (target) {
      target.classList.add("flash");
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => target.classList.remove("flash"), 1800);
    }
  }, 80);
});

$("#extract-btn").addEventListener("click", () => {
  const text = $("#transcript").value.trim();
  if (!text) {
    $("#extract-status").textContent = "Paste a transcript first.";
    $("#candidate-list").innerHTML = "";
    return;
  }
  renderCandidates(extractCandidates(text));
});

$("#demo-btn").addEventListener("click", () => {
  $("#transcript").value =
    "In this demonstration speech, the city opened 12 new clinics in 2025. The speaker says covered households increased from 20 percent to 40 percent. I believe this is a great achievement. We will complete 50 additional projects by 2028.";
  renderCandidates(extractCandidates($("#transcript").value));
});

$("#clear-btn").addEventListener("click", () => {
  $("#transcript").value = "";
  $("#extract-status").textContent = "";
  $("#candidate-list").innerHTML = "";
});

window.ClaimWatch = {
  extractCandidates,
  scoreSentence,
  instantCheck,
  instantResultHtml
};

loadAll();
