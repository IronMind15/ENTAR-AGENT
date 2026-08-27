# -*- coding: utf-8 -*-
"""v1.11.9 遗留：清理 Chroma standards 确认重复块（988 块）

删除前打印清单与数量，实际删除由本脚本执行（用户已授权确认重复即删）。
删除清单依据 scripts 探针对比：
  - EN62109 / GBT16935 / GB/T 34120：uuid 版与非 uuid 版 100% 相同（纯重复）
  - CQC 3310：uuid 版覆盖非 uuid 版 99.9%，保留 uuid 版删非 uuid 版
不确认重复的组（EN 50178、IEC 60664-1 vs I60664-1E2、CNCA vs CQC）不在本脚本内。
"""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from scripts.doc_mgr.storage import ChromaStore

store = ChromaStore()
raw = store.get_raw('standards')
ids, metas = raw['ids'], raw['metadatas']

# 删除清单：{std_id: {'del': [file_name...], 'keep': [file_name...]}}
plan = {
    'EN62109': {
        'del': ['EN 62109-1.pdf-eccfc270-275f-45a2-be36-51ac44e0107a'],
        'keep': ['EN 62109-1.pdf'],
    },
    'GBT16935': {
        'del': ['GBT16935.1-2008.pdf-857b51f3-499f-4377-8c84-964b094f7d5a'],
        'keep': ['GBT16935.1-2008.pdf'],
    },
    '电化学储能系统储能变流器技术要求 GB_T 34120-2023': {
        'del': ['电化学储能系统储能变流器技术要求 GB_T 34120-2023.pdf-45fe5794-1e2f-4075-82cc-dc5d9997657a'],
        'keep': ['电化学储能系统储能变流器技术要求 GB_T 34120-2023.pdf'],
    },
    'CQC 3310': {
        'del': ['62109光伏发电系统用储能变流器技术规范.pdf'],
        'keep': ['62109光伏发电系统用储能变流器技术规范.pdf-ddb8a909-8123-4f72-9ace-3fa282ba923c'],
    },
}

del_ids, del_summary = [], []
for sid, p in plan.items():
    for fn in p['del']:
        cur = [mid for i, mid in enumerate(ids)
               if (metas[i] or {}).get('std_id') == sid and (metas[i] or {}).get('file_name') == fn]
        del_ids.extend(cur)
        del_summary.append((sid, fn, len(cur)))

print("=== 删除清单 ===")
for sid, fn, n in del_summary:
    print(f"  删 {sid}: {fn[:50]}... = {n} 块")
print(f"  待删合计: {len(del_ids)} 块")

# 安全校验：待删 ids 必须存在于库中
existing = set(ids)
missing = [i for i in del_ids if i not in existing]
if missing:
    print(f"!! 有 {len(missing)} 个 id 不在库中，中止")
    sys.exit(1)

# 备份清单到项目 data/ 下（记录本次清理）
# v1.13.4：相对路径改绝对（此前依赖运行 CWD，换目录执行会写丢）
_cleanup_log = os.path.join(_PROJECT_ROOT, "data", "dup_cleanup_v1.11.9.json")
with open(_cleanup_log, 'w', encoding='utf-8') as f:
    json.dump({'deleted_ids': del_ids, 'plan': del_summary}, f, ensure_ascii=False, indent=1)
print(f"  清单已备份到 {_cleanup_log}")

# 执行删除
before = len(ids)
n = store.delete('standards', ids=del_ids)
raw2 = store.get_raw('standards')
after = len(raw2['ids'])
visible = store.count('standards')
print("\n=== 删除结果 ===")
print(f"  删除返回: {n} | 原始块数 {before} → {after}（count(可见)={visible}）")
print(f"  净减: {before - after} 块")
