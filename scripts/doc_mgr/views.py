"""
恩特小助手 — 文档管理页面 HTML

独立文件存放管理页面前端代码，router.py 通过 import ADMIN_HTML 引用。
与 web_page.py 的 HOME_HTML 同模式：内嵌 HTML/CSS/JS，无需模板引擎。
"""

ADMIN_HTML = r"""<!DOCTYPE html>
<html data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>恩特小助手 - 文档管理</title>
<style>
  :root {
    --brand: #4361ee; --pink: #f72585;
    --bg: #f8fafc; --card: #ffffff; --text: #1a1a2e;
    --text-secondary: #64748b; --primary: #4361ee;
    --primary-light: #eef1ff; --primary-dark: #3a56d4;
    --border: #eef2f6; --shadow: 0 2px 8px rgba(0,0,0,0.05);
    --shadow-lg: 0 8px 40px rgba(0,0,0,0.10);
    --success: #06d6a0; --warning: #fb8500; --danger: #ef4444;
    --hover: #f1f5f9; --scrollbar: #cbd5e1;
    --radius: 14px;
  }
  [data-theme="dark"] {
    --brand: #6c8cff;
    --bg: #0f172a; --card: #1e293b; --text: #e2e8f0;
    --text-secondary: #94a3b8; --primary: #6c8cff;
    --primary-light: #1e293b; --primary-dark: #5a7bff;
    --border: #334155; --shadow: 0 2px 8px rgba(0,0,0,0.3);
    --shadow-lg: 0 8px 40px rgba(0,0,0,0.45);
    --success: #34d399; --warning: #fbbf24; --danger: #f87171;
    --hover: #334155; --scrollbar: #475569;
    --radius: 14px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh;
    transition: background .3s, color .3s;
  }
  .layout { display: flex; min-height: 100vh; }

  /* ===== Sidebar ===== */
  .sidebar {
    width: 232px; flex-shrink: 0;
    background: var(--card);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column;
    padding: 20px 14px;
    position: sticky; top: 0; height: 100vh;
    transition: background .3s;
  }
  .side-brand {
    display: flex; align-items: center; gap: 10px;
    padding: 4px 10px 20px; border-bottom: 1px solid var(--border);
    margin-bottom: 16px;
  }
  .side-logo {
    width: 38px; height: 38px; border-radius: 11px;
    background: linear-gradient(135deg, #4361ee, #f72585);
    display: flex; align-items: center; justify-content: center;
    font-size: 19px;
    box-shadow: 0 4px 12px rgba(67,97,238,.25);
  }
  .side-title { font-size: 15px; font-weight: 800; letter-spacing: .3px; }
  .side-sub { font-size: 11px; color: var(--text-secondary); margin-top: 1px; }
  .side-nav { flex: 1; display: flex; flex-direction: column; gap: 4px; }
  .side-nav .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px; border: none; background: transparent;
    color: var(--text-secondary); font-size: 14px; font-weight: 500;
    cursor: pointer; border-radius: 10px; text-align: left;
    transition: all .2s; font-family: inherit;
  }
  .side-nav .nav-item:hover { background: var(--hover); color: var(--text); }
  .side-nav .nav-item.active { background: var(--primary-light); color: var(--primary); font-weight: 600; }
  .side-nav .nav-icon { font-size: 16px; width: 22px; text-align: center; }
  .side-footer {
    display: flex; gap: 8px; padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .side-footer button {
    flex: 1; padding: 9px 0; border: none; border-radius: 10px;
    background: var(--hover); color: var(--text-secondary);
    cursor: pointer; font-size: 13px; transition: all .2s;
  }
  .side-footer button:hover { background: var(--border); color: var(--text); }
  .side-footer .side-back { background: var(--primary); color: #fff; }
  .side-footer .side-back:hover { background: var(--primary-dark); color: #fff; }

  /* ===== Main ===== */
  .main { flex: 1; padding: 24px 28px; min-width: 0; }
  .page-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px;
  }
  .page-header h1 { font-size: 20px; font-weight: 800; letter-spacing: .3px; }
  .menu-toggle { display: none; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .card {
    background: var(--card); border-radius: 12px; padding: 20px;
    box-shadow: var(--shadow); margin-bottom: 16px;
  }
  .card h3 { font-size: 15px; margin-bottom: 12px; color: var(--text); }
  .card .empty-state {
    text-align: center; padding: 30px 20px;
    color: var(--text-secondary); font-size: 14px;
  }

  /* Upload Drop Zone */
  .drop-zone {
    border: 2px dashed var(--border); border-radius: 12px;
    padding: 40px 20px; text-align: center;
    cursor: pointer; transition: all .3s;
    background: var(--bg);
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: var(--primary); background: var(--primary-light);
  }
  .drop-zone .icon { font-size: 36px; margin-bottom: 8px; }
  .drop-zone p { color: var(--text-secondary); font-size: 14px; }
  .drop-zone p strong { color: var(--text); }
  .upload-progress { margin-top: 16px; display: none; }
  .upload-progress.show { display: block; }
  .progress-bar {
    height: 6px; background: var(--border); border-radius: 3px;
    overflow: hidden; margin-bottom: 8px;
  }
  .progress-bar .fill {
    height: 100%; background: var(--primary);
    border-radius: 3px; width: 0; transition: width .3s;
  }
  .progress-bar .fill.done { background: var(--success); }
  .upload-result {
    margin-top: 12px; padding: 12px 16px;
    border-radius: 8px; font-size: 13px; display: none;
  }
  .upload-result.show { display: block; }
  .upload-result.success { background: #ecfdf5; color: #065f46; }
  [data-theme="dark"] .upload-result.success { background: #064e3b; color: #a7f3d0; }
  .upload-result.error { background: #fef2f2; color: #991b1b; }
  [data-theme="dark"] .upload-result.error { background: #7f1d1d; color: #fecaca; }

  /* Doc List */
  .collection-group { margin-bottom: 20px; }
  .collection-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 0; border-bottom: 2px solid var(--border);
    margin-bottom: 8px;
  }
  .collection-header h3 { font-size: 15px; margin: 0; }
  .collection-header .badge {
    background: var(--primary); color: #fff;
    padding: 2px 10px; border-radius: 12px; font-size: 12px;
  }
  .doc-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; border-radius: 8px;
    transition: background .15s; margin-bottom: 4px;
  }
  .doc-item:hover { background: var(--hover); }
  .doc-item .doc-info { flex: 1; }
  .doc-item .doc-name {
    font-size: 14px; font-weight: 500;
    display: flex; align-items: center; gap: 8px;
  }
  .doc-item .doc-meta {
    font-size: 12px; color: var(--text-secondary); margin-top: 2px;
  }
  .doc-item .doc-meta span { margin-right: 12px; }
  .doc-item .doc-actions button {
    padding: 6px 14px; border: none; border-radius: 6px;
    font-size: 12px; cursor: pointer; transition: all .2s;
  }
  .doc-item .doc-actions .del-btn {
    background: #fef2f2; color: #ef4444;
  }
  .doc-item .doc-actions .del-btn:hover { background: #fee2e2; }
  [data-theme="dark"] .doc-item .doc-actions .del-btn {
    background: #450a0a; color: #fca5a5;
  }
  [data-theme="dark"] .doc-item .doc-actions .del-btn:hover { background: #7f1d1d; }

  /* Search */
  .search-bar {
    display: flex; gap: 8px; margin-bottom: 16px;
  }
  .search-bar select, .search-bar input {
    padding: 8px 12px; border: 1px solid var(--border);
    border-radius: 8px; font-size: 14px;
    background: var(--card); color: var(--text);
    outline: none; transition: border-color .2s;
  }
  .search-bar select { min-width: 140px; }
  .search-bar input { flex: 1; }
  .search-bar select:focus, .search-bar input:focus { border-color: var(--primary); }
  .search-bar button {
    padding: 8px 20px; border: none; border-radius: 8px;
    background: var(--primary); color: #fff; font-size: 14px;
    cursor: pointer; transition: all .2s;
  }
  .search-bar button:hover { background: var(--primary-dark); }
  .search-bar button:disabled { opacity: .5; }
  .search-result-item {
    padding: 12px; border: 1px solid var(--border);
    border-radius: 8px; margin-bottom: 8px;
    transition: background .15s;
  }
  .search-result-item:hover { background: var(--hover); }
  .search-result-item .score {
    display: inline-block; padding: 2px 8px;
    border-radius: 4px; font-size: 11px; font-weight: 600;
    margin-bottom: 6px;
  }
  .search-result-item .score.high { background: #ecfdf5; color: #065f46; }
  .search-result-item .score.medium { background: #fffbeb; color: #92400e; }
  .search-result-item .score.low { background: #fef2f2; color: #991b1b; }
  [data-theme="dark"] .search-result-item .score.high { background: #064e3b; color: #a7f3d0; }
  [data-theme="dark"] .search-result-item .score.medium { background: #451a03; color: #fcd34d; }
  [data-theme="dark"] .search-result-item .score.low { background: #7f1d1d; color: #fecaca; }
  .search-result-item .result-text {
    font-size: 13px; line-height: 1.6;
    max-height: 120px; overflow-y: auto;
  }
  .search-result-item .result-meta {
    font-size: 11px; color: var(--text-secondary); margin-top: 6px;
  }
  .search-result-item .result-meta span { margin-right: 8px; }

  /* Loading */
  .loading-spinner {
    display: flex; align-items: center; justify-content: center;
    padding: 20px; color: var(--text-secondary); font-size: 14px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-spinner::before {
    content: ''; width: 18px; height: 18px;
    border: 2px solid var(--border); border-top-color: var(--primary);
    border-radius: 50%; animation: spin .6s linear infinite;
    margin-right: 8px;
  }

  /* Toast */
  .toast {
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    padding: 12px 24px; border-radius: 10px; font-size: 13px;
    z-index: 9999; opacity: 0; transition: opacity .3s;
    pointer-events: none;
  }
  .toast.show { opacity: 1; }
  .toast.success { background: #065f46; color: #fff; }
  .toast.error { background: #991b1b; color: #fff; }

  @media (max-width: 768px) {
    .sidebar {
      position: fixed; left: 0; top: 0; z-index: 100;
      transform: translateX(-100%);
      transition: transform .25s ease;
      box-shadow: var(--shadow-lg);
    }
    .sidebar.open { transform: translateX(0); }
    .main { padding: 16px; }
    .menu-toggle {
      display: flex; align-items: center; justify-content: center;
      width: 36px; height: 36px; border: none;
      background: var(--card); border-radius: 10px; box-shadow: var(--shadow);
      cursor: pointer; font-size: 17px; color: var(--text);
    }
    .search-bar { flex-wrap: wrap; }
    .search-bar select { min-width: 100%; }
    .doc-item { flex-direction: column; align-items: flex-start; }
    .doc-item .doc-actions { margin-top: 8px; }
  }

  /* ===== Sync Dashboard ===== */
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
  }
  .stat-item {
    text-align: center;
    padding: 16px;
    background: var(--bg);
    border-radius: 8px;
  }
  .stat-item .stat-num { font-size: 28px; font-weight: 700; display: block; }
  .stat-item span:last-child { font-size: 12px; color: var(--text-secondary); }
  .filter-bar {
    display: flex; gap: 8px; margin-bottom: 16px;
  }
  .filter-bar select {
    padding: 8px 12px; border: 1px solid var(--border);
    border-radius: 8px; font-size: 13px;
    background: var(--card); color: var(--text);
    outline: none;
  }
  .filter-bar select:focus { border-color: var(--primary); }
  .sync-file-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; border-radius: 8px;
    border: 1px solid var(--border); margin-bottom: 6px;
    transition: background .15s;
  }
  .sync-file-item:hover { background: var(--hover); }
  .sync-file-item .file-info { flex: 1; min-width: 0; }
  .sync-file-item .file-name {
    font-size: 13px; font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .sync-file-item .file-meta {
    font-size: 11px; color: var(--text-secondary); margin-top: 2px;
  }
  .status-badge {
    display: inline-block; padding: 2px 10px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
  }
  .status-badge.synced { background: #ecfdf5; color: #065f46; }
  .status-badge.pending, .status-badge.new { background: #fffbeb; color: #92400e; }
  .status-badge.error { background: #fef2f2; color: #991b1b; }
  [data-theme="dark"] .status-badge.synced { background: #064e3b; color: #a7f3d0; }
  [data-theme="dark"] .status-badge.pending,
  [data-theme="dark"] .status-badge.new { background: #451a03; color: #fcd34d; }
  [data-theme="dark"] .status-badge.error { background: #7f1d1d; color: #fecaca; }
  .sync-file-item .sync-actions button {
    padding: 6px 14px; border: none; border-radius: 6px;
    font-size: 12px; cursor: pointer; transition: all .2s;
    margin-left: 8px;
  }
  .sync-actions .sync-btn {
    background: var(--primary); color: #fff;
  }
  .sync-actions .sync-btn:hover { background: var(--primary-dark); }
  .sync-actions .sync-btn:disabled { opacity: .5; cursor: not-allowed; }
  .sync-actions .sync-btn-force {
    background: transparent; color: var(--danger);
    border: 1px solid var(--danger);
  }
  .sync-actions .sync-btn-force:hover { background: #fef2f2; }
  [data-theme="dark"] .sync-actions .sync-btn-force:hover { background: #450a0a; }
  .sync-actions .sync-btn-force:disabled { opacity: .5; cursor: not-allowed; }
  /* ===== Sync Progress ===== */
  .progress-task-card {
    padding: 14px; border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 10px;
    background: var(--bg); transition: opacity .3s;
  }
  .progress-task-card .pt-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px; font-size: 14px; font-weight: 500;
  }
  .progress-task-card .pt-header .pt-status {
    font-size: 12px; padding: 2px 10px; border-radius: 10px;
  }
  .pt-status.running { background: #dbeafe; color: #1e40af; }
  .pt-status.done { background: #ecfdf5; color: #065f46; }
  .pt-status.error { background: #fef2f2; color: #991b1b; }
  [data-theme="dark"] .pt-status.running { background: #1e3a5f; color: #93c5fd; }
  [data-theme="dark"] .pt-status.done { background: #064e3b; color: #a7f3d0; }
  [data-theme="dark"] .pt-status.error { background: #7f1d1d; color: #fecaca; }
  .progress-bar-track {
    height: 8px; background: var(--border); border-radius: 4px;
    overflow: hidden; margin-bottom: 6px;
  }
  .progress-bar-track .pb-fill {
    height: 100%; border-radius: 4px;
    background: linear-gradient(90deg, #3b82f6, #2563eb);
    transition: width .5s ease;
  }
  .pb-fill.done { background: linear-gradient(90deg, #34d399, #10b981); }
  .pb-fill.error { background: linear-gradient(90deg, #f87171, #ef4444); }
  .progress-task-card .pt-message {
    font-size: 12px; color: var(--text-secondary);
    display: flex; justify-content: space-between;
  }
  .sync-delete-btn {
    padding: 4px 8px; border: none; border-radius: 4px;
    background: transparent; color: var(--danger);
    cursor: pointer; font-size: 15px; opacity: 0.5;
    transition: all .2s; margin-left: 4px;
  }
  .sync-delete-btn:hover { opacity: 1; background: #fef2f2; }
  [data-theme="dark"] .sync-delete-btn:hover { background: #450a0a; }
  .coll-select {
    padding: 2px 6px; border: 1px solid var(--border); border-radius: 4px;
    font-size: 12px; background: var(--bg); color: var(--text); outline: none;
    cursor: pointer;
  }
  .coll-select:hover { border-color: var(--primary); }
  .coll-select:focus { border-color: var(--primary); box-shadow: 0 0 0 2px rgba(37,99,235,0.15); }
  .primary-btn {
    padding: 8px 20px; border: none; border-radius: 8px;
    background: var(--primary); color: #fff; font-size: 13px;
    cursor: pointer; transition: all .2s;
  }
  .primary-btn:hover { background: var(--primary-dark); }
  .primary-btn:disabled { opacity: .5; cursor: not-allowed; }
  .history-item {
    padding: 6px 0; border-bottom: 1px solid var(--border);
    font-size: 12px;
    display: flex; justify-content: space-between;
  }
  .history-item .h-time { color: var(--text-secondary); }
  .history-item .h-status { font-weight: 500; }
  @media (max-width: 640px) {
    .stat-grid { grid-template-columns: repeat(2, 1fr); }
    .filter-bar { flex-wrap: wrap; }
    .sync-file-item { flex-direction: column; align-items: flex-start; }
    .sync-file-item .sync-actions { margin-top: 8px; }
  }
</style>
</head>
<body>

<div class="layout">
  <!-- Sidebar -->
  <aside class="sidebar" id="sidebar">
    <div class="side-brand">
      <div class="side-logo">🤖</div>
      <div>
        <div class="side-title">恩特小助手</div>
        <div class="side-sub">文档管理</div>
      </div>
    </div>
    <nav class="side-nav">
      <button class="nav-item active" onclick="switchTab('docs', this)"><span class="nav-icon">📚</span>文档列表</button>
      <button class="nav-item" onclick="switchTab('upload', this)"><span class="nav-icon">📤</span>上传文件</button>
      <button class="nav-item" onclick="switchTab('search', this)"><span class="nav-icon">🔍</span>搜索测试</button>
      <button class="nav-item" onclick="switchTab('sync', this)"><span class="nav-icon">🔄</span>同步管理</button>
      <button class="nav-item" onclick="switchTab('people', this)"><span class="nav-icon">🗂</span>按人查看</button>
      <button class="nav-item" onclick="switchTab('dashboard', this)"><span class="nav-icon">📈</span>看板状态</button>
      <button class="nav-item" onclick="switchTab('users', this)"><span class="nav-icon">👥</span>用户管理</button>
    </nav>
    <div class="side-footer">
      <button onclick="toggleTheme()" id="themeBtn" title="切换暗黑模式">🌙</button>
    </div>
  </aside>

  <!-- Main -->
  <main class="main">
    <header class="page-header">
      <button class="menu-toggle" id="menuToggle" onclick="toggleSidebar()" title="菜单">☰</button>
      <h1 id="pageTitle">📚 文档列表</h1>
      <div style="width:36px"></div>
    </header>

  <!-- Tab: Document List -->
  <div id="tab-docs" class="tab-content active">
    <div class="card">
      <div class="loading-spinner" id="docsLoading">加载中...</div>
      <div id="docsContent" style="display:none"></div>
    </div>
  </div>

  <!-- Tab: Upload -->
  <div id="tab-upload" class="tab-content">
    <div class="card">
      <h3>📤 上传文档</h3>
      <div style="margin-bottom:16px">
        <label style="font-size:13px;font-weight:500;color:var(--text-secondary);display:block;margin-bottom:6px">
          文件归属类型
        </label>
        <select id="uploadCollection" style="width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px;background:var(--card);color:var(--text);outline:none">
          <option value="standards" data-exts="pdf">📋 标准文档（PDF → 标准查询用）</option>
          <option value="error_codes" data-exts="xlsx,xls">🔧 故障代码（Excel → 故障查询用）</option>
          <option value="experience_kb" data-exts="md">💡 经验知识（Markdown → 经验查询用）</option>
        </select>
        <p id="uploadTypeHint" style="font-size:12px;color:var(--text-secondary);margin-top:4px">📋 标准文档：上传 PDF 文件，自动切块入库到标准知识库</p>
      </div>
      <div style="margin-bottom:16px">
        <label style="font-size:13px;font-weight:500;color:var(--text-secondary);display:block;margin-bottom:6px">
          所属部门（知识库所有者）
        </label>
        <select id="uploadDepartment" style="width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px;background:var(--card);color:var(--text);outline:none">
          <option value="public">🌐 全公司公开</option>
          <option value="pmo">📋 产品与项目管理中心（PMO）</option>
          <option value="rd">🔬 研发中心</option>
          <option value="mfg">🏭 制造中心</option>
          <option value="bz">💼 商业中心</option>
          <option value="ops">⚙️ 运营支持中心</option>
        </select>
      </div>
      <div class="drop-zone" id="dropZone">
        <div class="icon">📄</div>
        <p><strong>点击选择</strong> 或拖拽文件到此处</p>
        <p style="font-size:12px;margin-top:4px" id="uploadFileTypes">支持 .pdf</p>
        <input type="file" id="fileInput" accept=".pdf,.xlsx,.xls,.md" style="display:none">
      </div>
      <div class="upload-progress" id="uploadProgress">
        <div class="progress-bar"><div class="fill" id="progressFill"></div></div>
        <p id="progressText" style="font-size:13px;color:var(--text-secondary)">处理中...</p>
      </div>
      <div class="upload-result" id="uploadResult"></div>
    </div>
  </div>

  <!-- Tab: Search Test -->
  <div id="tab-search" class="tab-content">
    <div class="card">
      <h3>🔍 搜索测试</h3>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px">
        在已入库的文档中搜索关键词，验证切块和检索效果
      </p>
      <div class="search-bar">
        <select id="searchCollection">
          <option value="">所有 collection</option>
        </select>
        <input type="text" id="searchQuery" placeholder="输入搜索关键词…"
               onkeydown="if(event.key==='Enter') searchDocs()">
        <button onclick="searchDocs()" id="searchBtn">搜索</button>
      </div>
      <div id="searchResults"></div>
    </div>
  </div>

  <!-- Tab: Sync Dashboard -->
  <div id="tab-sync" class="tab-content">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3>🔄 文件同步管理</h3>
        <div style="display:flex;gap:8px">
          <button onclick="triggerSyncAll()" class="primary-btn" id="syncAllBtn">同步全部待处理</button>
          <button onclick="triggerForceSyncAll()" class="primary-btn" style="background:var(--danger);color:#fff" title="跳过 Chroma 预检查，全部文件强制重新入库">⚡强制重学全部</button>
        </div>
      </div>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">
        自动同步每 24h 执行一次。可手动选择文件单独同步。
      </p>

      <!-- 统计概览 -->
      <div id="syncStats" style="display:none;margin-bottom:16px">
        <div class="stat-grid">
          <div class="stat-item"><span class="stat-num" id="statTotal">0</span><span>总计</span></div>
          <div class="stat-item"><span class="stat-num" id="statSynced" style="color:var(--success)">0</span><span>已同步</span></div>
          <div class="stat-item"><span class="stat-num" id="statPending" style="color:var(--warning)">0</span><span>待处理</span></div>
          <div class="stat-item"><span class="stat-num" id="statError" style="color:var(--danger)">0</span><span>失败</span></div>
        </div>
      </div>

      <!-- 过滤栏 -->
      <div class="filter-bar">
        <select id="syncDirFilter" onchange="loadSyncFiles()">
          <option value="">所有目录</option>
          <option value="uploads">📁 data/uploads/（钉钉上传）</option>
          <option value="standards">📁 data/standards/（标准文档）</option>
          <option value="fault_codes">📁 data/fault_codes/（故障代码）</option>
          <option value="experience">📁 data/experience/（经验知识）</option>
        </select>
        <select id="syncStatusFilter" onchange="loadSyncFiles()">
          <option value="">所有状态</option>
          <option value="new">🆕 新文件</option>
          <option value="pending">⏳ 待处理</option>
          <option value="synced">✅ 已同步</option>
          <option value="error">❌ 失败</option>
        </select>
      </div>

      <!-- 文件列表 -->
      <div id="syncFileList">
        <div class="loading-spinner">加载中...</div>
      </div>

      <!-- 同步进度追踪 -->
      <div class="card" style="margin-top:20px">
        <h3>⏳ 同步进度</h3>
        <div id="syncProgressArea">
          <div class="empty-state">暂无进行中的同步任务</div>
        </div>
      </div>

      <!-- 同步历史 -->
      <details style="margin-top:20px">
        <summary style="cursor:pointer;font-size:14px;font-weight:500;color:var(--text-secondary)">
          📜 同步历史记录
        </summary>
        <div id="syncHistory" style="margin-top:8px">
          <div class="loading-spinner">加载中...</div>
        </div>
      </details>
    </div>
  </div>

  <!-- Tab: People（按人查看，v1.12.8 管理层视图） -->
  <div id="tab-people" class="tab-content">
    <div class="card">
      <h3>🗂 按人查看 <small style="font-size:13px;color:var(--text-secondary)">每人上传的文件与学习进度</small></h3>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">
        数据来自上传时记录的归属（钉钉端自动记录，管理员直传归「未记录上传者」）。
        同步/重学等操作请到「🔄 同步管理」tab 处理。
      </p>
      <div id="peopleContent">
        <div class="loading-spinner">加载中...</div>
      </div>
    </div>
  </div>

  <!-- Tab: Dashboard Status（真实执行状态，不把订阅配置当推送成功） -->
  <div id="tab-dashboard" class="tab-content">
    <div class="card">
      <h3>📈 看板状态 <small style="font-size:13px;color:var(--text-secondary)">最近一次实际运行与失败提醒送达情况</small></h3>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">
        这里展示任务是否真的推送成功；“未运行/跳过/失败”都会明确说明原因。失败提醒未送达时，请优先检查钉钉权限、网络与管理员 staff_id 配置。
      </p>
      <div id="dashboardStatusContent"><div class="loading-spinner">加载中...</div></div>
    </div>
  </div>

  <!-- Tab: User Management -->
  <div id="tab-users" class="tab-content">
    <div class="card">
      <h3>👥 用户管理 <small style="font-size:13px;color:var(--text-secondary)">设置用户归属中心</small></h3>
      <div class="filter-bar" style="margin-bottom:16px">
        <input type="text" id="userSearchInput" placeholder="搜索用户ID或昵称…"
               onkeyup="filterUserTable()"
               style="padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-size:13px;background:var(--card);color:var(--text);outline:none;flex:1">
      </div>
      <div id="userTableArea">
        <div class="loading-spinner">加载用户列表...</div>
      </div>
    </div>
  </div>
  </main>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
// ===== Init =====
(function init() {
  var t = localStorage.getItem('admin_theme') || 'light';
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeBtn').textContent = t === 'dark' ? '☀️' : '🌙';
  loadDocs();
  loadSearchCollections();
})();

// ===== Theme =====
function toggleTheme() {
  var h = document.documentElement;
  var cur = h.getAttribute('data-theme');
  var next = cur === 'dark' ? 'light' : 'dark';
  h.setAttribute('data-theme', next);
  localStorage.setItem('admin_theme', next);
  document.getElementById('themeBtn').textContent = next === 'dark' ? '☀️' : '🌙';
}

// ===== Toast =====
function showToast(msg, type) {
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type + ' show';
  setTimeout(function() { t.classList.remove('show'); }, 3000);
}

// ===== Tabs（侧边栏导航） =====
var TAB_TITLES = { docs: '📚 文档列表', upload: '📤 上传文件', search: '🔍 搜索测试', sync: '🔄 同步管理', people: '🗂 按人查看', dashboard: '📈 看板状态', users: '👥 用户管理' };
function switchTab(name, el) {
  document.querySelectorAll('.side-nav .nav-item').forEach(function(b) {
    b.classList.remove('active');
  });
  (el || event.target).classList.add('active');
  document.querySelectorAll('.tab-content').forEach(function(t) {
    t.classList.remove('active');
  });
  document.getElementById('tab-' + name).classList.add('active');
  var t = document.getElementById('pageTitle');
  if (t) t.textContent = TAB_TITLES[name] || name;
  if (name === 'docs') loadDocs();
  if (name === 'search') loadSearchCollections();
  if (name === 'sync') { loadSyncFiles(); loadSyncHistory(); loadSyncStats(); loadSyncProgress(); }
  if (name === 'people') { loadPeople(); }
  if (name === 'dashboard') { loadDashboardStatus(); }
  if (name === 'users') { loadUsers(); }
  // 移动端切换后收起侧边栏
  if (window.innerWidth <= 768) {
    var sb = document.getElementById('sidebar');
    if (sb) sb.classList.remove('open');
  }
}

// ===== 移动端侧边栏开合 =====
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

// ===== Escape HTML =====
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ===== Tab 1: Document List =====
function loadDocs() {
  var loading = document.getElementById('docsLoading');
  var content = document.getElementById('docsContent');
  loading.style.display = '';
  content.style.display = 'none';

  fetch('/admin/docs?password=' + encodeURIComponent(getPw()))
    .then(function(r) {
      if (!r.ok) throw new Error('服务端返回 ' + r.status + ' ' + r.statusText);
      return r.json();
    })
    .then(function(data) {
      loading.style.display = 'none';
      var html = '';
      var colNames = Object.keys(data);
      if (!colNames.length) {
        html = '<div class="empty-state">暂无入库文档</div>';
      }
      colNames.forEach(function(collection) {
        var info = data[collection];
        html += '<div class="collection-group">';
        html += '<div class="collection-header">';
        html += '<h3>' + esc(collection) + '</h3>';
        html += '<span class="badge">' + info.count + ' 条</span>';
        html += '</div>';

        if (!info.files || !info.files.length) {
          html += '<div class="empty-state">空</div>';
        } else {
          for (var i = 0; i < info.files.length; i++) {
            var f = info.files[i];
            html += '<div class="doc-item">';
            html += '<div class="doc-info">';
            html += '<div class="doc-name">📄 ' + esc(f.file_name) + '</div>';
            html += '<div class="doc-meta">';
            html += '<span>🧩 ' + f.chunk_count + ' 块</span>';
            if (f.chapters && f.chapters.length) {
              html += '<span>📖 ' + esc(f.chapters.slice(0, 5).join(' · ')) + '</span>';
            }
            html += '</div></div>';
            html += '<div class="doc-actions">';
            html += '<button class="del-btn" data-collection="' + esc(collection) + '" data-file="' + esc(f.file_name) + '">删除</button>';
            html += '</div></div>';
          }
        }
        html += '</div>';
      });
      content.innerHTML = html;
      content.style.display = '';
    })
    .catch(function(err) {
      loading.innerHTML = '加载失败: ' + err.message + '，请检查服务是否运行';
    });
}

// 事件委托：处理所有删除按钮点击
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.del-btn');
  if (btn) {
    var collection = btn.getAttribute('data-collection');
    var fileName = btn.getAttribute('data-file');
    deleteDoc(collection, fileName);
  }
});

function deleteDoc(collection, fileName) {
  if (!confirm('确定删除 "' + fileName + '" 吗？\n其所有切块将被清除。')) return;

  fetch('/admin/docs?collection=' + encodeURIComponent(collection) + '&file_name=' + encodeURIComponent(fileName) + '&password=' + encodeURIComponent(getPw()), {
    method: 'DELETE',
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      showToast('已删除 ' + data.deleted + ' 条', 'success');
      loadDocs();
    })
    .catch(function(err) {
      showToast('删除失败: ' + err.message, 'error');
    });
}

// ===== Tab 2: Upload =====
(function initUpload() {
  var dz = document.getElementById('dropZone');
  var fi = document.getElementById('fileInput');
  var sel = document.getElementById('uploadCollection');

  // 切换类型时更新可上传的文件类型提示
  sel.addEventListener('change', function() {
    var opt = sel.options[sel.selectedIndex];
    var exts = opt.getAttribute('data-exts');
    var hint = document.getElementById('uploadTypeHint');
    var types = document.getElementById('uploadFileTypes');
    hint.textContent = opt.text;
    types.textContent = '支持 .' + exts.replace(/,/g, ' .');
    fi.accept = '.' + exts.replace(/,/g, ',.');
  });

  dz.addEventListener('click', function() { fi.click(); });

  dz.addEventListener('dragover', function(e) {
    e.preventDefault();
    dz.classList.add('dragover');
  });
  dz.addEventListener('dragleave', function() {
    dz.classList.remove('dragover');
  });
  dz.addEventListener('drop', function(e) {
    e.preventDefault();
    dz.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });
  fi.addEventListener('change', function() {
    if (fi.files.length) uploadFile(fi.files[0]);
  });
})();

function uploadFile(file) {
  var progress = document.getElementById('uploadProgress');
  var fill = document.getElementById('progressFill');
  var text = document.getElementById('progressText');
  var result = document.getElementById('uploadResult');

  // Validate
  var ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'xlsx', 'xls'].includes(ext)) {
    showToast('不支持的文件格式: .' + ext, 'error');
    return;
  }

  progress.classList.add('show');
  fill.style.width = '30%';
  fill.classList.remove('done');
  text.textContent = '📤 上传中... (' + file.name + ')';
  result.classList.remove('show');
  result.className = 'upload-result';

  var formData = new FormData();
  formData.append('file', file);
  formData.append('collection', document.getElementById('uploadCollection').value);
  formData.append('department', document.getElementById('uploadDepartment').value);
  formData.append('password', getPw());

  fetch('/admin/upload', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('上传失败: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      fill.style.width = '100%';
      fill.classList.add('done');
      text.textContent = '✅ 上传完成，待同步';
      result.className = 'upload-result success show';
      result.innerHTML = '✅ 文件已上传成功！<br>'
        + '文件: ' + esc(data.file_name) + '<br>'
        + '预选库: ' + esc(data.collection) + '<br><br>'
        + '👉 请到「<a href="#" onclick="switchTab(\'sync\', document.querySelector(\'.side-nav .nav-item:nth-child(4)\'));return false" style="color:var(--primary)">🔄 同步管理</a>」侧边栏选库后手动同步入库';
      showToast('上传完成，请到同步管理处理', 'success');
      loadDocs();
      loadSearchCollections();
    })
    .catch(function(err) {
      fill.style.width = '100%';
      result.className = 'upload-result error show';
      result.innerHTML = '❌ ' + esc(err.message);
      text.textContent = '上传失败';
    });
}

function pollTaskStatus(taskId, fileName, fill, text, result) {
  var pollTimer = setInterval(function() {
    fetch('/admin/upload-status/' + taskId + '?password=' + encodeURIComponent(getPw()))
      .then(function(r) {
        if (!r.ok) throw new Error('查询失败');
        return r.json();
      })
      .then(function(status) {
        // 更新进度
        if (status.status === 'pending') {
          fill.style.width = '25%';
          text.textContent = '⏳ 排队等待处理...';
        } else if (status.status === 'processing') {
          fill.style.width = '50%';
          text.textContent = '⚙️ 正在处理中... (提取 → 切块 → 入库)';
        } else if (status.status === 'done') {
          clearInterval(pollTimer);
          fill.style.width = '100%';
          fill.classList.add('done');
          text.textContent = '✅ 处理完成！共 ' + (status.result.chunk_count || 0) + ' 块';
          result.className = 'upload-result success show';
          result.innerHTML = '✅ 处理完成！<br>'
            + '文件: ' + esc(status.result.file_name || fileName) + '<br>'
            + '入库: ' + (status.result.chunk_count || 0) + ' 块 → ' + esc(status.result.collection || '')
            + (status.result.std_id ? '<br>标准: ' + esc(status.result.std_id) : '')
            + (status.result.source ? '<br>来源: ' + esc(status.result.source) : '');
          showToast('上传成功: ' + (status.result.chunk_count || 0) + ' 块', 'success');
          loadDocs();
          loadSearchCollections();
          return;
        } else if (status.status === 'error') {
          clearInterval(pollTimer);
          fill.style.width = '100%';
          text.textContent = '❌ 处理失败';

          // 判断是否是 ocr_needed
          if (status.result && status.result.status === 'ocr_needed') {
            result.className = 'upload-result show';
            result.style.cssText = 'display:block;padding:12px 16px;border-radius:8px;font-size:13px;background:#fffbeb;color:#92400e;';
            result.innerHTML = '⚠️ ' + (status.result.message || '扫描型 PDF，需要 OCR 处理');
          } else {
            result.className = 'upload-result error show';
            result.innerHTML = '❌ ' + esc(status.error || '处理失败');
          }
          return;
        } else if (status.status === 'timeout') {
          clearInterval(pollTimer);
          fill.style.width = '100%';
          text.textContent = '⏰ 处理超时';
          result.className = 'upload-result error show';
          result.innerHTML = '⏰ ' + esc(status.error || '处理超时（超过 30 分钟）');
          return;
        }

        // 超过 30 分钟前端也兜底停止轮询
        if (status.updated_at && Date.now() / 1000 - status.updated_at > 1800) {
          clearInterval(pollTimer);
          fill.style.width = '100%';
          text.textContent = '⏰ 处理超时';
          result.className = 'upload-result error show';
          result.innerHTML = '⏰ 后台处理超过 30 分钟未完成，请检查服务端日志';
        }
      })
      .catch(function(err) {
        // 轮询失败不中断，继续尝试
        console.error('轮询失败:', err);
      });
  }, 2000);  // 每 2 秒轮询一次
}

// ===== Tab 3: Search =====
function loadSearchCollections() {
  fetch('/admin/collections?password=' + encodeURIComponent(getPw()))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var sel = document.getElementById('searchCollection');
      var cur = sel.value;
      sel.innerHTML = '<option value="">所有 collection</option>';
      var keys = Object.keys(data);
      for (var i = 0; i < keys.length; i++) {
        var opt = document.createElement('option');
        opt.value = keys[i];
        opt.textContent = keys[i] + ' (' + data[keys[i]].count + ' 条)';
        sel.appendChild(opt);
      }
      sel.value = cur;
    })
    .catch(function(err) {
      console.error('加载 collections 失败:', err);
    });
}

function searchDocs() {
  var q = document.getElementById('searchQuery').value.trim();
  var coll = document.getElementById('searchCollection').value;
  if (!q) { showToast('请输入搜索关键词', 'error'); return; }

  var btn = document.getElementById('searchBtn');
  btn.disabled = true;
  btn.textContent = '搜索中...';
  var resultsEl = document.getElementById('searchResults');
  resultsEl.innerHTML = '<div class="loading-spinner">搜索中...</div>';

  var url = '/admin/search?q=' + encodeURIComponent(q) + '&limit=10&password=' + encodeURIComponent(getPw());
  if (coll) url += '&collection=' + encodeURIComponent(coll);

  fetch(url)
    .then(function(r) {
      if (!r.ok) throw new Error('搜索接口返回 ' + r.status + ' ' + r.statusText);
      return r.json();
    })
    .then(function(data) {
      if (coll) {
        renderSearchResults(data, resultsEl);
      } else {
        // 多个 collection 的结果合并展示
        var html = '';
        var keys = Object.keys(data.results || {});
        for (var i = 0; i < keys.length; i++) {
          var r = data.results[keys[i]];
          if (r && r.length) {
            html += '<h4 style="margin:12px 0 8px;font-size:14px;color:var(--primary)">📁 ' + esc(keys[i]) + '</h4>';
            for (var j = 0; j < r.length; j++) {
              html += renderSingleResult(r[j], j);
            }
          }
        }
        if (!html) {
          html = '<div class="empty-state">无匹配结果</div>';
        }
        resultsEl.innerHTML = html;
      }
    })
    .catch(function(err) {
      resultsEl.innerHTML = '<div class="empty-state" style="color:var(--danger)">搜索失败: ' + esc(err.message) + '</div>';
    })
    .finally(function() {
      btn.disabled = false;
      btn.textContent = '搜索';
    });
}

function renderSearchResults(data, el) {
  var items = data.results || [];
  if (!items.length) {
    el.innerHTML = '<div class="empty-state">无匹配结果</div>';
    return;
  }
  var html = '<p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px">找到 ' + items.length + ' 条结果</p>';
  for (var i = 0; i < items.length; i++) {
    html += renderSingleResult(items[i], i);
  }
  el.innerHTML = html;
}

function renderSingleResult(item, idx) {
  var score = item.score !== undefined ? item.score : 1;
  var sim = item.similarity !== undefined ? item.similarity : 0;

  var scoreClass = 'low';
  var scoreLabel = '低';
  if (score < 0.6) { scoreClass = 'high'; scoreLabel = '高匹配'; }
  else if (score < 0.9) { scoreClass = 'medium'; scoreLabel = '中匹配'; }

  var text = item.text || '(无内容)';
  var meta = item.metadata || {};

  var metaHtml = '';
  var metaFields = ['std_id', 'std_title', 'chapter', 'chapter_title', 'file_name', 'fault_code', 'name'];
  for (var i = 0; i < metaFields.length; i++) {
    if (meta[metaFields[i]]) {
      metaHtml += '<span>' + esc(metaFields[i]) + ': ' + esc(meta[metaFields[i]]) + '</span>';
    }
  }

  return '<div class="search-result-item">'
    + '<span class="score ' + scoreClass + '">' + scoreLabel + ' (距离:' + score.toFixed(4) + ')</span>'
    + '<div class="result-text">' + esc(text) + '</div>'
    + (metaHtml ? '<div class="result-meta">' + metaHtml + '</div>' : '')
    + '</div>';
}

// ===== Tab 4: Sync Dashboard =====
// 管理端认证由 HttpOnly Cookie 承担；绝不把实际密码放进 URL、浏览器历史、
// 代理日志或前端 JavaScript 可读取的存储。保留空参数仅兼容既有接口形态。
function getPw() {
  return '';
}

function loadSyncStats() {
  fetch('/admin/sync-stats?password=' + getPw())
    .then(function(r) { return r.json(); })
    .then(function(data) {
      document.getElementById('syncStats').style.display = '';
      document.getElementById('statTotal').textContent =
        (data.files_in_uploads || 0) + (data.files_in_standards || 0);
      document.getElementById('statSynced').textContent = data.synced || 0;
      document.getElementById('statPending').textContent = data.pending || 0;
      document.getElementById('statError').textContent = data.error || 0;
    })
    .catch(function() {});
}

// ===== Tab: 按人查看（v1.12.8 管理层视图） =====
function loadPeople() {
  var area = document.getElementById('peopleContent');
  area.innerHTML = '<div class="loading-spinner">加载中...</div>';
  var pw = getPw();
  fetch('/admin/people?password=' + encodeURIComponent(pw))
    .then(function(r) {
      if (!r.ok) throw new Error('请求失败 ' + r.status);
      return r.json();
    })
    .then(function(data) {
      var people = data.people || [];
      var html = '';
      if (!people.length) {
        html = '<div class="empty-state">暂无上传记录</div>';
      }
      var statusLabel = {synced: '已同步', pending: '待处理', error: '失败'};
      for (var i = 0; i < people.length; i++) {
        var p = people[i];
        var name = p.upload_user_name
          || (p.upload_user_id ? ('ID:' + p.upload_user_id) : '未记录上传者');
        // 文件数多时默认折叠（如管理员历史上传），少时直接展开
        html += '<details class="collection-group" ' + (p.files.length <= 10 ? 'open' : '') + '>';
        html += '<summary class="collection-header" style="cursor:pointer">';
        html += '<h3>👤 ' + esc(name) + '</h3>';
        html += '<span style="display:flex;gap:6px">';
        html += '<span class="badge">' + p.files.length + ' 个文件</span>';
        html += '<span class="badge" style="background:var(--success)">✅ ' + p.synced + '</span>';
        html += '<span class="badge" style="background:var(--warning)">⏳ ' + p.pending + '</span>';
        html += '<span class="badge" style="background:var(--danger)">❌ ' + p.error + '</span>';
        html += '</span>';
        html += '</summary>';
        for (var fi = 0; fi < p.files.length; fi++) {
          var f = p.files[fi];
          var st = statusLabel[f.sync_status] || f.sync_status;
          html += '<div class="sync-file-item">';
          html += '<div class="file-info">';
          html += '<div class="file-name">📄 ' + esc(f.file_name) + '</div>';
          html += '<div class="file-meta">';
          html += '<span class="status-badge ' + f.sync_status + '">' + st + '</span>';
          html += ' · 目标: ' + esc(f.target_collection || '-');
          if (f.file_size) html += ' · ' + (f.file_size / 1024).toFixed(1) + 'KB';
          if (f.last_synced_at) html += ' · 同步于 ' + esc(f.last_synced_at).slice(0, 16);
          if (f.error_message) html += ' · ❌ ' + esc(f.error_message);
          html += '</div></div>';
          html += '</div>';
        }
        html += '</details>';
      }
      area.innerHTML = html;
    })
    .catch(function(err) {
      area.innerHTML = '<div class="empty-state" style="color:var(--danger)">加载失败: ' + esc(err.message) + '</div>';
    });
}

// ===== Tab: 看板状态（v1.13.1） =====
function loadDashboardStatus() {
  var area = document.getElementById('dashboardStatusContent');
  area.innerHTML = '<div class="loading-spinner">加载中...</div>';
  fetch('/admin/dashboard-status?password=' + encodeURIComponent(getPw()))
    .then(function(r) {
      if (!r.ok) throw new Error('请求失败 ' + r.status);
      return r.json();
    })
    .then(function(data) {
      var items = data.subscriptions || [];
      if (!items.length) {
        area.innerHTML = '<div class="empty-state">暂无看板任务。可在钉钉发送文档后说“做每日看板”创建。</div>';
        return;
      }
      var statusLabel = {success: '✅ 已成功推送', skipped: '⏸ 已跳过', failed: '❌ 执行失败'};
      var stageLabel = {resolve_sources: '数据源解析', collect: '数据采集', change_check: '变化检查', policy: '推送策略', push: '钉钉推送', execution: '执行异常'};
      var alertLabel = {not_needed: '无需失败提醒', sent: '失败提醒已送达', failed: '⚠️ 失败提醒未送达', not_sent: '⚠️ 未配置提醒接收人'};
      var html = '';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var state = it.last_run_status || '未运行';
        var stateText = statusLabel[state] || '⏳ ' + state;
        html += '<details class="collection-group" open>';
        html += '<summary class="collection-header" style="cursor:pointer"><h3>' + esc(it.title || '每日项目看板') + '</h3>';
        html += '<span class="badge">' + esc(stateText) + '</span></summary>';
        html += '<div class="file-meta" style="padding:10px 0">';
        html += '任务 #' + esc(it.id) + ' · 计划 ' + esc(it.schedule) + ' · ' + (it.enabled ? '启用中' : '已停用');
        html += '<br>最近运行：' + esc(it.last_run_at || '尚未运行');
        if (it.last_run_stage) html += ' · 阶段：' + esc(stageLabel[it.last_run_stage] || it.last_run_stage);
        if (it.last_pushed_at) html += '<br>最近成功推送：' + esc(it.last_pushed_at);
        if (it.last_run_reason) html += '<br>说明：' + esc(it.last_run_reason);
        if (it.last_alert_status) html += '<br>失败提醒：' + esc(alertLabel[it.last_alert_status] || it.last_alert_status);
        html += '</div></details>';
      }
      area.innerHTML = html;
    })
    .catch(function(err) {
      area.innerHTML = '<div class="empty-state" style="color:var(--danger)">加载失败：' + esc(err.message) + '</div>';
    });
}

function loadSyncFiles() {
  var list = document.getElementById('syncFileList');
  list.innerHTML = '<div class="loading-spinner">加载中...</div>';

  var dirFilter = document.getElementById('syncDirFilter').value;
  var statusFilter = document.getElementById('syncStatusFilter').value;
  var pw = getPw();

  fetch('/admin/sync-files?password=' + pw)
    .then(function(r) {
      if (!r.ok) throw new Error('请求失败 ' + r.status);
      return r.json();
    })
    .then(function(data) {
      var html = '';
      var dirKeys = Object.keys(data.directories);
      for (var di = 0; di < dirKeys.length; di++) {
        var dk = dirKeys[di];
        if (dirFilter && dirFilter !== dk) continue;
        var dirInfo = data.directories[dk];
        var files = dirInfo.files || [];

        if (statusFilter) {
          files = files.filter(function(f) { return f.sync_status === statusFilter; });
        }
        if (files.length === 0) { continue; }

        html += '<div class="collection-group">';
        html += '<div class="collection-header">';
        html += '<h3>📁 ' + esc(dk) + ' (' + files.length + ')</h3>';
        html += '</div>';

        for (var fi = 0; fi < files.length; fi++) {
          var f = files[fi];
          var statusLabel = {synced: '已同步', pending: '待处理', error: '失败', new: '新文件'};
          var label = statusLabel[f.sync_status] || f.sync_status;
          var isPending = (f.sync_status === 'new' || f.sync_status === 'pending' || f.sync_status === 'error');

          // 上传者信息
          var userHtml = '';
          if (f.upload_user_name) {
            userHtml = '👤 ' + esc(f.upload_user_name) + '  ·  ';
          } else if (f.sync_status === 'new') {
            userHtml = '👤 未知（钉钉上传自动记录）  ·  ';
          }

          // 目标库选择（待处理文件可选，已同步只读显示）
          var collHtml = '';
          var curColl = f.target_collection || dirInfo.default_collection;
          if (isPending) {
            collHtml = '目标库: <select class="coll-select" data-path="' + esc(f.file_path) + '">'
              + '<option value="standards"' + (curColl === 'standards' ? ' selected' : '') + '>📋 标准文档 (standards)</option>'
              + '<option value="error_codes"' + (curColl === 'error_codes' ? ' selected' : '') + '>🔧 故障代码 (error_codes)</option>'
              + '<option value="experience_kb"' + (curColl === 'experience_kb' ? ' selected' : '') + '>💡 经验知识 (experience_kb)</option>'
              + '</select>';
          } else {
            collHtml = '目标: ' + esc(curColl);
          }

          // 归属中心选择（所有状态可改；已入库文件改后需点「强制重学」重写 Chroma）
          var deptOptions = [
            {v: 'public', label: '🌐 全公司公开'},
            {v: 'pmo', label: '📋 PMO'},
            {v: 'rd', label: '🔬 研发'},
            {v: 'mfg', label: '🏭 制造'},
            {v: 'bz', label: '💼 商业'},
            {v: 'ops', label: '⚙️ 运营'}
          ];
          var curDept = f.suggested_department || 'public';
          var deptHtml = '归属: <select class="coll-select dept-select" data-path="' + esc(f.file_path) + '" title="修改归属后点「同步」或「强制重学」生效">';
          for (var d = 0; d < deptOptions.length; d++) {
            deptHtml += '<option value="' + deptOptions[d].v + '"' + (curDept === deptOptions[d].v ? ' selected' : '') + '>' + deptOptions[d].label + '</option>';
          }
          deptHtml += '</select>';

          html += '<div class="sync-file-item">';
          html += '<div class="file-info">';
          html += '<div class="file-name">📄 ' + esc(f.file_name) + '</div>';
          html += '<div class="file-meta">';
          html += userHtml;
          html += '<span class="status-badge ' + f.sync_status + '">' + label + '</span>';
          html += ' · ' + (f.file_size / 1024).toFixed(1) + 'KB';
          html += ' · ' + collHtml + ' · ' + deptHtml;
          if (f.error_message) html += ' · ❌ ' + esc(f.error_message);
          if (f.last_synced_at) html += ' · ' + esc(f.last_synced_at).slice(0, 16);
          html += '</div></div>';
          html += '<div class="sync-actions">';
          html += '<button class="sync-btn" data-path="' + esc(f.file_path) + '" data-force="false"' + (!isPending ? ' style="opacity:0.4"' : '') + '>' + (isPending ? '同步' : '已同步') + '</button>';
          html += '<button class="sync-btn-force" data-path="' + esc(f.file_path) + '" data-force="true" title="跳过 Chroma 预检查，强制重新入库">强制重学</button>';
          html += '<button class="sync-delete-btn" data-path="' + esc(f.file_path) + '" title="删除源文件（不影响已入库的知识库）">🗑️</button>';
          html += '</div></div>';
        }
        html += '</div>';
      }
      if (!html) html = '<div class="empty-state">无匹配文件</div>';
      list.innerHTML = html;
    })
    .catch(function(err) {
      list.innerHTML = '<div class="empty-state" style="color:var(--danger)">加载失败: ' + esc(err.message) + '</div>';
    });
}

// 事件委托：同步 + 删除按钮
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.sync-btn, .sync-btn-force');
  if (btn && btn.closest('#tab-sync')) {
    var path = btn.getAttribute('data-path');
    var force = btn.getAttribute('data-force') === 'true';

    // 从同文件项的下拉框读取目标库 + 归属中心
    var fileItem = btn.closest('.sync-file-item');
    var collSelect = fileItem ? fileItem.querySelector('.coll-select:not(.dept-select)') : null;
    var deptSelect = fileItem ? fileItem.querySelector('.dept-select') : null;
    var coll = collSelect ? collSelect.value : '';
    var dept = deptSelect ? deptSelect.value : '';

    asyncSyncFile(path, coll, dept, force, btn);
    return;
  }

  // 删除按钮
  var delBtn = e.target.closest('.sync-delete-btn');
  if (delBtn && delBtn.closest('#tab-sync')) {
    var path = delBtn.getAttribute('data-path');
    deleteSyncFile(path);
  }
});

function deleteSyncFile(filePath) {
  var fname = filePath.split('/').pop().split('\\').pop();
  if (!confirm('确定要删除源文件 "' + fname + '" 吗？\n\n注意：此操作只删除磁盘上的源文件，不影响已入库的知识库内容。\n如需删除知识库内容，请到「文档列表」Tab 操作。')) return;

  var formData = new FormData();
  formData.append('file_path', filePath);
  formData.append('password', getPw());

  fetch('/admin/sync-delete', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('删除失败: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      showToast('✅ 已删除: ' + data.file_name, 'success');
      loadSyncFiles();
      loadSyncStats();
      loadSyncHistory();
    })
    .catch(function(err) {
      showToast('❌ ' + err.message, 'error');
    });
}

function asyncSyncFile(filePath, collection, department, force, btn) {
  btn.disabled = true;
  btn.textContent = '⏳ 提交中...';

  var formData = new FormData();
  formData.append('file_path', filePath);
  formData.append('collection', collection);
  formData.append('department', department || '');
  formData.append('force', force ? 'true' : '');
  formData.append('password', getPw());

  fetch('/admin/sync-trigger', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('提交失败: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      if (data.status === 'skipped') {
        showToast('⏭ Chroma 已有该文件，跳过处理', 'success');
        btn.disabled = false;
        btn.textContent = '同步';
        loadSyncFiles();
        loadSyncStats();
        return;
      }

      var taskId = data.task_id;
      showToast('⏳ 同步任务已提交', 'success');
      loadSyncProgress();  // 立即加载进度面板
      btn.textContent = '⏳ 处理中...';

      // 轮询直到完成
      pollSyncTask(taskId, btn);
    })
    .catch(function(err) {
      showToast('❌ ' + err.message, 'error');
      btn.disabled = false;
      btn.textContent = '重试';
    });
}

function pollSyncTask(taskId, btn) {
  var pollTimer = setInterval(function() {
    fetch('/admin/upload-status/' + taskId + '?password=' + encodeURIComponent(getPw()))
      .then(function(r) { return r.json(); })
      .then(function(status) {
        loadSyncProgress();  // 刷新进度面板

        if (status.status === 'done' || status.status === 'error') {
          clearInterval(pollTimer);
          loadSyncFiles();
          loadSyncStats();
          if (btn) { btn.disabled = false; btn.textContent = '同步'; }
        }
      })
      .catch(function() {
        // 轮询失败不中断
      });
  }, 3000);  // 3 秒轮询一次
}

function loadSyncProgress() {
  fetch('/admin/recent-tasks?password=' + getPw())
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var tasks = data.running_tasks || [];
      var area = document.getElementById('syncProgressArea');
      if (!area) return;

      var html = '';

      // 活跃任务（正在处理或最近的）
      var activeTasks = tasks.filter(function(t) {
        return t.status === 'processing' || t.status === 'pending';
      });

      if (activeTasks.length === 0) {
        html += '<div class="empty-state">暂无进行中的同步任务</div>';
      }

      for (var i = 0; i < activeTasks.length; i++) {
        html += renderProgressCard(activeTasks[i]);
      }

      // 最近的已完成/失败任务（最多 5 条）
      var recentDone = tasks.filter(function(t) {
        return t.status === 'done' || t.status === 'error';
      }).slice(0, 5);

      if (recentDone.length > 0) {
        html += '<div style="margin-top:12px;font-size:13px;color:var(--text-secondary);font-weight:500">最近完成</div>';
        for (var i = 0; i < recentDone.length; i++) {
          html += renderProgressCard(recentDone[i]);
        }
      }

      area.innerHTML = html;
    })
    .catch(function() {});
}

function renderProgressCard(task) {
  var stepIcons = {
    'start': '🚀', 'mineru_upload': '📤', 'mineru_waiting': '⏳',
    'mineru_download': '📥', 'chunking': '✂️', 'indexing': '💾',
    'pymupdf_extract': '📖', 'excel_parse': '📊', 'syncing': '🔄',
    'done': '✅', 'error': '❌', 'timeout': '⏰',
  };
  var icon = stepIcons[task.step] || '⏳';

  var statusClass = 'running';
  var statusLabel = '处理中';
  if (task.status === 'done') { statusClass = 'done'; statusLabel = '完成'; }
  if (task.status === 'error') { statusClass = 'error'; statusLabel = '失败'; }

  var barClass = task.status === 'done' ? 'pb-fill done' :
                 task.status === 'error' ? 'pb-fill error' : 'pb-fill';
  var progress = task.progress || 0;
  var elapsed = '';
  if (task.created_at) {
    var secs = Math.floor((Date.now() / 1000) - task.created_at);
    if (secs < 120) elapsed = Math.floor(secs / 60) + '分' + (secs % 60) + '秒前';
    else elapsed = Math.floor(secs / 60) + '分钟前';
  }

  return '<div class="progress-task-card">'
    + '<div class="pt-header">'
    + '<span>' + icon + ' ' + esc(task.file_name || '') + '</span>'
    + '<span class="pt-status ' + statusClass + '">' + statusLabel + (elapsed ? ' · ' + elapsed : '') + '</span>'
    + '</div>'
    + '<div class="progress-bar-track">'
    + '<div class="' + barClass + '" style="width:' + progress + '%"></div>'
    + '</div>'
    + '<div class="pt-message">'
    + '<span>' + esc(task.progress_text || '') + '</span>'
    + '<span style="font-weight:600">' + progress + '%</span>'
    + '</div>'
    + '</div>';
}

// 每 5 秒自动刷新进度面板（不管在哪个 tab）
setInterval(function() {
  if (document.getElementById('tab-sync') &&
      document.getElementById('tab-sync').classList.contains('active')) {
    loadSyncProgress();
  }
}, 5000);

function triggerSyncAll() {
  var btn = document.getElementById('syncAllBtn');
  if (!confirm('确认同步所有待处理和失败的文件？')) return;
  btn.disabled = true;
  btn.textContent = '同步中...';

  var formData = new FormData();
  formData.append('sync_all', 'true');
  formData.append('force', '');
  formData.append('password', getPw());

  fetch('/admin/sync-trigger', { method: 'POST', body: formData })
    .then(function(r) { return r.json(); })
    .then(function() {
      showToast('全部同步完成', 'success');
      loadSyncFiles();
      loadSyncStats();
      loadSyncHistory();
      btn.disabled = false;
      btn.textContent = '同步全部待处理';
    })
    .catch(function(err) {
      showToast('同步失败: ' + err.message, 'error');
      btn.disabled = false;
      btn.textContent = '重新尝试';
    });
}

// 强制重学全部
function triggerForceSyncAll() {
  var btn = document.getElementById('syncAllBtn');
  if (!confirm('⚠️ 强制重学将重新入库所有文件，确认继续？')) return;
  btn.disabled = true;
  btn.textContent = '强制同步中...';

  var formData = new FormData();
  formData.append('sync_all', 'true');
  formData.append('force', 'true');
  formData.append('password', getPw());

  fetch('/admin/sync-trigger', { method: 'POST', body: formData })
    .then(function(r) { return r.json(); })
    .then(function() {
      showToast('强制同步完成', 'success');
      loadSyncFiles();
      loadSyncStats();
      loadSyncHistory();
      btn.disabled = false;
      btn.textContent = '同步全部待处理';
    })
    .catch(function(err) {
      showToast('同步失败: ' + err.message, 'error');
      btn.disabled = false;
      btn.textContent = '重新尝试';
    });
}

function loadSyncHistory() {
  fetch('/admin/sync-status?limit=30&password=' + getPw())
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var el = document.getElementById('syncHistory');
      var records = data.records || [];
      if (!records.length) {
        el.innerHTML = '<div class="empty-state">暂无同步记录</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        var icon = r.sync_status === 'synced' ? '✅' : r.sync_status === 'error' ? '❌' : '⏳';
        html += '<div class="history-item">';
        html += '<span>' + icon + ' ' + esc(r.file_name) + '</span>';
        html += '<span class="h-status">' + esc(r.sync_status) + '</span>';
        html += '<span class="h-time">' + esc((r.updated_at || '').slice(0, 16)) + '</span>';
        html += '</div>';
      }
      el.innerHTML = html;
    })
    .catch(function() {
      document.getElementById('syncHistory').innerHTML = '<div class="empty-state">加载失败</div>';
    });
}

// ===== Tab: User Management =====
function loadUsers() {
  var area = document.getElementById('userTableArea');
  area.innerHTML = '<div class="loading-spinner">加载中...</div>';

  fetch('/admin/users?password=' + getPw())
    .then(function(r) {
      if (!r.ok) throw new Error('请求失败 ' + r.status);
      return r.json();
    })
    .then(function(data) {
      var users = data.users || [];
      renderUserTable(users);
    })
    .catch(function(err) {
      area.innerHTML = '<div class="empty-state" style="color:var(--danger)">加载失败: ' + esc(err.message) + '</div>';
    });
}

function renderUserTable(users) {
  var html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">';
  html += '<thead><tr style="background:var(--bg);text-align:left">'
    + '<th style="padding:10px 12px">用户</th>'
    + '<th style="padding:10px 12px">昵称</th>'
    + '<th style="padding:10px 12px">角色</th>'
    + '<th style="padding:10px 12px">归属中心</th>'
    + '<th style="padding:10px 12px;width:100px">操作</th>'
    + '</tr></thead><tbody>';

  for (var i = 0; i < users.length; i++) {
    var u = users[i];
    var centers = u.centers || [];
    if (centers.length === 0 && u.center && u.center !== 'public') {
      centers = [u.center];
    }

    html += '<tr style="border-bottom:1px solid var(--border)">';
    html += '<td style="padding:8px 12px"><code style="font-size:11px;background:var(--bg);padding:2px 6px;border-radius:4px">' + esc(u.user_id.slice(0, 24)) + '</code></td>';
    html += '<td style="padding:8px 12px">' + esc(u.nick || '-') + '</td>';
    html += '<td style="padding:8px 12px">' + (u.leader ? '👑 主管' : '👤 成员') + '</td>';

    // Center checkboxes
    html += '<td style="padding:8px 12px" id="center-cells-' + i + '">';
    var allCenters = [
      {id: 'public', label: '公共'},
      {id: 'pmo', label: 'PMO'},
      {id: 'rd', label: '研发'},
      {id: 'mfg', label: '制造'},
      {id: 'bz', label: '商业'},
      {id: 'ops', label: '运营'},
    ];
    for (var c = 0; c < allCenters.length; c++) {
      var checked = centers.indexOf(allCenters[c].id) >= 0;
      html += '<label style="display:inline-block;margin:2px 6px 2px 0;cursor:pointer;white-space:nowrap">'
        + '<input type="checkbox" class="center-cb" data-uidx="' + i + '" value="' + allCenters[c].id + '"'
        + (checked ? ' checked' : '')
        + '> ' + allCenters[c].label
        + '</label> ';
    }
    html += '</td>';

    html += '<td style="padding:8px 12px">';
    html += '<button class="primary-btn" style="padding:4px 12px;font-size:12px" onclick="saveUserCenters(' + i + ',\'' + esc(u.user_id) + '\')">保存</button>';
    html += '</td>';
    html += '</tr>';
  }

  html += '</tbody></table></div>';

  if (users.length === 0) {
    html = '<div class="empty-state">暂无用户</div>';
  }

  document.getElementById('userTableArea').innerHTML = html;
}

function saveUserCenters(idx, userId) {
  var checkboxes = document.querySelectorAll('.center-cb[data-uidx="' + idx + '"]:checked');
  var centers = [];
  for (var i = 0; i < checkboxes.length; i++) {
    centers.push(checkboxes[i].value);
  }

  // 不允许取消所有勾选
  if (centers.length === 0) {
    showToast('至少勾选一个中心（没有就选「公共」）', 'error');
    return;
  }

  var formData = new FormData();
  formData.append('user_id', userId);
  formData.append('centers', JSON.stringify(centers));
  formData.append('password', getPw());

  fetch('/admin/users/update-centers', { method: 'POST', body: formData })
    .then(function(r) {
      if (!r.ok) throw new Error('保存失败: ' + r.status);
      return r.json();
    })
    .then(function(data) {
      showToast('✅ 已更新 ' + (userId.slice(0, 16)) + ' 的归属中心', 'success');
    })
    .catch(function(err) {
      showToast('❌ ' + err.message, 'error');
    });
}

function filterUserTable() {
  var input = document.getElementById('userSearchInput').value.toLowerCase();
  var rows = document.querySelector('#userTableArea table tbody tr');
  if (!rows) return;
  // Re-fetch with filter (simple approach: just reload)
  loadUsers();
}
</script>
</body>
</html>
"""
