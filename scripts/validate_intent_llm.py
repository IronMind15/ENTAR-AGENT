"""
第 0 步验证脚本：LLM 意图分类 vs 正则基线（v1.12.0 工具板块化重构前置）

目的：验证「bot 正则 + 技能层 match() 改用 LLM 托管做意图识别」的效果与延迟，
结论写入 docs/20260813-意图识别验证报告.md 供用户拍板是否扩大范围。

方法：
  1. 70 条样本（6 类 60 条 + 边界句 10 条），每条标 ground truth 顶层意图
  2. 正则基线：monkeypatch 掉看板 LLM 复核（_maybe_llm_verify → fallback），
     走现有技能链 get_matched_skill + bot 正则，得到「纯正则」顶层分类
  3. LLM 分类：DeepSeek 单轮意图分类（与现有 _llm_verify_subscription 同款
     调用：skills.agent.call_deepseek），逐条计时
  4. 对比：每类准确率 / 边界句识别 / 延迟 p50/p95

本脚本只做验证，不修改任何业务代码。
"""

import os
import re
import sys
import time
import statistics

_PARENT = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_PARENT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ===== 纯正则基线：跳过看板 LLM 复核（create/change_recipients 直接用 fallback 意图）=====
import scripts.dashboard.subscription_commands as sc  # noqa: E402
sc._maybe_llm_verify = lambda text, intent: intent

from scripts.skills import dingtalk_bot as bot  # noqa: E402
from scripts.skills import get_matched_skill  # noqa: E402
from scripts.skills.agent import _call_deepseek  # noqa: E402

# ===== 顶层意图清单（正则与 LLM 共用）=====
TOP_LABELS = ["fault_exact", "standard_exact", "pcb_calc", "kanban",
              "kanban_push", "file_cmd", "chat"]

# ===== 样本集： (文本, ground_truth 顶层意图, 备注) =====
SAMPLES = [
    # ── ① 故障码精确（8）──────────────────────────────
    ("d4-1", "fault_exact", "故障码精确"),
    ("查询 d4-1 故障", "fault_exact", "故障码精确"),
    ("df-8 是什么", "fault_exact", "故障码精确"),
    ("d10-1 是什么故障", "fault_exact", "故障码精确"),
    ("外部急停信号闭合 d4-1", "fault_exact", "故障码精确"),
    ("报错 d12-3", "fault_exact", "故障码精确"),
    ("d4-1 怎么回事", "fault_exact", "故障码精确"),
    ("故障代码 df-8", "fault_exact", "故障码精确"),
    # ── ② 标准号精确（8）──────────────────────────────
    ("GB/T 34133-2023", "standard_exact", "标准号精确"),
    ("查一下 GB/T 16935.1-2008", "standard_exact", "标准号精确"),
    ("EN50178 标准", "standard_exact", "标准号精确"),
    ("IEC 60664-1 是什么", "standard_exact", "标准号精确"),
    ("GB/T 34133 的内容", "standard_exact", "标准号精确"),
    ("UL 60950-1", "standard_exact", "标准号精确"),
    ("标准 GB/T 34120-2017 有哪些要求", "standard_exact", "标准号精确"),
    ("CQC 3310 标准是什么", "standard_exact", "标准号精确"),
    # ── ③ PCB 计算（10）───────────────────────────────
    ("走线 10A 用多宽", "pcb_calc", "PCB 计算"),
    ("2oz 铜厚 3A 走线宽度", "pcb_calc", "PCB 计算"),
    ("计算铜排载流", "pcb_calc", "PCB 计算"),
    ("PCB 走线压降怎么算", "pcb_calc", "PCB 计算"),
    ("1mm 线宽能过多少安", "pcb_calc", "PCB 计算"),
    ("过孔载流计算", "pcb_calc", "PCB 计算"),
    ("50 欧姆微带线阻抗宽度", "pcb_calc", "PCB 计算"),
    ("led 限流电阻怎么选", "pcb_calc", "PCB 计算"),
    ("计算趋肤深度", "pcb_calc", "PCB 计算"),
    ("三相铜排载流量", "pcb_calc", "PCB 计算"),
    # ── ④ 看板订阅管理（16）───────────────────────────
    ("帮我推个看板", "kanban", "看板-开通"),
    ("做每日看板", "kanban", "看板-开通"),
    ("开通看板订阅", "kanban", "看板-开通"),
    ("停掉看板", "kanban", "看板-停用"),
    ("把看板停掉", "kanban", "看板-停用"),
    ("取消看板推送", "kanban", "看板-停用"),
    ("删除看板订阅", "kanban", "看板-删除"),
    ("删掉我的看板", "kanban", "看板-删除"),
    ("我的看板几点推送", "kanban", "看板-查询"),
    ("看板设置是什么", "kanban", "看板-查询"),
    ("改看板时间到10点", "kanban", "看板-改时间"),
    ("每周一和周五推看板", "kanban", "看板-改频率"),
    ("也推给张工", "kanban", "看板-改接收人"),
    ("不要推给李四了", "kanban", "看板-改接收人"),
    ("看板用周报模板", "kanban", "看板-模板"),
    ("有哪些看板模板", "kanban", "看板-模板"),
    # ── ⑤ bot 文件命令（10）───────────────────────────
    ("帮我学习", "file_cmd", "文件-学习"),
    ("学习这个文件", "file_cmd", "文件-学习"),
    ("入库", "file_cmd", "文件-学习确认"),
    ("把这个文档学到产品手册", "file_cmd", "文件-学指定库"),
    ("我的文件", "file_cmd", "文件-我的文件"),
    ("查看我的上传", "file_cmd", "文件-我的文件"),
    ("删除学习 1", "file_cmd", "文件-删除"),
    ("重新学习 1", "file_cmd", "文件-重学"),
    ("ENTARBOSS", "file_cmd", "文件-管理员"),
    ("查看全部文件", "file_cmd", "文件-管理员"),
    # ── ⑥ 普通聊天（8）────────────────────────────────
    ("你好", "chat", "普通聊天"),
    ("今天天气怎么样", "chat", "普通聊天"),
    ("帮我写一份周报", "chat", "普通聊天"),
    ("谢谢", "chat", "普通聊天"),
    ("什么是人工智能", "chat", "普通聊天"),
    ("随便问问", "chat", "普通聊天"),
    ("xxx", "chat", "普通聊天"),
    ("你好呀，早上好", "chat", "普通聊天"),
    # ── ⑦ 边界句 / 正则盲区（10）──────────────────────
    ("项目进展怎么样", "kanban_push", "盲区-实时查看板"),
    ("我的看板进展如何", "kanban_push", "盲区-实时查看板"),
    ("现在就推看板", "kanban_push", "盲区-立即推送"),
    ("这跟看板功能有什么关系", "chat", "盲区-反问放行"),
    ("我已经粘贴过了重新发给了你", "chat", "盲区-非订阅"),
    ("看板推送是不是要收费", "chat", "盲区-疑问放行"),
    ("不要推给别人", "kanban", "盲区-指代词→set_self"),
    ("我想做一个每日看板", "kanban", "盲区-无文档词创建"),
    ("这个文档做好看板", "kanban", "盲区-doc_create"),
    ("把文档学习入库", "file_cmd", "盲区-学习非看板"),
]


