"""
恩特小助手 — Web 页面 UI（纯前端 HTML/CSS/JS）

独立文件存放前端代码，main.py 通过 import HOME_HTML 引用。
修改前端样式或功能时只需编辑此文件，无需动 main.py。

v1.5 UI 改版：
  - 品牌视觉统一（主蓝 #4361ee + 点缀色，参考 docs/恩特小助手介绍页.html）
  - 新增手写 Markdown 渲染器（列表/表格/代码块/粗体等）
  - 新增来源 chip（展示回答来自哪个知识库，读 /ask 返回的 source 字段）
  - 历史消息存储升级为 entar_messages_v2（存原始文本，渲染时再转义+Markdown）
"""

HOME_HTML = r"""<!DOCTYPE html>
<html data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>恩特小助手</title>
<style>
  :root {
    --brand: #4361ee;
    --brand-dark: #3a56d4;
    --brand-light: #eef1ff;
    --brand-gradient: linear-gradient(135deg, #4361ee, #5a7bff);
    --pink: #f72585;
    --green: #06d6a0;
    --green-light: #ecfdf5;
    --orange: #fb8500;
    --orange-light: #fff8f0;
    --purple: #7209b7;
    --purple-light: #f5f0ff;
    --bg: #f8fafc;
    --card: #ffffff;
    --text: #1a1a2e;
    --text-secondary: #64748b;
    --border: #eef2f6;
    --shadow: 0 2px 8px rgba(0,0,0,0.05);
    --shadow-lg: 0 8px 40px rgba(0,0,0,0.10);
    --header-bg: rgba(255,255,255,0.85);
    --input-bg: #ffffff;
    --hover: #f1f5f9;
    --scrollbar: #cbd5e1;
    --success: #10b981;
    --font: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  }
  [data-theme="dark"] {
    --brand: #6c8cff;
    --brand-dark: #5a7bff;
    --brand-light: #1e293b;
    --brand-gradient: linear-gradient(135deg, #6c8cff, #f72585);
    --green-light: #064e3b;
    --orange-light: #451a03;
    --purple-light: #2e1065;
    --bg: #0f172a;
    --card: #1e293b;
    --text: #e2e8f0;
    --text-secondary: #94a3b8;
    --border: #334155;
    --shadow: 0 2px 8px rgba(0,0,0,0.3);
    --shadow-lg: 0 8px 40px rgba(0,0,0,0.45);
    --header-bg: rgba(15,23,42,0.85);
    --input-bg: #1e293b;
    --hover: #334155;
    --scrollbar: #475569;
    --success: #34d399;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: var(--font);
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
    max-width: 840px;
    height: 100vh;
    max-height: 860px;
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
      border-radius: 20px;
      box-shadow: var(--shadow-lg);
    }
  }

  /* ===== Header ===== */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--header-bg);
    -webkit-backdrop-filter: blur(12px);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    z-index: 10;
    transition: background .3s;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand-logo {
    width: 34px; height: 34px;
    border-radius: 10px;
    background: var(--brand-gradient);
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    box-shadow: 0 4px 12px rgba(67,97,238,.25);
  }
  .brand h1 {
    font-size: 17px;
    font-weight: 800;
    letter-spacing: .3px;
    background: linear-gradient(135deg, var(--brand), var(--pink));
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .badge {
    font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 10px;
    background: var(--brand-light); color: var(--brand);
  }
  .header-actions { display: flex; gap: 4px; align-items: center; }
  .header-actions button {
    background: none;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    padding: 6px;
    border-radius: 10px;
    font-size: 17px;
    line-height: 1;
    transition: all .2s;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
  }
  .header-actions button:hover { background: var(--hover); color: var(--text); transform: translateY(-1px); }

  /* ===== Chat Area ===== */
  .chat-area {
    flex: 1;
    overflow-y: auto;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    scroll-behavior: smooth;
  }
  .chat-area::-webkit-scrollbar { width: 6px; }
  .chat-area::-webkit-scrollbar-track { background: transparent; }
  .chat-area::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 3px; }
  .chat-area::-webkit-scrollbar-thumb:hover { background: var(--text-secondary); }

  /* ===== Welcome ===== */
  .welcome { text-align: center; padding: 48px 24px; }
  .welcome-logo {
    width: 84px; height: 84px;
    margin: 0 auto 20px;
    background: var(--brand-gradient);
    border-radius: 24px;
    display: flex; align-items: center; justify-content: center;
    font-size: 42px;
    box-shadow: 0 12px 32px rgba(67,97,238,.25);
    animation: float 4s ease-in-out infinite;
  }
  @keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-6px); }
  }
  .welcome h2 {
    font-size: 22px;
    font-weight: 800;
    margin-bottom: 8px;
    letter-spacing: .5px;
  }
  .welcome p {
    font-size: 14px;
    line-height: 1.8;
    color: var(--text-secondary);
    max-width: 420px;
    margin: 0 auto;
  }
  .welcome .hints {
    margin-top: 24px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: center;
  }
  .hint {
    display: inline-block;
    padding: 8px 18px;
    border-radius: 22px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all .2s;
    border: 1px solid transparent;
  }
  .hint.blue   { background: var(--brand-light); color: var(--brand); }
  .hint.green  { background: var(--green-light); color: #057855; }
  .hint.purple { background: var(--purple-light); color: #5a0496; }
  .hint.orange { background: var(--orange-light); color: #b96300; }
  .hint:hover { transform: translateY(-2px); box-shadow: var(--shadow); }

  /* ===== Messages ===== */
  .message {
    display: flex;
    flex-direction: column;
    max-width: 88%;
    animation: fadeIn .3s ease;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .message.user { align-self: flex-end; align-items: flex-end; }
  .message.bot { align-self: flex-start; align-items: flex-start; }
  .bubble {
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.7;
    word-break: break-word;
    transition: background .3s, color .3s;
  }
  .message.user .bubble {
    background: var(--brand-gradient);
    color: #fff;
    border-bottom-right-radius: 4px;
    box-shadow: 0 4px 12px rgba(67,97,238,.2);
  }
  .message.bot .bubble {
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--border);
    border-bottom-left-radius: 4px;
    box-shadow: var(--shadow);
  }
  .message.bot .bubble.loading {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .dot-pulse { display: flex; gap: 4px; }
  .dot-pulse span {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--text-secondary);
    animation: pulse 1.4s infinite ease-in-out;
  }
  .dot-pulse span:nth-child(2) { animation-delay: .16s; }
  .dot-pulse span:nth-child(3) { animation-delay: .32s; }
  @keyframes pulse {
    0%, 80%, 100% { opacity: .3; transform: scale(.8); }
    40% { opacity: 1; transform: scale(1); }
  }
  .msg-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    padding: 0 4px;
  }
  .time {
    font-size: 11px;
    color: var(--text-secondary);
    opacity: .7;
  }
  .source-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
  }
  .source-chip.blue   { background: var(--brand-light); color: var(--brand); }
  .source-chip.green  { background: var(--green-light); color: #057855; }
  .source-chip.purple { background: var(--purple-light); color: #5a0496; }
  .source-chip.orange { background: var(--orange-light); color: #b96300; }
  .source-chip.gray   { background: var(--hover); color: var(--text-secondary); }

  /* ===== Markdown ===== */
  .md { line-height: 1.7; }
  .md h1, .md h2, .md h3 { margin: 12px 0 8px; line-height: 1.4; }
  .md h1 { font-size: 17px; }
  .md h2 { font-size: 16px; }
  .md h3 { font-size: 15px; }
  .md p { margin: 6px 0; }
  .md ul, .md ol { margin: 6px 0; padding-left: 22px; }
  .md li { margin: 3px 0; }
  .md pre {
    background: var(--hover);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    margin: 8px 0;
    overflow-x: auto;
    font-size: 12.5px;
    line-height: 1.6;
    font-family: "SF Mono", Consolas, "Courier New", monospace;
  }
  .md code {
    background: var(--hover);
    padding: 1px 6px;
    border-radius: 5px;
    font-size: 12.5px;
    font-family: "SF Mono", Consolas, "Courier New", monospace;
  }
  .md pre code { background: none; padding: 0; }
  .md table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
    font-size: 13px;
  }
  .md th, .md td {
    border: 1px solid var(--border);
    padding: 6px 10px;
    text-align: left;
  }
  .md th { background: var(--hover); font-weight: 600; }
  .md blockquote {
    border-left: 3px solid var(--brand);
    background: var(--brand-light);
    padding: 8px 12px;
    margin: 8px 0;
    border-radius: 0 8px 8px 0;
    color: var(--text);
  }
  .md a { color: var(--brand); text-decoration: none; }
  .md a:hover { text-decoration: underline; }

  /* ===== Bottom Bar ===== */
  .bottom-bar {
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    background: var(--header-bg);
    -webkit-backdrop-filter: blur(12px);
    backdrop-filter: blur(12px);
    flex-shrink: 0;
    transition: background .3s;
  }
  .user-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }
  .user-row label { font-size: 13px; color: var(--text-secondary); white-space: nowrap; }
  .user-row input {
    flex: 1;
    padding: 6px 12px;
    font-size: 13px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--input-bg);
    color: var(--text);
    outline: none;
    transition: border-color .2s;
  }
  .user-row input:focus { border-color: var(--brand); }
  .user-row .save-btn {
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 500;
    background: var(--brand);
    color: #fff;
    border: none;
    border-radius: 10px;
    cursor: pointer;
    transition: background .2s;
    white-space: nowrap;
  }
  .user-row .save-btn:hover { background: var(--brand-dark); }
  .user-row .save-status { font-size: 12px; color: var(--success); opacity: 0; transition: opacity .3s; }
  .user-row .save-status.show { opacity: 1; }
  .input-row {
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  .input-row textarea {
    flex: 1;
    padding: 12px 16px;
    font-size: 14px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--input-bg);
    color: var(--text);
    outline: none;
    resize: none;
    min-height: 46px;
    max-height: 130px;
    line-height: 1.5;
    font-family: inherit;
    transition: border-color .2s, box-shadow .2s;
  }
  .input-row textarea:focus {
    border-color: var(--brand);
    box-shadow: 0 0 0 3px rgba(67,97,238,.12);
  }
  .input-row textarea::placeholder { color: var(--text-secondary); }
  .input-row .send-btn {
    padding: 10px 22px;
    font-size: 14px;
    font-weight: 600;
    background: var(--brand-gradient);
    color: #fff;
    border: none;
    border-radius: 14px;
    cursor: pointer;
    transition: all .2s;
    white-space: nowrap;
    height: 46px;
    display: flex;
    align-items: center;
    gap: 6px;
    box-shadow: 0 4px 12px rgba(67,97,238,.25);
  }
  .input-row .send-btn:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(67,97,238,.3); }
  .input-row .send-btn:active { transform: translateY(0); }
  .input-row .send-btn:disabled { opacity: .5; cursor: not-allowed; transform: none; }
  .input-hint { font-size: 11px; color: var(--text-secondary); margin-top: 6px; padding-left: 2px; }

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
    border-radius: 20px;
    padding: 26px 30px;
    max-width: 340px;
    text-align: center;
    box-shadow: var(--shadow-lg);
    border: 1px solid var(--border);
  }
  .dialog-box h3 { margin-bottom: 8px; font-size: 16px; }
  .dialog-box p { font-size: 14px; color: var(--text-secondary); margin-bottom: 20px; }
  .dialog-actions { display: flex; gap: 10px; justify-content: center; }
  .dialog-actions button {
    padding: 8px 26px;
    border-radius: 10px;
    font-size: 14px;
    cursor: pointer;
    border: none;
    transition: all .2s;
  }
  .dialog-actions .confirm { background: #ef4444; color: #fff; }
  .dialog-actions .confirm:hover { background: #dc2626; }
  .dialog-actions .cancel { background: var(--hover); color: var(--text); }
  .dialog-actions .cancel:hover { background: var(--border); }
</style>
</head>
<body>

<div class="app" id="app">

  <!-- Header -->
  <header class="header">
    <div class="brand">
      <div class="brand-logo">🤖</div>
      <h1>恩特小助手</h1>
      <span class="badge">v1.5.1</span>
    </div>
    <div class="header-actions">
      <button onclick="location.href='/admin'" title="文档管理（上传/管理知识库）">📂</button>
      <button id="themeBtn" onclick="toggleTheme()" title="切换暗黑模式">🌙</button>
      <button onclick="exportChat()" title="导出聊天记录">📤</button>
      <button onclick="showClearDialog()" title="清空对话">🗑</button>
    </div>
  </header>

  <!-- Chat Area -->
  <div class="chat-area" id="chatArea">
    <div class="welcome" id="welcome">
      <div class="welcome-logo">🤖</div>
      <h2>你好！我是恩特小助手</h2>
      <p>输入故障代码秒查原因，或直接跟我聊天。<br>回答下方会标注数据来源，方便你核对。</p>
      <div class="hints">
        <span class="hint blue" onclick="quickAsk('d4-1')">d4-1 急停告警</span>
        <span class="hint green" onclick="quickAsk('逆变器报错')">逆变器报错</span>
        <span class="hint purple" onclick="quickAsk('3A 电流 1oz 走线宽度')">PCB 走线计算</span>
        <span class="hint orange" onclick="quickAsk('今天天气怎么样')">闲聊一下</span>
      </div>
    </div>
  </div>

  <!-- Bottom Bar -->
  <footer class="bottom-bar">
    <div class="user-row">
      <label>👤</label>
      <input type="text" id="username" placeholder="输入名字，小助手会记住你" maxlength="20">
      <button class="save-btn" onclick="saveName()">保存</button>
      <span class="save-status" id="saveStatus">✓ 已保存</span>
    </div>
    <div class="input-row">
      <textarea id="query" rows="1" placeholder="输入故障代码或直接提问…" oninput="autoResize(this)" onkeydown="onKeyDown(event)"></textarea>
      <button class="send-btn" id="sendBtn" onclick="send()"><span>发送</span><span>→</span></button>
    </div>
    <div class="input-hint">按 Enter 发送 · Ctrl+Enter 换行</div>
  </footer>

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
var MSGS_KEY = 'entar_messages_v2';   // v2：存原始文本 + source，渲染时统一转义
var MSGS_KEY_V1 = 'entar_messages';   // v1：存 innerHTML（已转义）
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

  // Messages（含 v1 → v2 迁移）
  migrateMessages();
  loadMessages();
  scrollToBottom();
  focusInput();
};

// ===== v1 → v2 数据迁移 =====
function migrateMessages() {
  var raw = localStorage.getItem(MSGS_KEY_V1);
  if (!raw) return;
  try {
    var v1 = JSON.parse(raw);
    if (Array.isArray(v1) && v1.length) {
      var v2 = v1.map(function(m) {
        return {
          role: m.role,
          text: unescapeHtml(m.text || ''),
          source: m.source || '',
          time: m.time || ''
        };
      });
      localStorage.setItem(MSGS_KEY, JSON.stringify(v2));
    }
  } catch(e) { /* 忽略坏数据 */ }
  localStorage.removeItem(MSGS_KEY_V1);
}

// ===== Escape / Unescape HTML =====
function escapeHtml(text) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(text));
  return div.innerHTML;
}
function unescapeHtml(s) {
  if (!s) return '';
  var div = document.createElement('div');
  div.innerHTML = s;
  return div.textContent;
}

// ===== Markdown 渲染（在已转义的文本上做结构化，保证 XSS 安全） =====
function renderInline(t) {
  // 先保护行内代码，避免其内容被后续粗体/链接规则二次处理
  var codes = [];
  t = t.replace(/`([^`]+)`/g, function(m, c) {
    codes.push(c);
    return '\x01' + (codes.length - 1) + '\x01';
  });
  // 粗体 **text**
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // 链接 [text](url) — 仅放行 http/https
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // 还原行内代码
  t = t.replace(/\x01(\d+)\x01/g, function(m, i) {
    return '<code>' + codes[parseInt(i, 10)] + '</code>';
  });
  return t;
}

function isTableSepLine(line) {
  return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.indexOf('-') >= 0;
}
function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '')
    .split('|').map(function(c) { return c.trim(); });
}

function renderMarkdown(text) {
  var lines = escapeHtml(text).split('\n');
  var html = '';
  var inCode = false, codeBuf = [];
  var listType = null, listItems = [];
  var tableBuf = [];
  var i;

  function flushList() {
    if (!listType) return;
    html += '<' + listType + '>';
    for (var k = 0; k < listItems.length; k++) html += '<li>' + listItems[k] + '</li>';
    html += '</' + listType + '>';
    listItems = []; listType = null;
  }
  function flushTable() {
    if (tableBuf.length < 1) { tableBuf = []; return; }
    var rows = tableBuf.filter(function(l) { return !isTableSepLine(l); });
    if (!rows.length) { tableBuf = []; return; }
    var header = splitRow(rows[0]);
    var thead = '<tr>' + header.map(function(c) { return '<th>' + renderInline(c) + '</th>'; }).join('') + '</tr>';
    var tbody = '';
    for (var r = 1; r < rows.length; r++) {
      tbody += '<tr>' + splitRow(rows[r]).map(function(c) { return '<td>' + renderInline(c) + '</td>'; }).join('') + '</tr>';
    }
    html += '<table><thead>' + thead + '</thead><tbody>' + tbody + '</tbody></table>';
    tableBuf = [];
  }

  for (i = 0; i < lines.length; i++) {
    var line = lines[i];

    // 代码块开关
    if (/^```/.test(line.trim())) {
      flushList(); flushTable();
      if (!inCode) { inCode = true; codeBuf = []; }
      else {
        inCode = false;
        html += '<pre><code>' + codeBuf.join('\n') + '</code></pre>';
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // 表格：以 | 开头的连续行
    if (line.trim().indexOf('|') === 0) {
      flushList();
      tableBuf.push(line);
      continue;
    } else {
      flushTable();
    }

    // 标题
    var h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) { flushList(); html += '<h' + h[1].length + '>' + renderInline(h[2]) + '</h' + h[1].length + '>'; continue; }

    // 无序列表
    var ul = line.match(/^[-*]\s+(.*)/);
    if (ul) {
      if (listType !== 'ul') { flushList(); listType = 'ul'; }
      listItems.push(renderInline(ul[1]));
      continue;
    }
    // 有序列表
    var ol = line.match(/^\d+[.、]\s+(.*)/);
    if (ol) {
      if (listType !== 'ol') { flushList(); listType = 'ol'; }
      listItems.push(renderInline(ol[1]));
      continue;
    }
    flushList();

    // 引用
    var q = line.match(/^>\s?(.*)/);
    if (q) { html += '<blockquote>' + renderInline(q[1]) + '</blockquote>'; continue; }

    // 空行
    if (!line.trim()) continue;

    // 普通段落
    html += '<p>' + renderInline(line) + '</p>';
  }
  flushList(); flushTable();
  if (inCode) html += '<pre><code>' + codeBuf.join('\n') + '</code></pre>';
  return html;
}

// ===== 来源 chip =====
var SOURCE_RULES = [
  { test: /^遥信（DI）表/, icon: '📊', cls: 'blue' },
  { test: /^语义搜索/,      icon: '🔍', cls: 'green' },
  { test: /^标准编号/,      icon: '📄', cls: 'purple' },
  { test: /^PCB计算/,       icon: '🧮', cls: 'orange' },
  { test: /^agent\(chat\)/, icon: '💬', cls: 'gray' },
  { test: /^agent\(RAG\)/,  icon: '🧠', cls: 'blue' },
  { test: /^agent/,         icon: '🤖', cls: 'gray' },
  { test: /^fallback/,      icon: '⚠️', cls: 'gray' },
];
function sourceChipHtml(source) {
  if (!source) return '';
  var meta = { icon: '📌', cls: 'gray' };
  for (var i = 0; i < SOURCE_RULES.length; i++) {
    if (SOURCE_RULES[i].test.test(source)) { meta = SOURCE_RULES[i]; break; }
  }
  return '<span class="source-chip ' + meta.cls + '" title="数据来源">' + meta.icon + ' ' + escapeHtml(source) + '</span>';
}

// ===== Focus input =====
function focusInput() {
  setTimeout(function() { document.getElementById('query').focus(); }, 100);
}

// ===== Auto resize textarea =====
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 130) + 'px';
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
  addMessage('user', q, '');

  // Set searching
  _searching = true;
  document.getElementById('sendBtn').disabled = true;

  // Add loading bubble
  var loadingId = addLoading();

  // Fetch（POST，避免问题内容进 URL/浏览器历史）
  var body = new URLSearchParams();
  body.append('q', q);
  body.append('user', user);
  fetch('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body
  })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      removeLoading(loadingId);
      var answer = data.answer || '抱歉，我没有找到相关信息。';
      addMessage('bot', answer, data.source || '');
      saveMessages();
    })
    .catch(function(err) {
      removeLoading(loadingId);
      addMessage('bot', '抱歉，连接出错了：' + err.message, '');
      saveMessages();
    })
    .finally(function() {
      _searching = false;
      document.getElementById('sendBtn').disabled = false;
      focusInput();
    });
}

// ===== Add message =====
function addMessage(role, text, source) {
  var area = document.getElementById('chatArea');
  var now = new Date();
  var timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');

  var div = document.createElement('div');
  div.className = 'message ' + role;
  var contentHtml = role === 'bot'
    ? '<div class="bubble md">' + renderMarkdown(text) + '</div>'
    : '<div class="bubble">' + escapeHtml(text) + '</div>';
  div.innerHTML = contentHtml
    + '<div class="msg-meta">' + sourceChipHtml(source) + '<span class="time">' + timeStr + '</span></div>';
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

// ===== Save / Load messages（v2：存原始文本，渲染时统一处理） =====
function saveMessages() {
  var area = document.getElementById('chatArea');
  var msgs = [];
  var items = area.querySelectorAll('.message');
  items.forEach(function(el) {
    var role = el.classList.contains('user') ? 'user' : 'bot';
    var bubble = el.querySelector('.bubble');
    if (!bubble || bubble.classList.contains('loading')) return;
    var chip = el.querySelector('.source-chip');
    var timeEl = el.querySelector('.time');
    msgs.push({
      role: role,
      text: bubble.textContent.trim(),
      source: chip ? chip.textContent.replace(/^\S+\s+/, '') : '',
      time: timeEl ? timeEl.textContent : ''
    });
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
      var contentHtml = m.role === 'bot'
        ? '<div class="bubble md">' + renderMarkdown(m.text || '') + '</div>'
        : '<div class="bubble">' + escapeHtml(m.text || '') + '</div>';
      div.innerHTML = contentHtml
        + '<div class="msg-meta">' + sourceChipHtml(m.source || '')
        + (m.time ? '<span class="time">' + m.time + '</span>' : '') + '</div>';
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
  var msgs = area.querySelectorAll('.message');
  msgs.forEach(function(el) { el.remove(); });
  localStorage.removeItem(MSGS_KEY);
  localStorage.removeItem(MSGS_KEY_V1);
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
    var chip = el.querySelector('.source-chip');
    var timeEl = el.querySelector('.time');
    var time = timeEl ? timeEl.textContent : '';
    lines.push(role + (time ? ' (' + time + ')' : ''));
    lines.push(text);
    if (chip) lines.push('来源: ' + chip.textContent.replace(/^\S+\s+/, ''));
    lines.push('');
  });
  var blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '恩特小助手对话_' + new Date().toISOString().slice(0,10) + '.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}
</script>

</body>
</html>
"""
