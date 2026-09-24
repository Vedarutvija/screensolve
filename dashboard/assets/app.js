const feed = document.getElementById("feed");
const lb = document.getElementById("lb");
const lbimg = document.getElementById("lbimg");
let known = new Set();
let first = true;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso + "Z").toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function badge(status) {
  const labels = { pending: "Pending", analyzing: "Analyzing…", solved: "Solved", no_question: "No question detected", error: "Error" };
  return `<span class="status ${status}">${status === "analyzing" || status === "pending" ? '<span class="spinner"></span>' : ""}${labels[status] || esc(status)}</span>`;
}

function stepsHTML(steps) {
  // additive pattern: each step is {step, code} where code accumulates
  let out = "", codeBuf = "";
  for (const s of steps || []) {
    let text, codeHtml = "";
    if (typeof s === "object" && s !== null) {
      text = s.step || "";
      if (s.code) codeBuf = s.code;
      codeHtml = `<pre><code class="language-python">${esc(codeBuf)}</code></pre>`;
    } else {
      text = String(s);
    }
    out += `<li>${esc(text)}${codeHtml}</li>`;
  }
  return out;
}

function cardHTML(c) {
  let body = "";
  if (c.status === "solved") {
    body = `
      <div class="card-body">
        <div class="badges">
          ${c.time_complexity ? `<span class="badge">⏱ ${esc(c.time_complexity)}</span>` : ""}
          ${c.space_complexity ? `<span class="badge space">💾 ${esc(c.space_complexity)}</span>` : ""}
        </div>
        ${c.solution_steps?.length ? `<div class="section"><h3>Step-by-step build-up</h3><ol class="steps">${stepsHTML(c.solution_steps)}</ol></div>` : ""}
        ${c.optimized_code ? `<div class="section"><h3>Optimized solution</h3><pre><code class="language-python">${esc(c.optimized_code)}</code></pre></div>` : ""}
        ${c.user_attempt ? `<div class="section"><h3>Your attempt on screen</h3><div class="attempt">${esc(c.user_attempt)}</div></div>` : ""}
        ${c.notes ? `<div class="section"><h3>Feedback</h3><div class="notes">${esc(c.notes)}</div></div>` : ""}
      </div>`;
  } else if (c.status === "no_question") {
    body = `<div class="card-body"><div class="section"><div class="notes" style="border-color:var(--line);background:transparent;color:var(--dim)">No coding question or solution attempt was detected on this screenshot.</div></div></div>`;
  } else if (c.status === "error") {
    body = `<div class="card-body"><div class="errbox">⚠️ Analysis failed: ${esc(c.error_message || "unknown error")}</div></div>`;
  } else {
    body = `<div class="card-body"><div class="errbox" style="color:var(--warn)">${badge(c.status)} Reading the screen with Gemini…</div></div>`;
  }
  return `<article class="card" id="cap-${c.id}">
    <div class="card-top">
      <img class="thumb" src="${c.image_url}" alt="screenshot" loading="lazy"
           onerror="this.style.visibility='hidden'">
      <div class="meta">
        ${badge(c.status)}
        <div class="time">${fmtTime(c.created_at)}</div>
      </div>
    </div>
    ${c.problem_statement ? `<div class="problem"><b>Problem:</b> ${esc(c.problem_statement)}</div>` : ""}
    ${body}
  </article>`;
}

async function refresh() {
  try {
    const res = await fetch("/api/captures?limit=50");
    if (!res.ok) throw new Error(res.status);
    const items = await res.json();
    if (!items.length) {
      feed.innerHTML = `<div class="empty"><b>No captures yet.</b><br>Press the hotkey (Ctrl+Alt+S) on your computer to capture the screen — solutions will appear here.</div>`;
      return;
    }
    if (first) {
      feed.innerHTML = items.map(cardHTML).join("");
      items.forEach(i => known.add(i.id));
      first = false;
    } else {
      const fresh = items.filter(i => !known.has(i.id));
      fresh.forEach(i => {
        known.add(i.id);
        feed.insertAdjacentHTML("afterbegin", cardHTML(i));
      });
      // refresh any row that changed status
      items.forEach(i => {
        const el = document.getElementById("cap-" + i.id);
        if (el && el.dataset.st && el.dataset.st !== i.status) {
          el.outerHTML = cardHTML(i);
        } else if (el) el.dataset.st = i.status;
      });
      if (fresh.length) hljs.highlightAll();
    }
    items.forEach(i => {
      const el = document.getElementById("cap-" + i.id);
      if (el) el.dataset.st = i.status;
    });
    hljs.highlightAll();
  } catch (e) {
    if (first) feed.innerHTML = `<div class="empty">⚠️ Cannot reach the server. Retrying…</div>`;
  }
}

document.addEventListener("click", e => {
  if (e.target.classList.contains("thumb")) {
    lbimg.src = e.target.src;
    lb.style.display = "flex";
  } else if (e.target.closest("#lb")) {
    lb.style.display = "none";
  }
});

refresh();
setInterval(refresh, 4000);
