const state = {
  // Demonstration records only. No real political verdicts are shipped as sample data.
  claims: [
    {
      id: "DEMO-C-001",
      speaker: "Demonstration record",
      date: "—",
      text: "This is a placeholder showing how a reviewed factual claim will appear.",
      verdict: "pending",
      evidence: 0
    }
  ],
  promises: [
    {
      id: "DEMO-P-001",
      electionYear: 2014,
      text: "Placeholder commitment — replace with exact primary-source wording during ingestion.",
      status: "pending",
      source: "Primary-source import required"
    },
    {
      id: "DEMO-P-002",
      electionYear: 2019,
      text: "Placeholder commitment — no substantive verdict is included in demo data.",
      status: "pending",
      source: "Primary-source import required"
    },
    {
      id: "DEMO-P-003",
      electionYear: 2024,
      text: "Placeholder commitment — status remains pending until an evidence packet is reviewed.",
      status: "pending",
      source: "Primary-source import required"
    }
  ]
};

const verdictLabel = {
  supported: "SUPPORTED",
  contradicted: "CONTRADICTED",
  context: "MISSING CONTEXT",
  insufficient: "INSUFFICIENT EVIDENCE",
  pending: "PENDING",
  not_checkable: "NOT CHECKABLE"
};

const transcript = document.querySelector("#transcript");
const candidateList = document.querySelector("#candidate-list");
const statusEl = document.querySelector("#extract-status");

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
  statusEl.textContent = `${candidates.length} candidate factual claim${candidates.length === 1 ? "" : "s"} detected. No truth verdict has been assigned.`;
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

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[char]));
}

function renderClaims() {
  const root = document.querySelector("#claim-ledger");
  root.innerHTML = state.claims.map(claim => `
    <article class="claim-card">
      <div class="claim-top">
        <span class="meta">${escapeHtml(claim.id)} · ${escapeHtml(claim.speaker)}</span>
        <span class="verdict v-${claim.verdict}">${verdictLabel[claim.verdict]}</span>
      </div>
      <p>${escapeHtml(claim.text)}</p>
      <span class="meta">Evidence items: ${claim.evidence}</span>
    </article>
  `).join("");
  updateStats();
}

function renderPromises(year = "all") {
  const root = document.querySelector("#promise-list");
  const items = state.promises.filter(p => year === "all" || String(p.electionYear) === String(year));
  root.innerHTML = items.map(p => `
    <article class="promise-card">
      <div class="promise-top">
        <span class="meta">${escapeHtml(p.id)} · ${p.electionYear}</span>
        <span class="verdict v-${p.status}">${verdictLabel[p.status]}</span>
      </div>
      <p>${escapeHtml(p.text)}</p>
      <span class="meta">${escapeHtml(p.source)}</span>
    </article>
  `).join("");
}

function updateStats() {
  const counts = state.claims.reduce((acc, c) => {
    acc.total += 1;
    acc[c.verdict] = (acc[c.verdict] || 0) + 1;
    return acc;
  }, { total: 0 });
  document.querySelector("#total-count").textContent = counts.total || 0;
  document.querySelector("#supported-count").textContent = counts.supported || 0;
  document.querySelector("#contradicted-count").textContent = counts.contradicted || 0;
  document.querySelector("#context-count").textContent = counts.context || 0;
  document.querySelector("#pending-count").textContent = (counts.pending || 0) + (counts.insufficient || 0);
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
  transcript.value = "In this demonstration speech, the city opened 12 new clinics in 2025. The speaker says the number of covered households doubled from 20 percent to 40 percent. I believe this is a great achievement. We will complete 50 additional projects by 2028.";
  renderCandidates(extractCandidates(transcript.value));
});

document.querySelector("#clear-btn").addEventListener("click", () => {
  transcript.value = "";
  candidateList.innerHTML = "";
  statusEl.textContent = "";
});

document.querySelectorAll(".filter").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    renderPromises(button.dataset.year);
  });
});

renderClaims();
renderPromises();
