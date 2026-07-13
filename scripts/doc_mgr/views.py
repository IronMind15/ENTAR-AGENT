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
    --bg: #f0f4f8; --card: #ffffff; --text: #1e293b;
    --text-secondary: #64748b; --primary: #2563eb;
    --primary-light: #eff6ff; --primary-dark: #1d4ed8;
    --border: #e2e8f0; --shadow: 0 1px 3px rgba(0,0,0,0.08);
    --shadow-lg: 0 4px 20px rgba(0,0,0,0.08);
    --success: #10b981; --warning: #f59e0b; --danger: #ef4444;
    --hover: #f1f5f8; --scrollbar: #cbd5e1;
  }
  [data-theme="dark"] {
    --bg: #0f172a; --card: #1e293b; --text: #e2e8f0;
    --text-secondary: #94a3b8; --primary: #3b82f6;
    --primary-light: #1e293b; --primary-dark: #2563eb;
    --border: #334155; --shadow: 0 1px 3px rgba(0,0,0,0.3);
    --shadow-lg: 0 4px 20px rgba(0,0,0,0.4);
    --success: #34d399; --warning: #fbbf24; --danger: #f87171;
    --hover: #334155; --scrollbar: #475569;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; padding: 20px;
    transition: background .3s, color .3s;
  }
  .container { max-width: 1000px; margin: 0 auto; }
  .header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 20px;
  }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header h1 small { font-size: 13px; font-weight: 400; color: var(--text-secondary); }
  .header-actions { display: flex; gap: 8px; }
  .header-actions button {
    padding: 8px 16px; border: none; border-radius: 8px;
    background: var(--card); color: var(--text-secondary);
    cursor: pointer; font-size: 13px; box-shadow: var(--shadow);
    transition: all .2s;
  }
  .header-actions button:hover { background: var(--hover); color: var(--text); }
  .header-actions .back-btn {
    background: var(--primary); color: #fff;
  }
  .header-actions .back-btn:hover { background: var(--primary-dark); }
  .tabs {
    display: flex; gap: 2px; margin-bottom: 20px;
    background: var(--card); border-radius: 12px; overflow: hidden;
    box-shadow: var(--shadow);
  }
  .tabs button {
    flex: 1; padding: 12px 16px; border: none; background: transparent;
    color: var(--text-secondary); cursor: pointer; font-size: 14px;
    font-weight: 500; transition: all .2s;
  }
  .tabs button:hover { background: var(--hover); }
  .tabs button.active { background: var(--primary); color: #fff; }
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

  @media (max-width: 640px) {
    body { padding: 12px; }
    .tabs button { font-size: 12px; padding: 10px 8px; }
    .search-bar { flex-wrap: wrap; }
    .search-bar select { min-width: 100%; }
    .doc-item { flex-direction: column; align-items: flex-start; }
    .doc-item .doc-actions { margin-top: 8px; }
  }
</style>
</head>
<body>

<div class="container">
  <!-- Header -->
  <div class="header">
    <div>
      <h1>恩特小助手 <small>文档管理</small></h1>
    </div>
    <div class="header-actions">
      <button onclick="toggleTheme()" id="themeBtn" title="切换暗黑模式">🌙</button>
      <button onclick="location.href='/'" class="back-btn">← 返回聊天</button>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <button class="active" onclick="switchTab('docs', this)">📚 文档列表</button>
    <button onclick="switchTab('upload', this)">📤 上传文件</button>
    <button onclick="switchTab('search', this)">🔍 搜索测试</button>
  </div>

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
        </select>
        <p id="uploadTypeHint" style="font-size:12px;color:var(--text-secondary);margin-top:4px">📋 标准文档：上传 PDF 文件，自动切块入库到标准知识库</p>
      </div>
      <div class="drop-zone" id="dropZone">
        <div class="icon">📄</div>
        <p><strong>点击选择</strong> 或拖拽文件到此处</p>
        <p style="font-size:12px;margin-top:4px" id="uploadFileTypes">支持 .pdf</p>
        <input type="file" id="fileInput" accept=".pdf,.xlsx,.xls" style="display:none">
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

// ===== Tabs =====
function switchTab(name, el) {
  document.querySelectorAll('.tabs button').forEach(function(b) {
    b.classList.remove('active');
  });
  (el || event.target).classList.add('active');
  document.querySelectorAll('.tab-content').forEach(function(t) {
    t.classList.remove('active');
  });
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'docs') loadDocs();
  if (name === 'search') loadSearchCollections();
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

  fetch('/admin/docs')
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

  fetch('/admin/docs?collection=' + encodeURIComponent(collection) + '&file_name=' + encodeURIComponent(fileName), {
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
  fill.style.width = '20%';
  fill.classList.remove('done');
  text.textContent = '📤 上传中... (' + file.name + ')';
  result.classList.remove('show');

  var formData = new FormData();
  formData.append('file', file);
  formData.append('collection', document.getElementById('uploadCollection').value);

  // 上传完成后切换为"处理中"状态（大文件处理可能需要 30s+）
  var processingTimer = setTimeout(function() {
    fill.style.width = '50%';
    text.textContent = '⚙️ 正在处理，请耐心等待... (嵌入向量计算)';
  }, 2000);

  fetch('/admin/upload', { method: 'POST', body: formData })
    .then(function(r) {
      clearTimeout(processingTimer);
      fill.style.width = '80%';
      text.textContent = '✅ 处理完成，整理结果...';
      return r.json();
    })
    .then(function(data) {
      fill.style.width = '100%';
      fill.classList.add('done');

      if (data.status === 'done') {
        result.className = 'upload-result success show';
        result.innerHTML = '✅ 处理完成！<br>'
          + '文件: ' + esc(data.file_name) + '<br>'
          + '入库: ' + data.chunk_count + ' 块 → ' + esc(data.collection)
          + (data.std_id ? '<br>标准: ' + esc(data.std_id) : '');
        text.textContent = '完成！共 ' + data.chunk_count + ' 块';
        showToast('上传成功: ' + data.chunk_count + ' 块', 'success');
        loadDocs();
        loadSearchCollections();
      } else if (data.status === 'ocr_needed') {
        result.className = 'upload-result show';
        result.style.cssText = 'display:block;padding:12px 16px;border-radius:8px;font-size:13px;background:#fffbeb;color:#92400e;';
        result.innerHTML = '⚠️ 扫描型 PDF，需要 OCR 处理';
        text.textContent = '需 OCR 处理';
      } else {
        result.className = 'upload-result error show';
        result.innerHTML = '❌ ' + (data.message || '处理失败');
        text.textContent = '处理失败';
      }
    })
    .catch(function(err) {
      clearTimeout(processingTimer);
      fill.style.width = '100%';
      result.className = 'upload-result error show';
      result.innerHTML = '❌ 上传异常: ' + esc(err.message);
      text.textContent = '上传异常';
    });
}

// ===== Tab 3: Search =====
function loadSearchCollections() {
  fetch('/admin/collections')
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

  var url = '/admin/search?q=' + encodeURIComponent(q) + '&limit=10';
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
</script>
</body>
</html>
"""