# ===== 正则基线 =====
def regex_top(text: str) -> str:
    """模拟当前系统纯正则路由（bot 文件命令 → 技能链）的顶层意图"""
    t = (text or "").strip()
    # 1. bot 文件命令（dingtalk_bot 正则）
    if (bot._is_learn_command(t) or bot._LEARN_TO_KB_RE.match(t)
            or bot._MY_FILES_RE.match(t) or bot._DELETE_RE.match(t)
            or bot._RELEARN_RE.match(t) or bot._ADMIN_ENTER_RE.match(t)
            or bot._ADMIN_EXIT_RE.match(t) or bot._ADMIN_LIST_ALL_RE.match(t)):
        return "file_cmd"
    # 2. 技能链（按 priority：故障 100 → PCB 90 → 看板 80 → 标准 70 → agent 50）
    t0 = time.perf_counter()
    skill = get_matched_skill(t, "")
    dt = (time.perf_counter() - t0) * 1000
    name = skill.name if skill else ""
    if name == "故障查询":
        return "fault_exact"
    if name == "PCB设计计算":
        return "pcb_calc"
    if name == "标准查询":
        return "standard_exact"
    if name == "dashboard":
        return "kanban"
    return "chat"


# ===== LLM 分类 =====
_LLM_SYSTEM = (
    "你是恩特小助手（企业内部 AI 助手）的消息意图分类器。"
    "判断这条钉钉消息属于哪类意图，只返回一个意图名（不要任何其他文字、不要解释、不要标点）：\n"
    "- fault_exact：查询 PCS 故障代码（如 d4-1、df-8 这类代码，或故障现象描述）\n"
    "- standard_exact：查询标准文档编号（如 GB/T 34133、IEC 60664-1 这类标准号）\n"
    "- pcb_calc：PCB/电路设计计算（走线宽度、载流、阻抗、铜排、限流电阻等）\n"
    "- kanban：管理每日看板订阅（开通/停用/删除/改时间/改频率/改接收人/看模板/用模板）\n"
    "- kanban_push：立即查看或推送每日看板（不是管理订阅，是现在就查一次/推一次）\n"
    "- file_cmd：上传文件相关命令（帮我学习/入库/我的文件/删除学习/重新学习/管理员模式）\n"
    "- chat：以上都不是的普通聊天或询问"
)


