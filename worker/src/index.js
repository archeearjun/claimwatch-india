function json(data, init = {}) {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      ...(init.headers || {})
    }
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,POST,OPTIONS",
          "access-control-allow-headers": "content-type"
        }
      });
    }

    if (url.pathname === "/api/health") {
      return json({ ok: true, service: "claimwatch-api", version: "0.1.0" });
    }

    if (url.pathname === "/api/claims" && request.method === "GET") {
      if (!env.DB) return json({ error: "D1 binding DB is not configured" }, { status: 503 });
      const result = await env.DB.prepare(`
        SELECT c.id, c.exact_text, c.statement_at, c.review_status,
               s.name AS speaker_name
        FROM claims c
        LEFT JOIN speakers s ON s.id = c.speaker_id
        ORDER BY c.statement_at DESC, c.created_at DESC
        LIMIT 100
      `).all();
      return json({ claims: result.results || [] });
    }

    if (url.pathname === "/api/promises" && request.method === "GET") {
      if (!env.DB) return json({ error: "D1 binding DB is not configured" }, { status: 503 });
      const year = url.searchParams.get("year");
      const stmt = year
        ? env.DB.prepare("SELECT * FROM promises WHERE election_year = ? ORDER BY id").bind(Number(year))
        : env.DB.prepare("SELECT * FROM promises ORDER BY election_year, id");
      const result = await stmt.all();
      return json({ promises: result.results || [] });
    }

    return json({ error: "Not found" }, { status: 404 });
  }
};
