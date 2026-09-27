const STOPWORDS = new Set([
  "the","a","an","and","or","of","to","in","for","on","with","by","from","as","at","that","this",
  "is","are","was","were","be","been","being","we","our","will","shall","would","can","could","may",
  "more","all","its","their","they","it","into","through","across","over","under","such","these","those",
  "also","prime","minister","narendra","modi","india","indian","government","bjp","bharat","people",
  "country","has","have","had","did","does","do","than","since","during","today","now"
]);

const OFFICIAL_HOSTS = [
  "pib.gov.in",
  "rbi.org.in",
  "mospi.gov.in",
  "cag.gov.in",
  "indiabudget.gov.in",
  "data.gov.in",
  "sansad.in",
  "parliamentofindia.nic.in",
  "indiacode.nic.in"
];

const PROMISE_FEED =
  "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/promises/candidates.json";
const EVIDENCE_FEED =
  "https://raw.githubusercontent.com/archeearjun/claimwatch-india/main/data/evidence/latest.json";

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store"
    }
  });
}

function stripHtml(value) {
  return String(value || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function decodeXml(value) {
  return stripHtml(value)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .trim();
}

function xmlTag(block, tag) {
  const match = block.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return match ? decodeXml(match[1]) : "";
}

function sourceInfo(block) {
  const match = block.match(/<source(?:\s+url=["']([^"']+)["'])?[^>]*>([\s\S]*?)<\/source>/i);
  if (!match) return { name: "", url: "" };
  return { name: decodeXml(match[2]), url: decodeXml(match[1] || "") };
}

function parseRss(xml, limit = 8) {
  const blocks = String(xml || "").match(/<item\b[\s\S]*?<\/item>/gi) || [];
  return blocks.slice(0, limit).map(block => {
    const source = sourceInfo(block);
    return {
      title: xmlTag(block, "title"),
      url: xmlTag(block, "link"),
      published_at: xmlTag(block, "pubDate"),
      description: xmlTag(block, "description"),
      source: source.name,
      source_url: source.url
    };
  });
}

function tokens(text) {
  return (String(text || "").toLowerCase().match(/\p{L}[\p{L}\p{M}\p{N}-]{1,}/gu) || [])
    .filter(token => token.length >= 2 && !STOPWORDS.has(token));
}

function keywords(text, limit = 8) {
  const counts = new Map();
  for (const token of tokens(text)) counts.set(token, (counts.get(token) || 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([token]) => token);
}

function numericTokens(text) {
  const values = String(text || "").match(/\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b/g) || [];
  return [...new Set(values.map(value => value.replace(/,/g, "")))];
}

function matchingTerms(queryTerms, evidenceText) {
  const q = new Set(queryTerms);
  const e = new Set(keywords(evidenceText, 16));
  return [...q].filter(term => e.has(term));
}

function overlap(queryTerms, evidenceText) {
  if (!queryTerms.length) return 0;
  return matchingTerms(queryTerms, evidenceText).length / queryTerms.length;
}

function claimSimilarity(a, b) {
  const aa = new Set(keywords(a, 16));
  const bb = new Set(keywords(b, 16));
  if (!aa.size || !bb.size) return 0;
  let shared = 0;
  for (const term of aa) if (bb.has(term)) shared += 1;
  const lexical = shared / Math.max(aa.size, bb.size);

  const an = new Set(numericTokens(a));
  const bn = new Set(numericTokens(b));
  let numeric = 1;
  if (an.size || bn.size) {
    const sharedNums = [...an].filter(value => bn.has(value)).length;
    numeric = sharedNums / Math.max(an.size, bn.size, 1);
  }

  return lexical * 0.78 + numeric * 0.22;
}

async function cachedAssessment(claim) {
  try {
    const response = await fetch(EVIDENCE_FEED, {
      cf: { cacheTtl: 120, cacheEverything: true }
    });
    if (!response.ok) return null;
    const payload = await response.json();

    let best = null;
    for (const packet of payload.claim_packets || []) {
      if (!packet?.signal?.publishable_verdict) continue;
      const score = claimSimilarity(claim, packet.claim || "");
      if (!best || score > best.score) best = { packet, score };
    }

    if (!best || best.score < 0.78) return null;

    const claimNums = new Set(numericTokens(claim));
    const cachedNums = new Set(numericTokens(best.packet.claim || ""));
    if (claimNums.size || cachedNums.size) {
      const shared = [...claimNums].filter(value => cachedNums.has(value)).length;
      if (shared === 0) return null;
    }

    return {
      ...best.packet,
      cache_similarity: Number(best.score.toFixed(3)),
    };
  } catch {
    return null;
  }
}

function hostname(value) {
  try {
    return new URL(value).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
}

function isOfficial(row) {
  const hosts = [hostname(row.source_url), hostname(row.url)].filter(Boolean);
  return hosts.some(host =>
    OFFICIAL_HOSTS.some(official => host === official || host.endsWith(`.${official}`))
  );
}

async function googleNews(query, limit = 8) {
  const url = new URL("https://news.google.com/rss/search");
  url.searchParams.set("q", query);
  url.searchParams.set("hl", "en-IN");
  url.searchParams.set("gl", "IN");
  url.searchParams.set("ceid", "IN:en");

  const response = await fetch(url, {
    headers: { "user-agent": "ClaimWatchIndia/0.5" }
  });
  if (!response.ok) throw new Error(`Google News RSS HTTP ${response.status}`);
  return parseRss(await response.text(), limit);
}

async function pibLatest() {
  const url = "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1";
  try {
    const response = await fetch(url, {
      headers: { "user-agent": "ClaimWatchIndia/0.5" }
    });
    if (!response.ok) return [];
    return parseRss(await response.text(), 60).map(row => ({
      ...row,
      source: row.source || "Press Information Bureau",
      source_url: row.source_url || "https://pib.gov.in/"
    }));
  } catch {
    return [];
  }
}

async function factCheckSearch(claim, key) {
  if (!key) return [];
  const url = new URL("https://factchecktools.googleapis.com/v1alpha1/claims:search");
  url.searchParams.set("query", claim.slice(0, 500));
  url.searchParams.set("languageCode", "en");
  url.searchParams.set("pageSize", "6");
  url.searchParams.set("key", key);

  const response = await fetch(url);
  if (!response.ok) return [];
  const payload = await response.json();
  const rows = [];
  for (const item of payload.claims || []) {
    for (const review of item.claimReview || []) {
      rows.push({
        source: review.publisher?.name || review.publisher?.site || "Fact-check",
        source_url: review.publisher?.site || "",
        title: review.title || item.text || "Fact-check review",
        url: review.url || "",
        published_at: review.reviewDate || item.claimDate || null,
        rating: review.textualRating || null,
        claim_text: item.text || "",
        tier: "fact_check"
      });
    }
  }
  return rows;
}

async function promiseMatches(claim) {
  try {
    const response = await fetch(PROMISE_FEED, {
      cf: { cacheTtl: 300, cacheEverything: true }
    });
    if (!response.ok) return [];
    const payload = await response.json();
    const qTerms = keywords(claim, 10);
    return (payload.promises || [])
      .map(item => {
        const text = [
          item.exact_text || item.anchor || "",
          ...(item.keywords || []),
          ...(item.numbers || []),
          ...(item.deadline_hints || [])
        ].join(" ");
        const matched = matchingTerms(qTerms, text);
        return {
          id: item.id,
          year: item.year,
          category: item.category,
          page: item.page,
          anchor: item.anchor,
          source_url: item.source_url,
          pdf_url: item.pdf_url,
          similarity: qTerms.length ? matched.length / qTerms.length : 0,
          matched_terms: matched.slice(0, 6)
        };
      })
      .filter(item => item.similarity >= 0.15 && item.matched_terms.length >= 2)
      .sort((a, b) => b.similarity - a.similarity)
      .slice(0, 5)
      .map(item => ({ ...item, similarity: Number(item.similarity.toFixed(3)) }));
  } catch {
    return [];
  }
}

function rankEvidence(claim, rows) {
  const qTerms = keywords(claim, 12);
  const qNumbers = new Set(numericTokens(claim));

  return rows
    .map(row => {
      const evidenceText = [row.title, row.description, row.claim_text].filter(Boolean).join(" ");
      const evidenceNumbers = numericTokens(evidenceText);
      const sharedNumbers = evidenceNumbers.filter(value => qNumbers.has(value));
      const matchedTerms = matchingTerms(qTerms, evidenceText);
      const official = isOfficial(row);

      let relevance = qTerms.length ? matchedTerms.length / qTerms.length : 0;
      relevance += Math.min(0.18, matchedTerms.length * 0.035);
      if (sharedNumbers.length) relevance += Math.min(0.22, sharedNumbers.length * 0.11);
      if (official) relevance += 0.05;

      return {
        ...row,
        tier: row.tier || (official ? "primary" : "secondary"),
        relevance: Number(Math.min(1, relevance).toFixed(3)),
        matched_terms: matchedTerms.slice(0, 8),
        shared_numbers: sharedNumbers,
        evidence_numbers: evidenceNumbers.slice(0, 8)
      };
    })
    .filter(row =>
      row.tier === "fact_check" ||
      row.shared_numbers.length ||
      (row.matched_terms.length >= 2 && row.relevance >= 0.13)
    )
    .sort((a, b) => b.relevance - a.relevance)
    .slice(0, 10);
}

function buildSignal(claim, evidence) {
  const claimNumbers = numericTokens(claim);
  const primary = evidence.filter(item =>
    item.tier === "primary" &&
    item.relevance >= 0.22 &&
    (item.matched_terms?.length || 0) >= 2
  );
  const primaryShared = primary.filter(item => item.shared_numbers?.length);

  if (primaryShared.length) {
    return {
      level: "primary_numeric_match_candidate",
      label: "Primary-source lead with matching numeric value",
      verdict: "pending",
      reason: "The same number appears in a relevant primary-source lead, but units, dates, population and methodology still need comparability checks."
    };
  }

  if (claimNumbers.length && primary.length) {
    const primaryWithOtherNumbers = primary.filter(item =>
      item.evidence_numbers?.length && !item.shared_numbers?.length
    );
    if (primaryWithOtherNumbers.length) {
      return {
        level: "possible_numeric_conflict",
        label: "Possible numeric mismatch",
        verdict: "pending",
        reason: "A relevant primary-source lead contains different numeric values. This is not a contradiction verdict until the metric, date, unit and population are confirmed comparable."
      };
    }
  }

  if (primary.length) {
    return {
      level: "primary_evidence_found",
      label: "Relevant primary-source lead found",
      verdict: "pending",
      reason: "Primary-source material was found, but ClaimWatch has not established that it directly proves or disproves the claim."
    };
  }

  if (evidence.some(item => item.tier === "fact_check")) {
    return {
      level: "prior_fact_check_found",
      label: "Prior fact-check found",
      verdict: "pending",
      reason: "An outside fact-check is available. Its rating remains attributed to its publisher and is not automatically adopted."
    };
  }

  if (evidence.length) {
    return {
      level: "reporting_found",
      label: "Relevant reporting found",
      verdict: "pending",
      reason: "Current reporting was found, but stronger primary evidence is still needed for a ClaimWatch verdict."
    };
  }

  return {
    level: "needs_research",
    label: "No strong evidence lead found",
    verdict: "pending",
    reason: "The automatic search did not find a sufficiently relevant source."
  };
}

export async function onRequestPost(context) {
  let body;
  try {
    body = await context.request.json();
  } catch {
    return json({ error: "Expected JSON body." }, 400);
  }

  const claim = String(body?.claim || "").replace(/\s+/g, " ").trim();
  if (claim.length < 12 || claim.length > 1200) {
    return json({ error: "Claim must be between 12 and 1200 characters." }, 400);
  }

  const cached = await cachedAssessment(claim);
  if (cached) {
    return json({
      checked_at: new Date().toISOString(),
      claim,
      cached: true,
      matched_claim: cached.claim,
      cache_similarity: cached.cache_similarity,
      signal: cached.signal,
      verdict: cached.verdict,
      evidence: cached.evidence || [],
      fact_checks: cached.fact_checks || [],
      promise_matches: cached.promise_matches || [],
      claim_source: cached.claim_source || null,
      note: "Returned from a previously generated strict evidence packet for a near-duplicate claim."
    });
  }

  const terms = keywords(claim, 7);
  const numbers = numericTokens(claim).slice(0, 3);
  const core = [...numbers, ...terms].join(" ").slice(0, 240);

  const officialDomains =
    "(site:pib.gov.in OR site:rbi.org.in OR site:mospi.gov.in OR site:cag.gov.in OR site:data.gov.in OR site:indiabudget.gov.in OR site:sansad.in OR site:indiacode.nic.in)";

  const tasks = await Promise.allSettled([
    googleNews(`${core} ${officialDomains}`, 8),
    googleNews(core, 8),
    pibLatest(),
    factCheckSearch(claim, context.env?.FACTCHECK_API_KEY),
    promiseMatches(claim)
  ]);

  const officialSearch = tasks[0].status === "fulfilled" ? tasks[0].value : [];
  const newsSearch = tasks[1].status === "fulfilled" ? tasks[1].value : [];
  const pib = tasks[2].status === "fulfilled"
    ? tasks[2].value
        .map(row => ({ ...row, score: overlap(terms, [row.title,row.description].join(" ")) }))
        .filter(row => row.score >= 0.18)
        .sort((a,b) => b.score-a.score)
        .slice(0,6)
    : [];
  const factChecks = tasks[3].status === "fulfilled" ? tasks[3].value : [];
  const promises = tasks[4].status === "fulfilled" ? tasks[4].value : [];

  const evidence = rankEvidence(claim, [
    ...pib,
    ...officialSearch,
    ...newsSearch,
    ...factChecks
  ]);

  return json({
    checked_at: new Date().toISOString(),
    claim,
    query: core,
    claim_numbers: numbers,
    signal: buildSignal(claim, evidence),
    evidence,
    promise_matches: promises,
    errors: tasks
      .map((task, index) =>
        task.status === "rejected"
          ? { stage: ["official_search","news_search","pib","fact_check","promise_match"][index], error: String(task.reason?.message || task.reason) }
          : null
      )
      .filter(Boolean),
    note: "This endpoint retrieves evidence leads immediately. A pending signal is not a final truth verdict."
  });
}

export function onRequestGet() {
  return json({
    ok: true,
    service: "ClaimWatch instant evidence check",
    method: "POST",
    body: { claim: "A checkable public statement" }
  });
}