def llm_top(text: str) -> tuple[str, float]:
    """DeepSeek 单轮分类，返回 (意图名, 耗时秒)；失败返回 ("", 耗时)

    与现有看板 _llm_verify_subscription 同款调用栈（skills.agent），
    但关 thinking 省 token/延迟（分类任务无需推理，V4 关思考实测更稳）。
    """
    t0 = time.time()
    try:
        resp = _call_deepseek(
            [{"role": "system", "content": _LLM_SYSTEM},
             {"role": "user", "content": f"消息：{text[:200]}"}],
            max_tokens=50, thinking=False,
        )
        raw = (resp or {}).get("content") or ""
    except Exception:
        raw = ""
    return raw.strip(), time.time() - t0


def _normalize(raw: str) -> str:
    """LLM 返回归一化：去掉可能的前缀/标点，命中 TOP_LABELS 任一即返回"""
    if not raw:
        return ""
    s = raw.strip().strip("`\"'。.!！，,、")
    for lab in TOP_LABELS:
        if lab in s:
            return lab
    # 常见近义归一（LLM 可能把细分写进返回）
    for alias, lab in [("kanban_create", "kanban"), ("kanban_stop", "kanban"),
                       ("kanban_delete", "kanban"), ("kanban_query", "kanban"),
                       ("kanban_change", "kanban"), ("kanban_template", "kanban"),
                       ("file_manage", "file_cmd"), ("learn", "file_cmd")]:
        if alias in s:
            return lab
    return s


def run_regex() -> dict:
    """正则基线全量判定，返回 {label: {"hit": n, "total": n, "misses": [...]}}"""
    res = {lab: {"hit": 0, "total": 0, "misses": []} for lab in TOP_LABELS}
    for text, truth, _ in SAMPLES:
        got = regex_top(text)
        res[truth]["total"] += 1
        if got == truth:
            res[truth]["hit"] += 1
        else:
            res[truth]["misses"].append((text, truth, got))
    return res


def run_llm() -> dict:
    """LLM 全量分类，返回 {label: {"hit","total","misses"}} + 延迟统计"""
    res = {lab: {"hit": 0, "total": 0, "misses": []} for lab in TOP_LABELS}
    lat = []
    print("[验证] LLM 逐条分类中...")
    for i, (text, truth, note) in enumerate(SAMPLES, 1):
        got, dt = llm_top(text)
        lat.append(dt)
        res[truth]["total"] += 1
        if got == truth:
            res[truth]["hit"] += 1
        else:
            res[truth]["misses"].append((text, truth, got or "(无返回)"))
        print(f"  {i:2d}/{len(SAMPLES)} [{truth:>14}] {got or '-':>14} "
              f"{dt:5.1f}s  {text[:30]}")
    lat_sorted = sorted(lat)
    n = len(lat_sorted)
    return res, {
        "count": n,
        "p50": lat_sorted[int(n * 0.50)],
        "p90": lat_sorted[int(n * 0.90)],
        "p95": lat_sorted[min(n - 1, int(n * 0.95))],
        "max": lat_sorted[-1],
    }


def _fmt(res: dict) -> str:
    lines = []
    total_hit = total = 0
    for lab in TOP_LABELS:
        r = res[lab]
        acc = (r["hit"] / r["total"] * 100) if r["total"] else 0
        total_hit += r["hit"]
        total += r["total"]
        lines.append(f"  {lab:>14}: {r['hit']}/{r['total']} ({acc:.0f}%)")
        for text, truth, got in r["misses"]:
            lines.append(f"      ✗ {text!r} → 判为 {got!r}（期望 {truth}）")
    acc = total_hit / total * 100 if total else 0
    lines.insert(0, f"  合计: {total_hit}/{total} ({acc:.1f}%)")
    return "\n".join(lines)


