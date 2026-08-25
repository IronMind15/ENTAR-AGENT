"""
MinerU 文档提取 - Python API 方式
扫描 PDF → 上传到 MinerU → 轮询结果 → 下载 Markdown/ZIP

用法：
    python scripts/mineru_extract.py <pdf文件> [输出目录]
    python scripts/mineru_extract.py --batch <输入目录> [输出目录]

Token 配置：
    先运行 mineru-open-api auth 登录，token 会自动保存到 ~/.mineru/config.yaml
"""
import os
import sys
import io
import json
import time
import argparse
import logging
import requests

# 全局直连 Session：MinerU 是国内 API，强制直连，不跟随系统/环境代理（避免 Clash 劫持）
_NET_SESSION = requests.Session()
_NET_SESSION.trust_env = False

from pathlib import Path
from scripts.paths import STANDARDS_DIR

# 修复 Windows 控制台编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mineru_extract")

# 配置
MINERU_API_BASE = "https://mineru.net/api/v4"
# 默认输出目录：项目 data/standards/mineru_output/
DEFAULT_OUTPUT_DIR = STANDARDS_DIR / "mineru_output"

# 进度上报（导入 task_manager，兼容无任务上下文的情况）
try:
    from scripts.doc_mgr.task_manager import report_progress as _report_progress
except ImportError:
    _report_progress = lambda step, progress, msg: None


def load_token() -> str | None:
    """读取 MinerU Token（优先级：env > /admin 系统设置 > local_config > config.yaml）"""
    # 0. 统一配置链路：环境变量 > admin_config.json（/admin「系统设置」网页填的）
    #    > local_config.py（由 scripts.config 汇总读取）。网页填的覆盖本地，
    #    接手人无需改代码配 MinerU 密钥。
    try:
        from scripts.config import MINERU_TOKEN as _cfg_token
        if _cfg_token:
            return _cfg_token
    except Exception:
        pass

    # 1. 读 ~/.mineru/config.yaml（mineru-open-api auth 生成的）
    config_path = Path.home() / ".mineru" / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            for line in f:
                if line.startswith("token:"):
                    return line.split(":", 1)[1].strip()

    # 3. 看环境变量
    env_token = os.environ.get("MINERU_TOKEN", "")
    if env_token:
        return env_token

    return None


# 模块加载时自动读取 token
MINERU_TOKEN = load_token()


