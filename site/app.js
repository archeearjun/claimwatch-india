const DISCOVERY_FEED =
  "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/discovery/latest.json";

const state = {
  claims: [],
  promises: [],
  discoveries: []
};

const verdictLabel = {
  supported: "SUPPORTED",
  contradicted: "CONTRADICTED",
  context: "MISSING CONTEXT",
  insufficient: "INSUFFICIENT EVIDENCE",
  pending: "PENDING",
  not_checkable: "NOT CHECKABLE",
  complete: "COMPLETE",
  partial: "PARTIAL",
  no_documented_implementation: "NO DOCUMENTED IMPLEMENTATION",
  not_measurable: "NOT MEASURABLE",
  deadline_not_reached: "DEADLINE NOT REACHED"
};

const transcript = document.querySelector("#transcript");
const candidateList = document.querySelector("#candidate-list");
const statusEl = document.querySelector("#extract-status");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
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

function splitSentences(text) {
  return text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?।])\s+/)
    .map(s => s.trim())
    .filter(s => s.length >= 20);
}

function scoreSentence(sentence) {
  const s = sentence.toLowerCase();
  let score = 0;
  const reasons = [];

  if (/\b\d+(?:\.\d+)?\s*(?:%|percent|crore|lakh|million|billion|km|years?|months?|days?)?\b/i.test(sentence)) {
    score += 3; reasons.push("number/quantity");
  }
  if (/\b(19|20)\d{2}\b/.test(sentence)) {
    score += 2; reasons.push("date/year");
  }
  if (/\b(doubled|tripled|increased|decreased|reduced|highest|lowest|more than|less than|only|never|always)\b/i.test(s)) {
    score += 2; reasons.push("comparison");
  }
  if (/\b(created|built|provided|delivered|achieved|completed|launched|opened|closed|gave|added|removed)\b/i.test(s)) {
    score += 2; reasons.push("accomplishment claim");
  }
  if (/\b(will|promise|target|by \d{4}|within \d+)\b/i.test(s)) {
    score += 1; reasons.push("future commitment");
  }
  if (/\b(i think|i believe|should|better|worse|great|terrible)\b/i.test(s) && score < 3) {
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
  candidateList.innerHTML = "";
  if (!candidates.length) {
    statusEl.textContent = "No strong factual-claim candidates detected. Try a longer transcript with dates, quantities, comparisons or concrete accomplishments.";
    return;
  }

  statusEl.textContent =
    `${candidates.length} candidate factual claim${candidates.length === 1 ? "" : "s"} detected. No truth verdict has been assigned.`;

  candidates.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "candidate";
    card.innerHTML = `
      <span class="candidate-index">${String(index + 1).padStart(2, "0")}</span>
      <p>${escapeHtml(item.sentence)}</p>
      <span class="tag">${escapeHtml(item.reasons.join(" · "))}</span>
    `;
    candidateList.appendChild(card);
  });
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function discoveryKindLabel(kind) {
  return ({
    news: "NEWS / WEB",
    official_speech_or_video: "OFFICIAL SPEECH",
    youtube_video: "YOUTUBE"
  })[kind] || String(kind || "SOURCE").toUpperCase();
}

function renderDiscoveryFeed(feed) {
  const items = Array.isArray(feed.items) ? feed.items : [];
  const errors = Array.isArray(feed.errors) ? feed.errors : [];
  state.discoveries = items;

  document.querySelector("#scanner-count").textContent = items.length;
  document.querySelector("#scanner-time").textContent = formatTime(feed.generated_at);

  const status = document.querySelector("#scanner-status");
  if (!feed.generated_at) {
    status.textContent = "Waiting for the first scanner run";
  } else if (items.length) {
    status.textContent = "Scanner feed available";
  } else {
    status.textContent = "Scan completed; no matching sources found";
  }

  const notices = document.querySelector("#scanner-notices");
  const youtubeMissing = errors.some(e =>
    e && e.source === "youtube" && String(e.error || "").includes("not_configured")
  );

  const noticeRows = [];
  if (youtubeMissing) {
    noticeRows.push(
      '<div class="notice warn"><strong>YouTube discovery not connected yet.</strong> Add the free YouTube Data API key as the GitHub secret <code>YOUTUBE_API_KEY</code>.</div>'
    );
  }

  errors
    .filter(e => !(e && e.source === "youtube" && String(e.error || "").includes("not_configured")))
    .slice(0, 3)
    .forEach(e => {
      noticeRows.push(
        `<div class="notice"><strong>${escapeHtml(e.source || "Scanner")}:</strong> ${escapeHtml(e.error || "scanner notice")}</div>`
      );
    });

  notices.innerHTML = noticeRows.join("");

  const root = document.querySelector("#discovery-list");
  if (!items.length) {
    root.innerHTML = `
      <div class="empty-state">
        <strong>No discovery records are available yet.</strong>
        <p>The scheduled GitHub scanner may not have completed its first run. Once it does, recent sources will appear here automatically.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = items.slice(0, 24).map(item => {
    const claims = Array.isArray(item.candidate_claims) ? item.candidate_claims : [];
    const firstClaim = claims[0];
    const url = safeUrl(item.url);

    return `
      <article class="discovery-card">
        <div class="discovery-top">
          <div>
            <span class="source-type">${escapeHtml(discoveryKindLabel(item.kind))}</span>
            <span class="meta">${escapeHtml(item.source || item.channel_title || "Source")}</span>
          </div>
          <span class="meta">${escapeHtml(formatTime(item.published_at))}</span>
        </div>
        <h3>${escapeHtml(item.title || "Untitled source")}</h3>
        ${firstClaim ? `
          <div class="candidate-claim">
            <span>Candidate claim · unverified</span>
            <p>${escapeHtml(firstClaim)}</p>
          </div>
        ` : ""}
        <div class="discovery-footer">
          <span class="meta">${claims.length} candidate claim${claims.length === 1 ? "" : "s"} extracted</span>
          <a class="source-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>
        </div>
      </article>
    `;
  }).join("");
}

async function loadDiscoveryFeed() {
  const status = document.querySelector("#scanner-status");
  status.textContent = "Loading discovery feed…";

  try {
    const response = await fetch(
      `${DISCOVERY_FEED}?t=${Date.now()}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const feed = await response.json();
    renderDiscoveryFeed(feed);
  } catch (error) {
    status.textContent = "Discovery feed unavailable";
    document.querySelector("#discovery-list").innerHTML = `
      <div class="empty-state">
        <strong>Could not load the public discovery feed.</strong>
        <p>${escapeHtml(error.message || "Unknown error")}</p>
      </div>
    `;
  }
}