def main() -> None:
    print("=" * 70)
    print("第 0 步：LLM 意图分类 vs 正则基线")
    print(f"样本数：{len(SAMPLES)}（6 类 60 条 + 边界句 10 条）")
    print("=" * 70)

    # 正则基线（顺带测延迟）
    print("\n[1] 正则基线判定中...")
    reg_res = {lab: {"hit": 0, "total": 0, "misses": []} for lab in TOP_LABELS}
    reg_lat = []
    for text, truth, _ in SAMPLES:
        got = regex_top(text)
        reg_res[truth]["total"] += 1
        if got == truth:
            reg_res[truth]["hit"] += 1
        else:
            reg_res[truth]["misses"].append((text, truth, got))
        # 延迟：单独再跑一次计时
    for text, _, _ in SAMPLES:
        t0 = time.perf_counter()
        regex_top(text)
        reg_lat.append((time.perf_counter() - t0) * 1000)

    print("\n正则基线结果：")
    print(_fmt(reg_res))
    reg_s = sorted(reg_lat)
    n = len(reg_s)
    print(f"正则延迟: p50={reg_s[n//2]:.2f}ms  p95={reg_s[min(n-1,int(n*0.95))]:.2f}ms")

    # LLM 分类
    print("\n[2] LLM 分类中（约 1-3 分钟）...")
    llm_res, llm_lat = run_llm()
    print("\nLLM 分类结果：")
    print(_fmt(llm_res))
    print(f"LLM 延迟: p50={llm_lat['p50']:.1f}s  p90={llm_lat['p90']:.1f}s  "
          f"p95={llm_lat['p95']:.1f}s  max={llm_lat['max']:.1f}s")

    # 边界句专项对比
    print("\n[3] 边界句（正则盲区）专项：")
    boundary = [s for s in SAMPLES if "盲区" in s[2]]
    b_regex = b_llm = 0
    for text, truth, note in boundary:
        rg = regex_top(text)
        lg, _ = llm_top(text)
        lg = _normalize(lg)
        if rg == truth:
            b_regex += 1
        if lg == truth:
            b_llm += 1
        print(f"  {note:>14} | 正则:{rg:>13} | LLM:{lg or '-':>13} | "
              f"期望:{truth:>12} | {text}")
    print(f"  边界句命中：正则 {b_regex}/{len(boundary)}，LLM {b_llm}/{len(boundary)}")

    # 写报告
    _write_report(reg_res, llm_res, llm_lat, b_regex, b_llm, len(boundary))
    print("\n[4] 报告已写入 docs/20260813-意图识别验证报告.md")


def _write_report(reg_res, llm_res, llm_lat, b_regex, b_llm, b_total):
    def _acc(res, lab):
        r = res[lab]
        return (r["hit"] / r["total"] * 100) if r["total"] else 0

    total_reg_hit = sum(reg_res[l]["hit"] for l in TOP_LABELS)
    total_llm_hit = sum(llm_res[l]["hit"] for l in TOP_LABELS)
    total_n = len(SAMPLES)

    rows = []
    for lab in TOP_LABELS:
        rows.append(f"| {lab} | {reg_res[lab]['hit']}/{reg_res[lab]['total']} "
                    f"({_acc(reg_res, lab):.0f}%) | "
                    f"{llm_res[lab]['hit']}/{llm_res[lab]['total']} "
                    f"({_acc(llm_res, lab):.0f}%) |")
    reg_miss = []
    for lab in TOP_LABELS:
        for text, truth, got in reg_res[lab]["misses"]:
            reg_miss.append(f"- `{text}` 期望 {truth}，正则判为 {got}")
    llm_miss = []
    for lab in TOP_LABELS:
        for text, truth, got in llm_res[lab]["misses"]:
            llm_miss.append(f"- `{text}` 期望 {truth}，LLM 判为 {got or '(无返回)'}")

    report = f"""# 意图识别验证报告：LLM 意图分类 vs 正则基线

> 日期：2026-08-13 ｜ 前置验证（v1.12.0 工具板块化重构 · 第 0 步）
> 脚本：`scripts/validate_intent_llm.py`（只读验证，不修改业务代码）
> 方法：70 条样本（6 类 60 条 + 边界句 10 条），正则走现有技能链（monkeypatch 掉看板 LLM 复核 = 纯正则），
> LLM 用 DeepSeek 单轮分类（同款调用 `skills.agent.call_deepseek`，与现有看板 `_llm_verify_subscription` 一致）。

## 一、总体结果

| 指标 | 正则基线 | LLM 分类 |
|---|---|---|
| 顶层准确率 | {total_reg_hit}/{total_n}（{total_reg_hit/total_n*100:.1f}%） | {total_llm_hit}/{total_n}（{total_llm_hit/total_n*100:.1f}%） |
| 延迟 p50 | 毫秒级（本地 re） | {llm_lat['p50']:.1f}s |
| 延迟 p95 | 毫秒级 | {llm_lat['p95']:.1f}s |

## 二、分项准确率

| 意图 | 正则 | LLM |
|---|---|---|
{chr(10).join(rows)}

## 三、边界句（正则盲区）专项

样本 {b_total} 条（实时查看板 / 立即推送 / 反问放行 / 疑问放行 / 指代词 / 无文档词创建等）。

- 正则命中：{b_regex}/{b_total}
- LLM 命中：{b_llm}/{b_total}

## 四、正则误判明细（正则判错而 ground truth 期望的）

{chr(10).join(reg_miss) if reg_miss else '（无）'}

## 五、LLM 误判明细

{chr(10).join(llm_miss) if llm_miss else '（无）'}

## 六、结论与建议

（由主模型根据上面数据补写——判定分支见计划 0.2）
"""
    with open(os.path.join(_PARENT, "..", "docs",
                           "20260813-意图识别验证报告.md"),
              "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
