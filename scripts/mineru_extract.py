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
import requests
from pathlib import Path

# 修复 Windows 控制台编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# 配置
MINERU_API_BASE = "https://mineru.net/api/v4"
# 默认输出目录：项目 data/standards/mineru_output/
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "standards" / "mineru_output"


def load_token() -> str | None:
    """从配置文件或环境变量读取 Token（优先 config.yaml）"""
    # 优先读 config.yaml（mineru-open-api auth 生成的）
    config_path = Path.home() / ".mineru" / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            for line in f:
                if line.startswith("token:"):
                    return line.split(":", 1)[1].strip()

    # 其次看环境变量
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

    resp = requests.post(url, headers=headers, json=data)
    resp.raise_for_status()
    result = resp.json()

    if result["code"] == 0:
        return result["data"]
    raise Exception(f"申请上传 URL 失败: {result['msg']}")


def upload_file(upload_url: str, file_path: str) -> None:
    """第二步：上传文件到 OSS（官方示例方式）"""
    with open(file_path, 'rb') as f:
        resp = requests.put(upload_url, data=f)
    if resp.status_code == 200:
        print(f"  ✅ 上传成功")
    else:
        raise Exception(f"上传失败: {resp.status_code} - {resp.text[:200]}")


def get_task_status(batch_id: str) -> dict:
    """第三步：查询任务状态（官方示例方式）"""
    url = f"{MINERU_API_BASE}/extract-results/batch/{batch_id}"
    headers = {"Authorization": f"Bearer {MINERU_TOKEN}"}

    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    result = resp.json()

    if result["code"] == 0:
        return result["data"]
    raise Exception(f"查询状态失败: {result['msg']}")


def download_result(download_url: str, output_path: str) -> None:
    """下载提取结果（ZIP 或 Markdown）"""
    resp = requests.get(download_url, stream=True)
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

    print(f"\n📄 正在提取: {file_name}")

    # 1. 获取上传 URL
    print("  → 获取上传 URL...")
    result = get_upload_urls([file_name], model_version=model)

    batch_id = result["batch_id"]
    urls = result["file_urls"]
    headers = result.get("headers", [{}])
    print(f"  → batch_id: {batch_id}")

    # 2. 上传文件（官方示例方式）
    print("  → 上传文件...")
    content_type = headers[0].get("Content-Type", "application/pdf") if headers else "application/pdf"
    for i in range(len(urls)):
        with open(file_path, 'rb') as f:
            res_upload = requests.put(urls[i], data=f, headers={"Content-Type": content_type})
            if res_upload.status_code == 200:
                print(f"  ✅ 上传成功")
            else:
                raise Exception(f"上传失败: {res_upload.status_code} - {res_upload.text[:200]}")

    # 3. 等待处理完成
    print("  → 等待处理...")
    max_wait = 300  # 最多等待5分钟
    start_time = time.time()

    while time.time() - start_time < max_wait:
        status = get_task_status(batch_id)
        state = status.get("state", "unknown")
        is_done = status.get("is_done", False)
        elapsed = int(time.time() - start_time)
        print(f"  → 状态: {state} ({elapsed}s)")

        if is_done or state == "done":
            break
        if state == "failed":
            raise Exception(f"处理失败: {status}")

        time.sleep(5)
    else:
        raise Exception(f"等待超时（{max_wait}s）")

    # 4. 获取结果
    print("  → 获取结果...")
    result = get_task_status(batch_id)

    # 5. 下载结果（注意：API 返回的是 extract_result，不是 extract_results）
    extract_results = result.get("extract_result", result.get("extract_results", []))
    for item in extract_results:
        # 优先下载 ZIP
        zip_url = item.get("full_zip_url") or item.get("zip_url")
        if zip_url:
            zip_path = os.path.join(output_dir, f"{Path(file_name).stem}.zip")
            download_result(zip_url, zip_path)
            print(f"  ✅ 已保存: {zip_path}")
            return zip_path

        # 或者下载 Markdown
        md_content = item.get("markdown", "")
        if md_content:
            md_path = os.path.join(output_dir, f"{Path(file_name).stem}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"  ✅ 已保存: {md_path}")
            return md_path

    # 兜底：尝试直接从 result 获取
    zip_url = result.get("full_zip_url") or result.get("zip_url")
    if zip_url:
        zip_path = os.path.join(output_dir, f"{Path(file_name).stem}.zip")
        download_result(zip_url, zip_path)
        print(f"  ✅ 已保存: {zip_path}")
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
        print("❌ 未找到 PDF 文件")
        return []

    print(f"📚 找到 {len(pdf_files)} 个 PDF 文件")

    results = []
    for pdf_file in pdf_files:
        try:
            result = extract_pdf(str(pdf_file), output_dir, model)
            results.append({"file": str(pdf_file), "status": "success", "output": result})
        except Exception as e:
            print(f"  ❌ 提取失败: {e}")
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
        print("❌ 未找到 Token，请先运行: mineru-open-api auth")
        sys.exit(1)

    print(f"🔑 Token: {token[:10]}...")

    # 批量模式
    if args.batch:
        input_dir = args.input or "."
        output_dir = args.output or str(DEFAULT_OUTPUT_DIR)
        results = batch_extract(input_dir, output_dir)

        success = sum(1 for r in results if r["status"] == "success")
        print(f"\n📊 完成: {success}/{len(results)} 成功")
        sys.exit(0)

    # 单文件模式
    if not args.file:
        parser.print_help()
        sys.exit(1)

    output_dir = args.output or str(DEFAULT_OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)

    result = extract_pdf(args.file, output_dir)
    print(f"\n✅ 提取完成: {result}")
