#!/usr/bin/env node
// Finds how a procurement site loads its list: opens the home page like a visitor, follows up to 3 links that look like the procedure
// search, and prints the public JSON/XHR answers the pages load themselves (method, status, size, items, address, request body, keys of the first item).
//
//   node recon_sites.cjs URL [URL ...] [--out recon.json] [--follow 3] [--pause 3]
//
// Rules: robots.txt of the site is read first (group *, the page itself is not opened when disallowed); TLS verification stays on; no login, no forms,
// no captcha or protection is bypassed (a captcha page is reported as "gate"); cookies, tokens and headers are never printed.
// The answer that looks like a list (an array of 5+ objects with a title-like key) is marked LIST: that is the address to build a collector on.
const fs = require("fs");
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const urls = args.filter((a, i) => /^https?:\/\//.test(a) && !["--out", "--follow", "--pause"].includes(args[i - 1]));
const OUT = opt("--out", ""), FOLLOW = +opt("--follow", 3), PAUSE = +opt("--pause", 3) * 1000;
if (!urls.length) { console.error("usage: node recon_sites.cjs URL [URL ...] [--out f.json] [--follow 3] [--pause 3]"); process.exit(2); }
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector";
const SKIP = /(mc\.yandex|google|doubleclick|facebook|metrika|hotjar|sentry|mail\.ru|vk\.com|top-fwz|counter|jivo|analytics|gtm)/i;
const NAV = /(закуп|торги|процедур|поиск|тендер|лот|запрос|tender|purchase|procedure|lot|search|trade|auction|zakup)/i;
const GATE = /(captcha|капч|just a moment|checking your browser|проверка браузера|подтвердите, что вы не робот|access denied|доступ запрещ|ddos-guard|qrator)/i;
const TITLEKEY = /^(name|title|subject|caption|purchaseName|lotName|tradeName|objectInfo|purchaseObjectInfo)$/i;

function robotsAllows(txt, p) {
  let inStar = false, rules = [];
  for (const raw of txt.split(/\r?\n/)) {
    const m = /^([A-Za-z-]+)\s*:\s*(.*)$/.exec(raw.replace(/#.*/, "").trim());
    if (!m) continue;
    const k = m[1].toLowerCase(), v = m[2].trim();
    if (k === "user-agent") inStar = v === "*";
    else if (inStar && (k === "disallow" || k === "allow") && v) rules.push([k, v]);
  }
  let best = null;
  for (const [k, v] of rules) {
    const re = new RegExp("^" + v.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*"));
    if (re.test(p) && (!best || v.length > best[1].length || (v.length === best[1].length && k === "allow"))) best = [k, v];
  }
  return !best || best[0] === "allow";
}

async function robotsOk(u) {
  try {
    const r = await fetch(u.origin + "/robots.txt", { headers: { "user-agent": UA }, signal: AbortSignal.timeout(20000) });
    const t = r.ok ? await r.text() : "";
    if (t && !/<html/i.test(t.slice(0, 200))) return robotsAllows(t, u.pathname + u.search);
  } catch (e) { /* no robots.txt reachable: nothing to obey */ }
  return true;
}

(async () => {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch();
  const report = [];
  for (const start of urls) {
    const site = { url: start, status: "error", pages: [], note: "" };
    report.push(site);
    console.log("\n########", start);
    try {
      const u0 = new URL(start);
      if (!(await robotsOk(u0))) { site.status = "robots"; console.log("robots.txt запрещает: " + start); continue; }
      const ctx = await browser.newContext({ userAgent: UA, locale: "ru-RU", viewport: { width: 1366, height: 900 } });
      const visit = async (url) => {
        const pg = { url, title: "", head: "", endpoints: [], links: [] };
        const page = await ctx.newPage();
        const pending = [];
        page.on("response", (resp) => {
          const rq = resp.request();
          if (!["xhr", "fetch"].includes(rq.resourceType()) || SKIP.test(resp.url())) return;
          pending.push((async () => {
            try {
              const body = await resp.text();
              if (!/json/i.test(resp.headers()["content-type"] || "") && !/^[\[{]/.test(body.trim())) return;
              const j = JSON.parse(body);
              const arrays = [];
              const walk = (o, path, d) => { if (d > 3 || !o || typeof o !== "object") return; if (Array.isArray(o)) { arrays.push([path, o]); return; } for (const [k, v] of Object.entries(o)) walk(v, path + "." + k, d + 1); };
              walk(j, "", 0);
              const best = arrays.sort((a, b) => b[1].length - a[1].length)[0];
              const first = best && best[1][0] && typeof best[1][0] === "object" ? best[1][0] : null;
              const keys = first ? Object.keys(first).slice(0, 18) : [];
              const list = !!(best && best[1].length >= 5 && keys.some((k) => TITLEKEY.test(k)));
              const ep = { method: rq.method(), status: resp.status(), size: body.length, items: best ? best[1].length : null, arrayAt: best ? best[0] : "",
                url: resp.url().slice(0, 300), body: (rq.postData() || "").slice(0, 300), keys, list,
                total: (j && typeof j === "object" && !Array.isArray(j)) ? Object.entries(j).filter(([, v]) => typeof v === "number").slice(0, 4).map(([k, v]) => k + "=" + v).join(" ") : "" };
              pg.endpoints.push(ep);
            } catch (e) { /* not json */ }
          })());
        });
        try {
          await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
          await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
          await page.waitForTimeout(3500);
          pg.title = await page.title();
          const text = await page.evaluate(() => (document.body ? document.body.innerText : ""));
          pg.head = text.replace(/\s+/g, " ").trim().slice(0, 160);
          if (GATE.test(text.slice(0, 3000)) && text.length < 1500) pg.gate = true;
          pg.links = await page.evaluate(() => Array.from(document.querySelectorAll("a[href]")).map((a) => ({ t: (a.innerText || "").replace(/\s+/g, " ").trim().slice(0, 60), h: a.href })));
        } catch (e) { pg.error = String(e && e.message || e).slice(0, 160); }
        await Promise.all(pending);
        await page.close();
        return pg;
      };
      const home = await visit(start);
      site.pages.push(home);
      site.status = home.gate ? "gate" : home.error ? "error" : "ok";
      if (home.error) site.note = home.error;
      const seen = new Set([start]);
      const cand = home.links.filter((l) => { try { const x = new URL(l.h); return x.origin === u0.origin && NAV.test(l.t + " " + x.pathname) && !seen.has(x.href) && !/(login|auth|registr|news|blog|about|contact|faq|help|tarif|support|\.pdf|\.docx?)/i.test(x.pathname + l.t); } catch { return false; } })
        .filter((l, i, a) => a.findIndex((z) => z.h === l.h) === i).slice(0, FOLLOW);
      for (const l of cand) {
        if (home.gate) break;
        await new Promise((r) => setTimeout(r, PAUSE));
        if (!(await robotsOk(new URL(l.h)))) { site.pages.push({ url: l.h, note: "robots" }); continue; }
        site.pages.push(await visit(l.h));
      }
      await ctx.close();
      for (const pg of site.pages) {
        console.log(`page: ${pg.url.slice(0, 110)} | ${pg.title.slice(0, 50)}${pg.gate ? " | GATE" : ""}${pg.error ? " | ERR " + pg.error : ""}${pg.note ? " | " + pg.note : ""}`);
        for (const e of (pg.endpoints || []).filter((x, i, a) => a.findIndex((z) => z.url === x.url && z.body === x.body) === i))
          console.log(`  ${e.list ? "LIST " : "json "} ${e.method} ${e.status} ${e.size}b items=${e.items} ${e.total} | ${e.url.slice(0, 200)}${e.body ? " | body=" + e.body.slice(0, 160) : ""} | ${e.keys.slice(0, 10).join(",")}`);
      }
    } catch (e) { site.note = String(e && e.message || e).slice(0, 200); console.log("error: " + site.note); }
    await new Promise((r) => setTimeout(r, PAUSE));
  }
  await browser.close();
  if (OUT) fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
})();
