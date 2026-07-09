"""
恩特小助手 — Web 页面 UI（纯前端 HTML/CSS/JS）

独立文件存放前端代码，main.py 通过 import HOME_HTML 引用。
修改前端样式或功能时只需编辑此文件，无需动 main.py。
"""

HOME_HTML = r"""<!DOCTYPE html>
<html data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>恩特小助手</title>
<style>
  :root {
    --bg: #f0f4f8;
    --card: #ffffff;
    --text: #1e293b;
    --text-secondary: #64748b;
    --primary: #2563eb;
    --primary-light: #eff6ff;
    --primary-dark: #1d4ed8;
    --accent: #10b981;
    --bubble-user: #2563eb;
    --bubble-user-text: #ffffff;
    --bubble-bot: #ffffff;
    --bubble-bot-text: #1e293b;
    --border: #e2e8f0;
    --shadow: 0 1px 3px rgba(0,0,0,0.08);
    --shadow-lg: 0 4px 20px rgba(0,0,0,0.08);
    --header-bg: #ffffff;
    --input-bg: #ffffff;
    --hover: #f1f5f9;
    --scrollbar: #cbd5e1;
    --success: #10b981;
  }
  [data-theme="dark"] {
    --bg: #0f172a;
    --card: #1e293b;
    --text: #e2e8f0;
    --text-secondary: #94a3b8;
    --primary: #3b82f6;
    --primary-light: #1e293b;
    --primary-dark: #2563eb;
    --bubble-user: #3b82f6;
    --bubble-user-text: #ffffff;
    --bubble-bot: #1e293b;
    --bubble-bot-text: #e2e8f0;
    --border: #334155;
    --shadow: 0 1px 3px rgba(0,0,0,0.3);
    --shadow-lg: 0 4px 20px rgba(0,0,0,0.4);
    --header-bg: #1e293b;
    --input-bg: #1e293b;
    --hover: #334155;
    --scrollbar: #475569;
    --success: #34d399;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    transition: background .3s, color .3s;
  }
  .app {
    width: 100%;
    max-width: 720px;
    height: 100vh;
    max-height: 800px;
    display: flex;
    flex-direction: column;
    background: var(--card);
    border-radius: 0;
    box-shadow: none;
    position: relative;
    overflow: hidden;
    transition: background .3s;
  }
  @media (min-width: 640px) {
    .app {
      height: 90vh;
      border-radius: 16px;
      box-shadow: var(--shadow-lg);
    }
  }

  /* ===== Header ===== */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    background: var(--header-bg);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    transition: background .3s;
  }
  .header h1 {
    font-size: 18px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .header h1 small {
    font-size: 12px;
    font-weight: 400;
    color: var(--text-secondary);
    margin-left: 4px;
  }
  .header-actions {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .header-actions button {
    background: none;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    padding: 6px;
    border-radius: 8px;
    font-size: 18px;
    line-height: 1;
    transition: all .2s;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
  }
  .header-actions button:hover {
    background: var(--hover);
    color: var(--text);
  }
  .header-actions button.active {
    color: var(--primary);
  }

  /* ===== Chat Area ===== */
  .chat-area {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    scroll-behavior: smooth;
  }
  .chat-area::-webkit-scrollbar {
    width: 6px;
  }
  .chat-area::-webkit-scrollbar-track {
    background: transparent;
  }
  .chat-area::-webkit-scrollbar-thumb {
    background: var(--scrollbar);
    border-radius: 3px;
  }
  .chat-area::-webkit-scrollbar-thumb:hover {
    background: var(--text-secondary);
  }

  /* ===== Welcome ===== */
  .welcome {
    text-align: center;
    padding: 30px 20px;
    color: var(--text-secondary);
  }
  .welcome .icon { font-size: 48px; margin-bottom: 12px; }
  .welcome h2 { font-size: 20px; color: var(--text); margin-bottom: 8px; }
  .welcome p { font-size: 14px; line-height: 1.7; max-width: 400px; margin: 0 auto; }
  .welcome .hints { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
  .welcome .hints span {
    display: inline-block;
    background: var(--primary-light);
    color: var(--primary);
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 13px;
    cursor: pointer;
    transition: all .2s;
    border: 1px solid transparent;
  }
  .welcome .hints span:hover {
    background: var(--primary);
    color: #fff;
    border-color: var(--primary);
  }

  /* ===== Bubbles ===== */
  .message {
    display: flex;
    flex-direction: column;
    max-width: 85%;
    animation: fadeIn .3s ease;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .message.user {
    align-self: flex-end;
    align-items: flex-end;
  }
  .message.bot {
    align-self: flex-start;
    align-items: flex-start;
  }
  .bubble {
    padding: 10px 16px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    transition: background .3s, color .3s;
  }
  .message.user .bubble {
    background: var(--bubble-user);
    color: var(--bubble-user-text);
    border-bottom-right-radius: 4px;
  }
  .message.bot .bubble {
    background: var(--bubble-bot);
    color: var(--bubble-bot-text);
    border-bottom-left-radius: 4px;
    box-shadow: var(--shadow);
  }
  .message.bot .bubble.loading {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .message.bot .bubble.loading .dot-pulse {
    display: flex;
    gap: 4px;
  }
  .message.bot .bubble.loading .dot-pulse span {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--text-secondary);
    animation: pulse 1.4s infinite ease-in-out;
  }
  .message.bot .bubble.loading .dot-pulse span:nth-child(2) { animation-delay: .16s; }
  .message.bot .bubble.loading .dot-pulse span:nth-child(3) { animation-delay: .32s; }
  @keyframes pulse {
    0%,80%,100% { opacity: .3; transform: scale(.8); }
    40% { opacity: 1; transform: scale(1); }
  }
  .time {
    font-size: 11px;
    color: var(--text-secondary);
    margin-top: 4px;
    padding: 0 4px;
    opacity: .7;
  }

  /* ===== Bottom Bar ===== */
  .bottom-bar {
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    background: var(--header-bg);
    flex-shrink: 0;
    transition: background .3s;
  }
  .user-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }
  .user-row label {
    font-size: 13px;
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .user-row input {
    flex: 1;
    padding: 6px 10px;
    font-size: 13px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--input-bg);
    color: var(--text);
    outline: none;
    transition: border-color .2s;
  }
  .user-row input:focus { border-color: var(--primary); }
  .user-row .save-btn {
    padding: 6px 14px;
    font-size: 12px;
    background: var(--primary);
    color: #fff;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    transition: background .2s;
    white-space: nowrap;
  }
  .user-row .save-btn:hover { background: var(--primary-dark); }
  .user-row .save-status {
    font-size: 12px;
    color: var(--success);
    opacity: 0;
    transition: opacity .3s;
  }
  .user-row .save-status.show { opacity: 1; }
  .input-row {
    display: flex;
    gap: 8px;
    align-items: flex-end;
  }
  .input-row textarea {
    flex: 1;
    padding: 10px 14px;
    font-size: 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--input-bg);
    color: var(--text);
    outline: none;
    resize: none;
    min-height: 42px;
    max-height: 120px;
    line-height: 1.5;
    font-family: inherit;
    transition: border-color .2s;
  }
  .input-row textarea:focus { border-color: var(--primary); }
  .input-row textarea::placeholder { color: var(--text-secondary); }
  .input-row .send-btn {
    padding: 10px 20px;
    font-size: 14px;
    background: var(--primary);
    color: #fff;
    border: none;
    border-radius: 12px;
    cursor: pointer;
    transition: all .2s;
    white-space: nowrap;
    font-weight: 500;
    height: 42px;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .input-row .send-btn:hover { background: var(--primary-dark); transform: translateY(-1px); }
  .input-row .send-btn:active { transform: translateY(0); }
  .input-row .send-btn:disabled {
    opacity: .5;
    cursor: not-allowed;
    transform: none;
  }
  .input-hint {
    font-size: 11px;
    color: var(--text-secondary);
    margin-top: 4px;
    padding-left: 2px;
  }

  /* ===== Confirm Dialog ===== */
  .dialog-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,.5);
    z-index: 9999;
    align-items: center;
    justify-content: center;
  }
  .dialog-overlay.show { display: flex; }
  .dialog-box {
    background: var(--card);
    border-radius: 16px;
    padding: 24px 28px;
    max-width: 320px;
    text-align: center;
    box-shadow: var(--shadow-lg);
  }
  .dialog-box h3 { margin-bottom: 8px; font-size: 16px; }
  .dialog-box p { font-size: 14px; color: var(--text-secondary); margin-bottom: 20px; }
  .dialog-actions { display: flex; gap: 10px; justify-content: center; }
  .dialog-actions button {
    padding: 8px 24px;
    border-radius: 10px;
    font-size: 14px;
    cursor: pointer;
    border: none;
    transition: all .2s;
  }
  .dialog-actions .confirm {
    background: #ef4444;
    color: #fff;
  }
  .dialog-actions .confirm:hover { background: #dc2626; }
  .dialog-actions .cancel {
    background: var(--hover);
    color: var(--text);
  }
  .dialog-actions .cancel:hover { background: var(--border); }
</style>
</head>
<body>

<div class="app" id="app">

  <!-- Header -->
  <div class="header">
    <h1>🔧 恩特小助手 <small>v2</small></h1>
    <div class="header-actions">
      <button id="themeBtn" onclick="toggleTheme()" title="切换暗黑模式">🌙</button>
      <button onclick="exportChat()" title="导出聊天记录">📤</button>
      <button onclick="showClearDialog()" title="清空对话">🗑</button>
    </div>
  </div>

  <!-- Chat Area -->
  <div class="chat-area" id="chatArea">
    <div class="welcome" id="welcome">
      <div class="icon">🤖</div>
      <h2>你好！我是恩特小助手</h2>
      <p>输入故障代码查询原因，或直接跟我聊天。<br>小助手会记住你的对话，刷新页面也不丢失。</p>
      <div class="hints">
        <span onclick="quickAsk('d4-1')">d4-1 急停告警</span>
        <span onclick="quickAsk('de-6')">de-6 故障代码</span>
        <span onclick="quickAsk('逆变器报错')">逆变器报错</span>
        <span onclick="quickAsk('今天天气怎么样')">闲聊一下</span>
      </div>
    </div>
  </div>

  <!-- Bottom Bar -->
  <div class="bottom-bar">
    <div class="user-row">
      <label>👤</label>
      <input type="text" id="username" placeholder="输入名字，小助手会记住你" maxlength="20">
      <button class="save-btn" onclick="saveName()">保存</button>
      <span class="save-status" id="saveStatus">✓ 已保存</span>
    </div>
    <div class="input-row">
      <textarea id="query" rows="1" placeholder="输入故障代码或直接提问…" oninput="autoResize(this)" onkeydown="onKeyDown(event)"></textarea>
      <button class="send-btn" id="sendBtn" onclick="send()">发送</button>
    </div>
    <div class="input-hint">按 Enter 发送 · Ctrl+Enter 换行</div>
  </div>

</div>

<!-- Clear dialog -->
<div class="dialog-overlay" id="clearDialog">
  <div class="dialog-box">
    <h3>🗑 清空对话</h3>
    <p>确定要清空所有聊天记录吗？<br>你的名字不会被删除。</p>
    <div class="dialog-actions">
      <button class="cancel" onclick="hideClearDialog()">取消</button>
      <button class="confirm" onclick="clearChat()">确认清空</button>
    </div>
  </div>
</div>

<script>
// ===== State =====
var _searching = false;
var THEME_KEY = 'entar_theme';
var MSGS_KEY = 'entar_messages';
var USER_KEY = 'entar_username';

// ===== Init =====
window.onload = function() {
  // Theme
  var savedTheme = localStorage.getItem(THEME_KEY) || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
  document.getElementById('themeBtn').textContent = savedTheme === 'dark' ? '☀️' : '🌙';

  // Username
  var savedUser = localStorage.getItem(USER_KEY) || '';
  document.getElementById('username').value = savedUser;

  // Messages
  loadMessages();
  scrollToBottom();
  focusInput();
};

// ===== Focus input =====
function focusInput() {
  setTimeout(function() { document.getElementById('query').focus(); }, 100);
}

// ===== Auto resize textarea =====
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ===== Key handler =====
function onKeyDown(e) {
  if (e.key === 'Enter' && !e.ctrlKey && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

// ===== User name =====
function saveName() {
  var name = document.getElementById('username').value.trim();
  if (!name) return;
  localStorage.setItem(USER_KEY, name);
  var status = document.getElementById('saveStatus');
  status.classList.add('show');
  setTimeout(function() { status.classList.remove('show'); }, 2000);
}

// ===== Quick ask =====
function quickAsk(text) {
  document.getElementById('query').value = text;
  send();
}

// ===== Send message =====
function send() {
  if (_searching) return;

  var q = document.getElementById('query').value.trim();
  if (!q) return;

  var user = document.getElementById('username').value.trim();
  if (!user) {
    document.getElementById('username').focus();
    document.getElementById('username').style.borderColor = '#ef4444';
    setTimeout(function() {
      document.getElementById('username').style.borderColor = '';
    }, 1500);
    return;
  }

  // Save user name
  localStorage.setItem(USER_KEY, user);

  // Clear input, reset height
  document.getElementById('query').value = '';
  autoResize(document.getElementById('query'));

  // Remove welcome
  var welcome = document.getElementById('welcome');
  if (welcome) welcome.style.display = 'none';

  // Add user message
  addMessage('user', q);

  // Set searching
  _searching = true;
  document.getElementById('sendBtn').disabled = true;

  // Add loading bubble
  var loadingId = addLoading();

  // Fetch
  var url = '/ask?q=' + encodeURIComponent(q) + '&user=' + encodeURIComponent(user);
  fetch(url)
    .then(function(res) { return res.json(); })
    .then(function(data) {
      removeLoading(loadingId);
      var answer = data.answer || '抱歉，我没有找到相关信息。';
      addMessage('bot', answer);
      saveMessages();
    })
    .catch(function(err) {
      removeLoading(loadingId);
      addMessage('bot', '抱歉，连接出错了：' + err.message);
      saveMessages();
    })
    .finally(function() {
      _searching = false;
      document.getElementById('sendBtn').disabled = false;
      focusInput();
    });
}

// ===== Add message =====
function addMessage(role, text) {
  var area = document.getElementById('chatArea');
  var now = new Date();
  var timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  var div = document.createElement('div');
  div.className = 'message ' + role;
  div.innerHTML = '<div class="bubble">' + escapeHtml(text) + '</div>'
    + '<span class="time">' + timeStr + '</span>';
  area.appendChild(div);
  scrollToBottom();
  return div;
}

// ===== Add loading indicator =====
function addLoading() {
  var area = document.getElementById('chatArea');
  var now = new Date();
  var timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  var id = 'loading-' + Date.now();
  var div = document.createElement('div');
  div.className = 'message bot';
  div.id = id;
  div.innerHTML = '<div class="bubble loading">'
    + '<div class="dot-pulse"><span></span><span></span><span></span></div>'
    + '<span style="font-size:13px;color:var(--text-secondary)">思考中…</span>'
    + '</div>'
    + '<span class="time">' + timeStr + '</span>';
  area.appendChild(div);
  scrollToBottom();
  return id;
}

// ===== Remove loading =====
function removeLoading(id) {
  var el = document.getElementById(id);
  if (el) el.remove();
}

// ===== Save / Load messages =====
function saveMessages() {
  var area = document.getElementById('chatArea');
  var msgs = [];
  var items = area.querySelectorAll('.message');
  items.forEach(function(el) {
    var role = el.classList.contains('user') ? 'user' : 'bot';
    var bubble = el.querySelector('.bubble');
    if (!bubble || bubble.classList.contains('loading')) return;
    var text = bubble.innerHTML;
    var timeEl = el.querySelector('.time');
    var time = timeEl ? timeEl.textContent : '';
    msgs.push({ role: role, text: text, time: time });
  });
  localStorage.setItem(MSGS_KEY, JSON.stringify(msgs));
}

function loadMessages() {
  var raw = localStorage.getItem(MSGS_KEY);
  if (!raw) return;
  try {
    var msgs = JSON.parse(raw);
    if (!msgs || !msgs.length) return;
    var welcome = document.getElementById('welcome');
    if (welcome) welcome.style.display = 'none';
    var area = document.getElementById('chatArea');
    msgs.forEach(function(m) {
      var div = document.createElement('div');
      div.className = 'message ' + m.role;
      div.innerHTML = '<div class="bubble">' + m.text + '</div>'
        + (m.time ? '<span class="time">' + m.time + '</span>' : '');
      area.appendChild(div);
    });
  } catch(e) { /* ignore bad data */ }
}

// ===== Scroll =====
function scrollToBottom() {
  var area = document.getElementById('chatArea');
  setTimeout(function() { area.scrollTop = area.scrollHeight; }, 50);
}

// ===== Theme toggle =====
function toggleTheme() {
  var html = document.documentElement;
  var current = html.getAttribute('data-theme');
  var next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem(THEME_KEY, next);
  document.getElementById('themeBtn').textContent = next === 'dark' ? '☀️' : '🌙';
}

// ===== Clear dialog =====
function showClearDialog() {
  document.getElementById('clearDialog').classList.add('show');
}
function hideClearDialog() {
  document.getElementById('clearDialog').classList.remove('show');
}

function clearChat() {
  hideClearDialog();
  var area = document.getElementById('chatArea');
  // Remove all messages except welcome
  var msgs = area.querySelectorAll('.message');
  msgs.forEach(function(el) { el.remove(); });
  localStorage.removeItem(MSGS_KEY);
  // Show welcome again
  document.getElementById('welcome').style.display = '';
}

// ===== Export =====
function exportChat() {
  var area = document.getElementById('chatArea');
  var items = area.querySelectorAll('.message');
  if (!items.length) {
    alert('暂无聊天记录可导出');
    return;
  }
  var lines = [];
  lines.push('恩特小助手 - 聊天记录');
  lines.push('导出时间：' + new Date().toLocaleString());
  lines.push('='.repeat(40));
  lines.push('');
  items.forEach(function(el) {
    var role = el.classList.contains('user') ? '👤 我' : '🤖 小助手';
    var bubble = el.querySelector('.bubble');
    if (!bubble || bubble.classList.contains('loading')) return;
    var text = bubble.textContent.trim();
    var timeEl = el.querySelector('.time');
    var time = timeEl ? timeEl.textContent : '';
    lines.push(role + (time ? ' (' + time + ')' : ''));
    lines.push(text);
    lines.push('');
  });
  var blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '恩特小助手对话_' + new Date().toISOString().slice(0,10) + '.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ===== Escape HTML =====
function escapeHtml(text) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(text));
  return div.innerHTML;
}
</script>

</body>
</html>
"""
