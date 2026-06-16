const SESSION = 'web';
const $ = (id) => document.getElementById(id);
const messagesEl = $('messages');

let replyTarget = null;       // { msgId, snippet }
let prevSlots = {};
let currentMode = 'hybrid';   // 'hybrid' | 'llm_full'

// ── send a message ────────────────────────────────────────────────────────────
async function send(text, replyToMsgId, displayText) {
  if (!text.trim()) return;
  addUserBubble(displayText ?? text, replyToMsgId);
  clearReply();
  $('input').value = '';
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION, message: text, reply_to_msg_id: replyToMsgId ?? null, mode: currentMode }),
    });
    const data = await res.json();
    addBotBubble(data, currentMode);
    renderSlots(data.slots);
  } catch (e) {
    addBotBubble({ msg_id: null, reply: '(lỗi kết nối tới server)' });
  }
}

// ── render bubbles ────────────────────────────────────────────────────────────
function quoteSnippet(text) {
  const t = (text || '').replace(/\n/g, ' ');
  return t.length > 60 ? t.slice(0, 60) + '…' : t;
}

function addUserBubble(text, replyToMsgId) {
  const row = document.createElement('div');
  row.className = 'row user';
  if (replyToMsgId) {
    const q = quotedTextFor(replyToMsgId);
    if (q) row.innerHTML += `<div class="quote">↩ ${escapeHtml(q)}</div>`;
  }
  row.innerHTML += `<div class="bubble">${escapeHtml(text)}</div>`;
  messagesEl.appendChild(row);
  scroll();
}

function addBotBubble(data, mode) {
  const row = document.createElement('div');
  row.className = 'row bot' + (mode === 'llm_full' ? ' llm' : '');
  if (data.msg_id) row.dataset.msgId = data.msg_id;

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = data.reply;
  row.appendChild(bubble);

  // clickable candidate chips for proposals/suggestions
  if (data.proposal && data.proposal.candidates) {
    const wrap = document.createElement('div');
    wrap.className = 'cands';
    data.proposal.candidates.forEach((c, i) => {
      const chip = document.createElement('div');
      chip.className = 'cand';
      chip.innerHTML = `<span>${i + 1}) ${escapeHtml(c.name)}</span>` +
        `<span class="price">${formatMoney(c.price)}</span>`;
      chip.onclick = () => send(`số ${i + 1}`, data.proposal.msg_id);
      wrap.appendChild(chip);
    });
    row.appendChild(wrap);
  }

  // reply (quote) button — Messenger-style
  if (data.msg_id) {
    const rbtn = document.createElement('button');
    rbtn.className = 'reply-btn';
    rbtn.textContent = '↩ Trả lời';
    rbtn.onclick = () => setReply(data.msg_id, quoteSnippet(data.reply));
    row.appendChild(rbtn);
  }

  messagesEl.appendChild(row);
  scroll();
}

function quotedTextFor(msgId) {
  const el = messagesEl.querySelector(`.row.bot[data-msg-id="${msgId}"] .bubble`);
  return el ? quoteSnippet(el.textContent) : null;
}

// ── quoted reply bar ──────────────────────────────────────────────────────────
function setReply(msgId, snippet) {
  replyTarget = { msgId, snippet };
  $('reply-bar-snippet').textContent = snippet;
  $('reply-bar').classList.remove('hidden');
  $('input').focus();
}
function clearReply() {
  replyTarget = null;
  $('reply-bar').classList.add('hidden');
}

// ── slot panel ────────────────────────────────────────────────────────────────
function renderSlots(slots) {
  if (!slots) return;
  const cust = slots.customer || {};
  const order = slots.order || {};
  const flat = {};
  Object.entries(cust).forEach(([k, v]) => { flat['c.' + k] = v; });
  Object.entries(order).forEach(([k, v]) => {
    if (v === null || (Array.isArray(v) && v.length === 0)) return;
    flat['o.' + k] = Array.isArray(v) ? v.join(', ') : v;
  });

  $('slot-customer').innerHTML = renderKV(cust, 'c.', flat);
  $('slot-order').innerHTML = renderKV(orderView(order), 'o.', flat);
  $('slot-cart').innerHTML = (slots.cart && slots.cart.length)
    ? slots.cart.map((n) => `<div class="item">${escapeHtml(n)}</div>`).join('')
    : '<div class="empty">trống</div>';
  $('slot-stage').textContent = slots.stage || '—';
  $('slot-payment').textContent = slots.payment || '—';

  prevSlots = flat;
}

function orderView(order) {
  const out = {};
  for (const [k, v] of Object.entries(order)) {
    if (v === null || (Array.isArray(v) && v.length === 0)) continue;
    out[k] = (k.includes('BUDGET')) ? formatMoney(v) : (Array.isArray(v) ? v.join(', ') : v);
  }
  return out;
}

function renderKV(obj, prefix, flat) {
  const entries = Object.entries(obj);
  if (!entries.length) return '<div class="empty">chưa có</div>';
  return entries.map(([k, v]) => {
    const key = prefix + k;
    const changed = flat[key] !== undefined && flat[key] !== prevSlots[key];
    return `<div class="field ${changed ? 'flash' : ''}"><span class="k">${k}</span>` +
      `<span class="v">${escapeHtml(String(v))}</span></div>`;
  }).join('');
}

// ── helpers ───────────────────────────────────────────────────────────────────
function formatMoney(v) {
  if (v === null || v === undefined || isNaN(v)) return v ?? '';
  return Number(v).toLocaleString('vi-VN') + 'đ';
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function scroll() { messagesEl.scrollTop = messagesEl.scrollHeight; }

// ── wire up ───────────────────────────────────────────────────────────────────
$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  send($('input').value, replyTarget ? replyTarget.msgId : null);
});
$('reply-cancel').addEventListener('click', clearReply);
$('reset-btn').addEventListener('click', async () => {
  await fetch('/reset?session_id=' + SESSION, { method: 'POST' });
  messagesEl.innerHTML = '';
  prevSlots = {};
  renderSlots({ customer: {}, order: {}, cart: [], stage: null, payment: null });
  welcome();
});

// ── mode toggle ───────────────────────────────────────────────────────────────
document.querySelectorAll('.mode-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    currentMode = btn.dataset.mode;
    document.querySelectorAll('.mode-btn').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const label = currentMode === 'llm_full' ? '🤖 Full LLM' : '⚙️ Hybrid';
    addBotBubble({ msg_id: null, reply: `[Đã chuyển sang chế độ ${label}]` }, currentMode);
  });
});

function welcome() {
  addBotBubble({ msg_id: null, reply: 'Dạ shop nghe ạ, bạn cần tư vấn mẫu LEGO nào ạ? 🧱' });
}
welcome();
renderSlots({ customer: {}, order: {}, cart: [], stage: null, payment: null });