function renderClaims() {
  const root = document.querySelector("#claim-ledger");

  if (!state.claims.length) {
    root.innerHTML = `
      <div class="empty-state">
        <strong>No reviewed verdicts published yet.</strong>
        <p>ClaimWatch will only publish a substantive verdict after its evidence packet exists.</p>
      </div>
    `;
    updateStats();
    return;
  }

  root.innerHTML = state.claims.map(claim => `
    <article class="claim-card">
      <div class="claim-top">
        <span class="meta">${escapeHtml(claim.id)} · ${escapeHtml(claim.speaker)}</span>
        <span class="verdict v-${claim.verdict}">${escapeHtml(verdictLabel[claim.verdict] || claim.verdict)}</span>
      </div>
      <p>${escapeHtml(claim.text)}</p>
      <span class="meta">Evidence items: ${Number(claim.evidence || 0)}</span>
    </article>
  `).join("");

  updateStats();
}

function renderPromises(year = "all") {
  const root = document.querySelector("#promise-list");
  const items = state.promises.filter(
    p => year === "all" || String(p.electionYear) === String(year)
  );

  if (!items.length) {
    root.innerHTML = `
      <div class="empty-state">
        <strong>No reviewed promise records published yet.</strong>
        <p>The 2014, 2019 and 2024 primary-source manifesto corpus is the next data layer.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = items.map(p => `
    <article class="promise-card">
      <div class="promise-top">
        <span class="meta">${escapeHtml(p.id)} · ${p.electionYear}</span>
        <span class="verdict v-${p.status}">${escapeHtml(verdictLabel[p.status] || p.status)}</span>
      </div>
      <p>${escapeHtml(p.text)}</p>
      <span class="meta">${escapeHtml(p.source)}</span>
    </article>
  `).join("");
}

function updateStats() {
  const counts = state.claims.reduce((acc, claim) => {
    acc.total += 1;
    acc[claim.verdict] = (acc[claim.verdict] || 0) + 1;
    return acc;
  }, { total: 0 });

  document.querySelector("#total-count").textContent = counts.total || 0;
  document.querySelector("#supported-count").textContent = counts.supported || 0;
  document.querySelector("#contradicted-count").textContent = counts.contradicted || 0;
  document.querySelector("#context-count").textContent = counts.context || 0;
  document.querySelector("#pending-count").textContent =
    (counts.pending || 0) + (counts.insufficient || 0);
}

document.querySelector("#extract-btn").addEventListener("click", () => {
  const text = transcript.value.trim();
  if (!text) {
    statusEl.textContent = "Paste a transcript first.";
    candidateList.innerHTML = "";
    return;
  }
  renderCandidates(extractCandidates(text));
});

document.querySelector("#demo-btn").addEventListener("click", () => {
  transcript.value =
    "In this demonstration speech, the city opened 12 new clinics in 2025. The speaker says the number of covered households doubled from 20 percent to 40 percent. I believe this is a great achievement. We will complete 50 additional projects by 2028.";
  renderCandidates(extractCandidates(transcript.value));
});

document.querySelector("#clear-btn").addEventListener("click", () => {
  transcript.value = "";
  candidateList.innerHTML = "";
  statusEl.textContent = "";
});

document.querySelector("#refresh-discoveries").addEventListener("click", loadDiscoveryFeed);

document.querySelectorAll(".filter").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    renderPromises(button.dataset.year);
  });
});

renderClaims();
renderPromises();
loadDiscoveryFeed();
