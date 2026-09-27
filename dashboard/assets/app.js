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

function speakableText(c) {
  // plain-text version of a solution for speech: problem + step explanations.
  // Code blocks are NOT read verbatim — only mentioned.
  const parts = [];
  if (c.problem_statement) parts.push("Problem: " + c.problem_statement);
  for (const s of c.solution_steps || []) {
    if (typeof s === "object" && s !== null) {
      if (s.step) parts.push(s.step);
    } else {
      parts.push(String(s));
    }
  }
  if (c.notes) parts.push("Feedback: " + c.notes);
  return parts.join(" ");
}

function toggleSpeak(cardEl, c) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  if (synth.speaking) {
    synth.cancel();
    return;
  }
  const u = new SpeechSynthesisUtterance(speakableText(c));
  u.rate = 1.0;
  synth.speak(u);
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
  const canSpeak = "speechSynthesis" in window;
  const speakBtn = (c.status === "solved" && canSpeak)
    ? `<button class="speak-btn" data-cap="${c.id}" title="Read aloud" aria-label="Read aloud">🔊</button>`
    : "";
  return `<article class="card" id="cap-${c.id}" data-st="${c.status}">
    <div class="card-top">
      <img class="thumb" src="${c.image_url}" alt="screenshot" loading="lazy"
           onerror="this.style.visibility='hidden'">
      <div class="meta">
        ${badge(c.status)}
        <div class="time">${fmtTime(c.created_at)}</div>
      </div>
      ${speakBtn}
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
  } else if (e.target.classList.contains("speak-btn")) {
    const cardEl = e.target.closest(".card");
    const id = Number(e.target.dataset.cap);
    fetch("/api/captures?limit=50")
      .then(r => r.json())
      .then(items => {
        const c = (items || []).find(x => x.id === id);
        if (c) toggleSpeak(cardEl, c);
      });
  }
});

/* ---------- Chat panel (same pipeline as the Telegram bot) ---------- */

const chatlog = document.getElementById("chatlog");
const chatin = document.getElementById("chatin");
const chatsend = document.getElementById("chatsend");
const micbtn = document.getElementById("micbtn");

function addMsg(cls, html) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.innerHTML = html;
  chatlog.appendChild(d);
  chatlog.scrollTop = chatlog.scrollHeight;
  return d;
}

// Render a bot answer. All server answers that contain markup are
// Telegram-flavoured HTML with content already escaped server-side — insert
// directly. Pure plain-text answers get light formatting only.
function renderAnswer(text) {
  if (/<(\/?)(b|i|u|code|pre)\b/i.test(text)) {
    // replace bare <pre>...</pre> with our styled blocks (no hljs class)
    return text.replace(/<pre><code>/g, '<pre>').replace(/<\/code><\/pre>/g, '</pre>');
  }
  let out = esc(text);
  out = out.replace(/&lt;(\/?)(b|i|code|u)&gt;/g, "<$1$2>");
  out = out.replace(/^(🔹|🧪|📋|✅|⚠️|🎙)/gm, "<b>$1</b>");
  return out;
}

async function ask(text) {
  addMsg("user", esc(text));
  const t = addMsg("bot typing", "Thinking…");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "request failed");
    t.classList.remove("typing");
    t.innerHTML = renderAnswer(j.answer || "(empty answer)");
    chatlog.scrollTop = chatlog.scrollHeight;
    if (window.hljs) hljs.highlightAll();
  } catch (e) {
    t.classList.remove("typing");
    t.innerHTML = "⚠️ " + esc(String(e.message || e));
  }
}

async function askVoice(blob) {
  const t = addMsg("bot typing", "🎙 Transcribing…");
  try {
    const fd = new FormData();
    fd.append("audio", blob, "voice.webm");
    const r = await fetch("/api/chat/voice", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "request failed");
    addMsg("user", "🎙 “" + esc(j.transcript) + "”");
    t.classList.remove("typing");
    t.innerHTML = renderAnswer(j.answer || "(empty answer)");
    chatlog.scrollTop = chatlog.scrollHeight;
    if (window.hljs) hljs.highlightAll();
  } catch (e) {
    t.classList.remove("typing");
    t.innerHTML = "⚠️ " + esc(String(e.message || e));
  }
}

chatsend.addEventListener("click", () => {
  const v = chatin.value.trim();
  if (!v || chatsend.disabled) return;
  chatin.value = "";
  ask(v);
});
chatin.addEventListener("keydown", e => {
  if (e.key === "Enter") chatsend.click();
});

/* mic: MediaRecorder → POST /api/chat/voice */
let mediaRec = null, recChunks = [], recStream = null;
micbtn.addEventListener("click", async () => {
  if (mediaRec && mediaRec.state === "recording") {
    mediaRec.stop();
    return;
  }
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    addMsg("bot", "⚠️ Microphone unavailable: " + esc(String(e.message || e)));
    return;
  }
  recChunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
  mediaRec = new MediaRecorder(recStream, mime ? { mimeType: mime } : undefined);
  mediaRec.ondataavailable = ev => { if (ev.data.size) recChunks.push(ev.data); };
  mediaRec.onstop = () => {
    recStream.getTracks().forEach(t => t.stop());
    micbtn.classList.remove("rec");
    micbtn.title = "Record voice";
    chatsend.disabled = false;
    if (recChunks.length) askVoice(new Blob(recChunks, { type: mime || "audio/webm" }));
  };
  mediaRec.start();
  micbtn.classList.add("rec");
  micbtn.title = "Recording — click to stop & send";
  chatsend.disabled = true;
});

/* New Question ("Q"): end current question context, start fresh */
const newqbtn = document.getElementById("newqbtn");
if (newqbtn) {
  newqbtn.addEventListener("click", async () => {
    newqbtn.disabled = true;
    addMsg("user", "Q");
    const t = addMsg("bot typing", "Clearing question context…");
    try {
      const r = await fetch("/api/session/new", { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "request failed");
      t.classList.remove("typing");
      t.innerHTML = "✅ Question ended — context cleared. Your next capture/question starts fresh.";
    } catch (e) {
      t.classList.remove("typing");
      t.innerHTML = "⚠️ " + esc(String(e.message || e));
    } finally {
      newqbtn.disabled = false;
    }
  });
}

refresh();
setInterval(refresh, 4000);