def get_upload_urls(files: list, model_version: str = "vlm") -> dict:
    """第一步：申请上传 URL（官方示例方式）"""
    url = f"{MINERU_API_BASE}/file-urls/batch"
    headers = {
        "Authorization": f"Bearer {MINERU_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "files": [{"name": f} for f in files],
        "model_version": model_version
    }

    resp = _NET_SESSION.post(url, headers=headers, json=data, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    if result["code"] == 0:
        return result["data"]
    raise Exception(f"申请上传 URL 失败: {result['msg']}")


def upload_file(upload_url: str, file_path: str) -> None:
    """第二步：上传文件到 OSS（官方示例方式）

    注意：不传 Content-Type，否则 OSS 签名会校验失败。
    """
    with open(file_path, 'rb') as f:
        resp = _NET_SESSION.put(upload_url, data=f)
    if resp.status_code == 200:
        logger.info(f"  ✅ 上传成功")
    else:
        raise Exception(f"上传失败: {resp.status_code} - {resp.text[:200]}")


def get_task_status(batch_id: str) -> dict:
    """第三步：查询任务状态（官方示例方式）"""
    url = f"{MINERU_API_BASE}/extract-results/batch/{batch_id}"
    headers = {"Authorization": f"Bearer {MINERU_TOKEN}"}

    resp = _NET_SESSION.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    result = resp.json()

    if result["code"] == 0:
        return result["data"]
    raise Exception(f"查询状态失败: {result['msg']}")


def download_result(download_url: str, output_path: str) -> None:
    """下载提取结果（ZIP 或 Markdown）"""
    resp = _NET_SESSION.get(download_url, stream=True, timeout=(30, 120))
    resp.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def extract_pdf(file_path: str, output_dir: str, model: str = "vlm") -> str:
    """
    提取单个 PDF 文件

    流程：申请上传URL → 上传文件 → 轮询结果 → 下载

    Args:
        file_path: PDF 文件路径
        output_dir: 输出目录
        model: 模型版本 (vlm/pipeline)

    Returns:
        下载的文件路径
    """
    file_path = os.path.abspath(file_path)
    file_name = os.path.basename(file_path)

    logger.info(f"\n📄 正在提取: {file_name}")

    # 1. 获取上传 URL
    _report_progress("mineru_upload", 15, f"获取 MinerU 上传地址...")
    logger.info("  → 获取上传 URL...")
    result = get_upload_urls([file_name], model_version=model)

    batch_id = result["batch_id"]
    urls = result["file_urls"]
    headers = result.get("headers", [{}])
    logger.info(f"  → batch_id: {batch_id}")

    # 2. 上传文件到阿里云 OSS（注意：不能传 Content-Type，否则 OSS 签名校验会失败）
    _report_progress("mineru_upload", 20, f"上传 {file_name} 到 MinerU...")
    logger.info("  → 上传文件...")
    for i in range(len(urls)):
        # 自动重试：OSS 偶发超时/断连时自动恢复
        MAX_RETRIES = 3
        for attempt in range(MAX_RETRIES):
            try:
                with open(file_path, 'rb') as f:
                    res_upload = _NET_SESSION.put(urls[i], data=f, timeout=180)
                if res_upload.status_code == 200:
                    logger.info(f"  ✅ 上传成功")
                    break  # 成功，跳出重试循环
                else:
                    raise Exception(f"HTTP {res_upload.status_code}: {res_upload.text[:200]}")
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 5
                    logger.warning(f"  ⚠️ OSS 上传异常（第{attempt+1}次），{wait}s 后重试: {e}")
                    time.sleep(wait)
                else:
                    raise Exception(f"上传失败（重试{MAX_RETRIES}次均失败）: {e}")

    # 3. 等待处理完成（每 60 秒轮询一次，最长等 30 分钟）
    MAX_WAIT = 1800       # 总超时 30 分钟
    POLL_INTERVAL = 60    # 轮询间隔 60 秒
    _report_progress("mineru_waiting", 30, f"已上传到 MinerU，等待转换结果（最长 {MAX_WAIT // 60} 分钟）...")
    logger.info(f"  → 等待处理...（每 {POLL_INTERVAL}s 轮询，最长 {MAX_WAIT // 60} 分钟）")
    start_time = time.time()
    first_check = True

    while True:
        # 每次轮询前先等待（首次和后续均为 POLL_INTERVAL）
        if first_check:
            logger.info(f"  → 等 {POLL_INTERVAL // 60} 分钟后首次检查...")
            first_check = False
        else:
            elapsed_before = int(time.time() - start_time)
            logger.info(f"  → 再等 {POLL_INTERVAL // 60} 分钟...（已等 {elapsed_before}s / {MAX_WAIT // 60}分钟）")

        time.sleep(POLL_INTERVAL)

        # 检查是否超过总超时
        elapsed = int(time.time() - start_time)
        if elapsed >= MAX_WAIT:
            msg = (f"MinerU 处理超时：已等待 {elapsed // 60} 分钟"
                   f"（上限 {MAX_WAIT // 60} 分钟），放弃等待")
            logger.error(f"  ⏰ {msg}")
            _report_progress("timeout", 0, msg)
            raise TimeoutError(msg)

        # 查询处理状态
        # 注意：API 返回结构为 data.extract_result[].state，不是 data.state
        status = get_task_status(batch_id)
        results_list = status.get("extract_result") or status.get("extract_results") or []
        if results_list:
            file_status = results_list[0]
            state = file_status.get("state", "unknown")
            err_msg = file_status.get("err_msg", "")
        else:
            state = "unknown"
            err_msg = ""
        elapsed = int(time.time() - start_time)
        pct = min(30 + int(elapsed / MAX_WAIT * 30), 55)  # 30%→55%
        _report_progress("mineru_waiting", pct, f"MinerU 转换中...（已等 {elapsed // 60} 分钟）")
        logger.info(f"  → 状态: {state}（已等 {elapsed}s）")

        if state == "done":
            _report_progress("mineru_download", 50, "MinerU 转换完成，下载结果...")
            logger.info(f"  ✅ MinerU 处理完成（耗时 {elapsed}s）")
            break
        if state == "failed":
            err_msg = err_msg or status.get("msg", "未知错误")
            logger.error(f"  ❌ MinerU 处理失败: {err_msg}")
            _report_progress("error", 0, f"MinerU 处理失败: {err_msg}")
            raise Exception(f"MinerU 处理失败: {err_msg}")

    # 4. 获取结果
    logger.info("  → 获取结果...")
    result = get_task_status(batch_id)

    # 5. 下载结果（注意：API 返回的是 extract_result，不是 extract_results）
    extract_results = result.get("extract_result", result.get("extract_results", []))
    for item in extract_results:
        # 优先下载 ZIP
        zip_url = item.get("full_zip_url") or item.get("zip_url")
        if zip_url:
            zip_path = os.path.join(output_dir, f"{Path(file_name).stem}.zip")
            download_result(zip_url, zip_path)
            logger.info(f"  ✅ 已保存: {zip_path}")
            return zip_path

        # 或者下载 Markdown
        md_content = item.get("markdown", "")
        if md_content:
            md_path = os.path.join(output_dir, f"{Path(file_name).stem}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            logger.info(f"  ✅ 已保存: {md_path}")
            return md_path

    # 兜底：尝试直接从 result 获取
    zip_url = result.get("full_zip_url") or result.get("zip_url")
    if zip_url:
        zip_path = os.path.join(output_dir, f"{Path(file_name).stem}.zip")
        download_result(zip_url, zip_path)
        logger.info(f"  ✅ 已保存: {zip_path}")
        return zip_path

    raise Exception(f"未获取到结果")


def batch_extract(input_dir: str, output_dir: str, model: str = "vlm") -> list:
    """
    批量提取目录下所有 PDF

    Args:
        input_dir: 输入目录
        output_dir: 输出目录
        model: 模型版本

    Returns:
        提取结果列表
    """
    os.makedirs(output_dir, exist_ok=True)

    pdf_files = list(Path(input_dir).glob("*.pdf"))
    if not pdf_files:
        logger.error("❌ 未找到 PDF 文件")
        return []

    logger.info(f"📚 找到 {len(pdf_files)} 个 PDF 文件")

    results = []
    for pdf_file in pdf_files:
        try:
            result = extract_pdf(str(pdf_file), output_dir, model)
            results.append({"file": str(pdf_file), "status": "success", "output": result})
        except Exception as e:
            logger.error(f"  ❌ 提取失败: {e}")
            results.append({"file": str(pdf_file), "status": "failed", "error": str(e)})

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MinerU 文档提取工具")
    parser.add_argument("file", nargs="?", help="PDF 文件路径")
    parser.add_argument("output", nargs="?", help="输出目录")
    parser.add_argument("--batch", action="store_true", help="批量模式，处理目录下所有 PDF")
    parser.add_argument("--input", help="批量模式的输入目录")
    args = parser.parse_args()

    # 加载 token
    token = load_token()
    if not token:
        logger.error("❌ 未找到 Token，请先运行: mineru-open-api auth")
        sys.exit(1)

    logger.info(f"🔑 Token: {token[:10]}...")

    # 批量模式
    if args.batch:
        input_dir = args.input or "."
        output_dir = args.output or str(DEFAULT_OUTPUT_DIR)
        results = batch_extract(input_dir, output_dir)

        success = sum(1 for r in results if r["status"] == "success")
        logger.info(f"\n📊 完成: {success}/{len(results)} 成功")
        sys.exit(0)

    # 单文件模式
    if not args.file:
        parser.print_help()
        sys.exit(1)

    output_dir = args.output or str(DEFAULT_OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)

    result = extract_pdf(args.file, output_dir)
    logger.info(f"\n✅ 提取完成: {result}")
