#!/usr/bin/env node
// Reads a procurement list that is built by JavaScript (single-page sites) with a headless browser.
//
//   node render_list.cjs URL [--out file.json] [--scrolls 3] [--wait 6000]
//
// What it does and does not do:
//   * checks robots.txt of the site first (User-agent: * and the generic rules); a disallowed path is not opened;
//   * opens the page once like a browser, scrolls a few screens so lazy lists load, then reads the rendered text;
//   * stops on a captcha, "checking your browser" or an access-denied page: no workaround is attempted;
//   * never logs in, never fills forms, never opens anything behind registration;
//   * TLS verification stays on.
// Output (JSON): status (ok | robots | gate | error), title, textHead (first 300 chars), textLength, procWords, dates, rows[] (text + link),
// jsonEndpoints[] (public JSON answers the page itself loaded: url, size, first keys).
//
// Playwright and Chromium are already installed in the Claude Code cloud image (PLAYWRIGHT_BROWSERS_PATH).
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const url = args[0];
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
if (!url || !/^https?:\/\//.test(url)) {
  console.error("usage: node render_list.cjs URL [--out file.json] [--scrolls 3] [--wait 6000]");
  process.exit(2);
}
const OUT = opt("--out", "");
const SCROLLS = +opt("--scrolls", 3);
const WAIT = +opt("--wait", 6000);
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector";

function robotsAllows(txt, p) {
  // minimal parser: rules of the "*" group; longest matching rule wins, Allow beats Disallow on a tie
  let inStar = false, seen = false, rules = [];
  for (const raw of txt.split(/\r?\n/)) {
    const line = raw.replace(/#.*/, "").trim();
    const m = /^([A-Za-z-]+)\s*:\s*(.*)$/.exec(line);
    if (!m) { if (!line) seen = seen; continue; }
    const k = m[1].toLowerCase(), v = m[2].trim();
    if (k === "user-agent") { inStar = v === "*"; }
    else if (inStar && (k === "disallow" || k === "allow") && v) rules.push([k, v]);
  }
  let best = null;
  for (const [k, v] of rules) {
    const re = new RegExp("^" + v.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\\\$$/, "$"));
    if (re.test(p) && (!best || v.length > best[1].length || (v.length === best[1].length && k === "allow"))) best = [k, v];
  }
  return !best || best[0] === "allow";
}

const GATE = /(captcha|капч|recaptcha|hcaptcha|just a moment|checking your browser|проверка браузера|подтвердите, что вы не робот|access denied|доступ запрещ|ddos-guard|qrator|cloudflare)/i;
const PROC = /(закупк|тендер|конкурс|лот|запрос предложений|запрос котировок|аукцион|процедур|заявк|приём заявок|прием заявок)/gi;
const DATE = /\b(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b/g;

(async () => {
  const res = { url, status: "error", title: "", textHead: "", textLength: 0, procWords: 0, dates: 0, rows: [], jsonEndpoints: [], note: "" };
  const finish = () => {
    const s = JSON.stringify(res, null, 1);
    if (OUT) fs.writeFileSync(OUT, s); else console.log(s);
  };
  try {
    const u = new URL(url);
    try {
      const r = await fetch(u.origin + "/robots.txt", { headers: { "user-agent": UA }, signal: AbortSignal.timeout(20000) });
      if (r.ok && !/<html/i.test((await r.clone().text()).slice(0, 200))) {
        if (!robotsAllows(await r.text(), u.pathname + u.search)) { res.status = "robots"; res.note = "robots.txt запрещает этот раздел"; return finish(); }
      }
    } catch (e) { /* no robots.txt reachable: nothing to obey */ }

    const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
    const browser = await chromium.launch();
    const ctx = await browser.newContext({ userAgent: UA, locale: "ru-RU", viewport: { width: 1366, height: 900 } });
    const page = await ctx.newPage();
    page.on("response", async (resp) => {
      try {
        const ct = resp.headers()["content-type"] || "";
        if (!/json/i.test(ct) || resp.request().resourceType() === "image") return;
        const body = await resp.text();
        if (body.length < 300 || body.length > 6e6) return;
        let j; try { j = JSON.parse(body); } catch { return; }
        const arr = Array.isArray(j) ? j : (j && typeof j === "object" ? Object.values(j).find(Array.isArray) : null);
        res.jsonEndpoints.push({ url: resp.url().slice(0, 200), size: body.length, items: arr ? arr.length : null,
          keys: (arr && arr[0] && typeof arr[0] === "object" ? Object.keys(arr[0]) : Object.keys(j || {})).slice(0, 14) });
      } catch { /* response body gone */ }
    });
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(WAIT);
    for (let i = 0; i < SCROLLS; i++) { await page.mouse.wheel(0, 1600); await page.waitForTimeout(900); }
    res.title = await page.title();
    const text = await page.evaluate(() => document.body ? document.body.innerText : "");
    res.textLength = text.length;
    res.textHead = text.replace(/\s+/g, " ").trim().slice(0, 300);
    if (GATE.test(text.slice(0, 3000)) && text.length < 1500) { res.status = "gate"; res.note = "страница-заслон (капча или проверка); не обходится"; await browser.close(); return finish(); }
    res.procWords = (text.match(PROC) || []).length;
    res.dates = (text.match(DATE) || []).length;
    res.rows = await page.evaluate(() => {
      const out = [], seen = new Set();
      const push = (el) => {
        const t = (el.innerText || "").replace(/\s+/g, " ").trim();
        if (t.length < 25 || t.length > 600 || seen.has(t)) return;
        seen.add(t);
        const a = el.querySelector("a[href]") || (el.tagName === "A" ? el : null);
        out.push({ text: t.slice(0, 400), link: a ? a.href : "" });
      };
      document.querySelectorAll("tr, li, article, [class*=card], [class*=item], [class*=row], [class*=lot], [class*=tender]").forEach(push);
      return out.slice(0, 120);
    });
    res.status = "ok";
    await browser.close();
  } catch (e) {
    res.note = String(e && e.message || e).slice(0, 300);
  }
  finish();
})();
