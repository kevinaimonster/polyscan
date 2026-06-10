export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/api/subscribe" && req.method === "POST") {
      let email = "";
      try { email = ((await req.json()).email || "").trim().toLowerCase(); } catch {}
      if (!/^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
        return Response.json({ ok: false, error: "invalid email" }, { status: 400 });
      }
      await env.SUBS.put("email:" + email, JSON.stringify({
        ts: new Date().toISOString(),
        ip: req.headers.get("cf-connecting-ip") || "",
      }));
      return Response.json({ ok: true });
    }
    return env.ASSETS.fetch(req);
  },
};
