// Shared page helpers: toast, copy to clipboard, BBCode preview, relative times, the running-bake dot in the nav.
(() => {
  let toastTimer;
  window.tbToast = (text, kind) => {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = text; t.dataset.kind = kind || "ok"; t.classList.add("on");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 2400);
  };

  // plain-http LAN pages have no clipboard API: fall back to selecting the field
  window.copyText = (id, label) => {
    const t = document.getElementById(id), done = () => tbToast((label || "Text") + " copied");
    const legacy = () => { t.select(); document.execCommand("copy"); t.setSelectionRange(0, 0); t.blur(); done(); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t.value).then(done, legacy);
    else legacy();
  };

  // a small, safe BBCode renderer for the post preview: everything is escaped first, only known tags become HTML
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const safeUrl = u => /^https?:\/\//i.test(u.trim()) ? u.trim() : "";
  const sizes = {1: ".75em", 2: ".875em", 3: "1em", 4: "1.2em", 5: "1.5em", 6: "1.9em", 7: "2.4em"};
  window.tbBBCode = src => {
    let h = esc(src);
    h = h.replace(/\[img\]([\s\S]*?)\[\/img\]/gi, (m, u) => safeUrl(u) ? `<img src="${safeUrl(u)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : m);
    h = h.replace(/\[code\]([\s\S]*?)\[\/code\]/gi, (m, c) => `<pre>${c.replace(/\[/g, "&#91;")}</pre>`);
    h = h.replace(/\[hr\]/gi, "<hr>");
    const rules = [
      [/\[b\]([\s\S]*?)\[\/b\]/gi, "<b>$1</b>"], [/\[i\]([\s\S]*?)\[\/i\]/gi, "<i>$1</i>"], [/\[u\]([\s\S]*?)\[\/u\]/gi, "<u>$1</u>"], [/\[s\]([\s\S]*?)\[\/s\]/gi, "<s>$1</s>"],
      [/\[center\]([\s\S]*?)\[\/center\]/gi, '<div class="bb-center">$1</div>'],
      [/\[quote(?:=[^\]]*)?\]([\s\S]*?)\[\/quote\]/gi, "<blockquote>$1</blockquote>"],
      [/\[size=(\d)\]([\s\S]*?)\[\/size\]/gi, (m, n, t) => `<span style="font-size:${sizes[n] || "1em"}">${t}</span>`],
      [/\[color=(#?[0-9a-z]+)\]([\s\S]*?)\[\/color\]/gi, '<span style="color:$1">$2</span>'],
      [/\[url=([^\]]+)\]([\s\S]*?)\[\/url\]/gi, (m, u, t) => safeUrl(u.replace(/&amp;/g, "&")) ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>` : t],
      [/\[url\]([\s\S]*?)\[\/url\]/gi, (m, u) => safeUrl(u.replace(/&amp;/g, "&")) ? `<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>` : u],
      [/\[spoiler(?:=([^\]]*))?\]([\s\S]*?)\[\/spoiler\]/gi, (m, t, c) => `<details class="bb-spoiler"><summary>${t || "Spoiler"}</summary><div>${c}</div></details>`],
      [/\[list\]([\s\S]*?)\[\/list\]/gi, (m, c) => "<ul>" + c.split(/\[\*\]/).filter(x => x.trim()).map(x => `<li>${x.trim()}</li>`).join("") + "</ul>"],
    ];
    for (let pass = 0; pass < 6; pass++) {  // nested tags: repeat until nothing changes
      const before = h;
      for (const [re, to] of rules) h = h.replace(re, to);
      if (h === before) break;
    }
    return h.replace(/\n/g, "<br>").replace(/(<\/(?:div|blockquote|pre|ul|li|details|summary)>|<hr>)<br>/g, "$1");
  };

  // "3 h ago" next to absolute dates
  const rel = ts => {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    if (s < 86400 * 7) { const d = Math.floor(s / 86400); return d === 1 ? "yesterday" : d + " days ago"; }
    return null;
  };
  const paintTimes = () => document.querySelectorAll("time[data-ts]").forEach(t => {
    const r = rel(+t.dataset.ts); if (!t.title) t.title = t.textContent;
    t.textContent = r || t.title;
  });

  document.addEventListener("DOMContentLoaded", () => {
    paintTimes(); setInterval(paintTimes, 60000);
    if (document.body.classList.contains("has-nav"))
      fetch("/busy").then(r => r.ok ? r.json() : null).then(d => { if (d && d.running) document.documentElement.dataset.busy = d.running; }).catch(() => {});
  });
})();
