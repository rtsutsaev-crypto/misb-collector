/* Прослойка отдельной версии: страница монитора написана для площадки артефактов Claude и обращается к ней через
   window.claude.use(name). Здесь те же функции поверх API своего сервера (misb/server.py):
   db — документы «коллекция/документ» с подпиской onSnapshot (опрос счётчиков изменений раз в POLL мс);
   user — вошедший по паролю считается владельцем и редактором;
   downloads — сохранение файла через ссылку браузера;
   mcp — вызов fire_trigger (кнопка «Обновить») запускает сбор на сервере. */
(function () {
  "use strict";
  const POLL = 15000;
  const api = (path, opt) => fetch(path, Object.assign({ credentials: "same-origin", cache: "no-store" }, opt || {})).then(async r => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || ("HTTP " + r.status)); e.code = r.status === 409 ? "conflict" : "http_" + r.status; e.status = r.status; throw e; }
    return j;
  });
  const split = p => { p = String(p).replace(/^\/+|\/+$/g, ""); const i = p.lastIndexOf("/"); return [p.slice(0, i), p.slice(i + 1)]; };
  const docSnap = (id, data) => ({ id, exists: data !== null && data !== undefined, data: () => (data === null ? undefined : data) });

  /* подписки: по коллекции — список колбэков; опрос /api/revs и перечитывание изменившихся коллекций */
  const subs = new Map();   // coll -> {rev, docs: Set(fnDocs), cols: Set(fnCol)}
  const subOf = c => { let s = subs.get(c); if (!s) { s = { rev: -1, docs: new Set(), cols: new Set() }; subs.set(c, s); } return s; };
  async function refresh(coll) {
    const s = subs.get(coll); if (!s) return;
    if (s.cols.size) {
      try { const j = await api("/api/col?path=" + encodeURIComponent(coll)); s.rev = j.rev;
        const snap = { docs: j.docs.map(d => docSnap(d.id, d.data)), size: j.docs.length, empty: !j.docs.length };
        s.cols.forEach(f => { try { f.cb(snap); } catch (e) { console.error(e); } });
      } catch (e) { s.cols.forEach(f => f.err && f.err(e)); }
    }
    s.docs.forEach(f => f.reload());
  }
  async function poll() {
    const cs = [...subs.keys()]; if (!cs.length) return;
    try {
      const j = await api("/api/revs?" + cs.map(c => "c=" + encodeURIComponent(c)).join("&"));
      for (const c of cs) { const s = subs.get(c); if (s && j[c] !== s.rev) { s.rev = j[c]; refresh(c); } }
    } catch (e) { /* сеть или сервер недоступны — повторим на следующем круге */ }
  }
  setInterval(poll, POLL);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

  function doc(path) {
    const [coll, id] = split(path);
    const get = async () => { const j = await api("/api/doc?path=" + encodeURIComponent(path)); return docSnap(id, j.data); };
    const write = async (op, data) => { await api("/api/doc?path=" + encodeURIComponent(path), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op, data }) }); refresh(coll); };
    return {
      id, path, get,
      set: data => write("set", data),
      update: data => write("update", data),
      delete: async () => { await api("/api/doc?path=" + encodeURIComponent(path), { method: "DELETE" }); refresh(coll); },
      onSnapshot(cb, err) {
        const s = subOf(coll);
        const f = { reload: () => get().then(cb, e => err && err(e)) };
        s.docs.add(f); f.reload();
        return () => s.docs.delete(f);
      }
    };
  }
  function collection(path) {
    const coll = String(path).replace(/^\/+|\/+$/g, "");
    return {
      path: coll,
      doc: id => doc(coll + "/" + id),
      get: async () => { const j = await api("/api/col?path=" + encodeURIComponent(coll)); return { docs: j.docs.map(d => docSnap(d.id, d.data)), size: j.docs.length, empty: !j.docs.length }; },
      onSnapshot(cb, err) {
        const s = subOf(coll); const f = { cb, err };
        s.cols.add(f); refresh(coll);
        return () => s.cols.delete(f);
      }
    };
  }
  const db = Object.freeze({ doc, collection });

  const user = Object.freeze({
    isOwner: async () => true, canEdit: async () => true, can: async () => true,
    id: async () => "local", me: async () => ({ id: "local", name: "Вы" }),
    profiles: async ids => Object.fromEntries((ids || []).map(i => [i, { id: i, name: i === "local" ? "Вы" : "" }]))
  });

  const downloads = Object.freeze({
    save: async ({ filename, data, type }) => {
      const blob = data instanceof Blob ? data : new Blob([data], { type: type || "text/plain;charset=utf-8" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename || "download";
      document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      return { saved: true };
    }
  });

  const mcp = Object.freeze({
    async callTool(server, tool, input) {
      if (tool !== "fire_trigger") { const e = new Error("инструмент недоступен в отдельной версии: " + tool); e.code = "tool_error"; throw e; }
      try { const j = await api("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "full" }) }); return { payload: j }; }
      catch (e) { const x = new Error(e.status === 409 ? "сбор уже идёт" : (e.message || "сервер не запустил сбор")); x.code = "tool_error"; throw x; }
    }
  });

  const caps = { db, user, downloads, mcp };
  window.claude = Object.freeze({ use: async name => caps[name] || null });
})();
