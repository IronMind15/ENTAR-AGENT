"""
工具：image_describe（原 describe_image，v1.12.0 改名）
识别图片内容 — 把图片发给有视觉能力的模型（阿里云百炼千问 qwen3.7-flash），取回文字描述。
底层模型（DeepSeek）无原生视觉，收到图片时通过本工具"借看"（参考 claude-vision-skill 思路）。
"""

import base64
import json
import logging
import os
import time
from pathlib import Path

from scripts.paths import UPLOADS_DIR

import httpx

from scripts.config import DASHSCOPE_API_KEY, VISION_MODEL
from scripts.tools import register

logger = logging.getLogger("tool.vision")

# 阿里云百炼 OpenAI 兼容端点
_VISION_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# 项目约定：trust_env=False 绕开 Clash 系统代理对国内 API 的劫持
_HTTP_CLIENT = httpx.Client(timeout=60, trust_env=False)
# 与 agent.py 一致的可重试状态码
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
# V1 大小守卫：base64 膨胀约 1.33×，8MB 原图约 10.6MB base64，留足余量
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
# 图片路径白名单根目录（防提示注入诱导读取任意文件外传）
_UPLOAD_ROOT = UPLOADS_DIR


def _resolve_image_path(image_path: str) -> str:
    """解析图片路径：绝对路径优先；找不到则按文件名在上传目录递归查找。

    v1.11.5：LLM 上下文里常只有文件名（如 image_1.png），
    补这个兜底让「帮我识别这个图片的内容」也能成功，而不要求 LLM 精确猜出
    data/uploads/{部门}/{员工}/{日期}/image_1.png 三段式路径。
    """
    p = os.path.abspath(image_path)
    if os.path.isfile(p):
        return p
    name = os.path.basename(image_path.strip().rstrip("/\\"))
    if name and _UPLOAD_ROOT.exists():
        try:
            for hit in _UPLOAD_ROOT.rglob(name):
                if hit.is_file():
                    logger.info(f"  按文件名找到图片: {hit}")
                    return str(hit)
        except OSError:
            pass
    return p  # 原路径（不存在），由调用方统一报错


DEFINITION = {
    "name": "image_describe",
    "description": (
        "识别图片内容。当用户发送图片并询问图中内容、文字、参数时使用。"
        "image_path 为图片文件路径（绝对路径或上传目录内的文件名，如 image_1.png，"
        "工具会自动在上传目录查找）；question 可选，为对图片的具体提问。"
        "返回该图片的中文描述。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "图片文件路径（绝对路径，或上传目录内的文件名如 image_1.png，工具自动查找）。",
            },
            "question": {
                "type": "string",
                "description": "对图片的具体问题，可选；不传则请求描述图片内容。",
            },
        },
        "required": ["image_path"],
    },
}


def detect_image_mime(data: bytes):
    """用 magic bytes 检测真实图片格式（不信任扩展名），返回规范 mime 名（png/jpeg/gif/webp/bmp），无法识别返回 None。"""
    if not data or len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return None


def _call_vision(body: dict):
    """调用千问视觉 API，指数退避重试，返回描述文本；失败返回 None。"""
    for attempt in range(1, 4):
        try:
            r = _HTTP_CLIENT.post(
                _VISION_API_URL,
                headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}"},
                json=body,
            )
        except (httpx.TimeoutException, httpx.RequestError):
            if attempt < 3:
                time.sleep(attempt)
                continue
            return None
        if r.status_code == 200:
            try:
                choices = (r.json() or {}).get("choices") or []
                if choices:
                    content = choices[0].get("message", {}).get("content")
                    if content:
                        return content
            except ValueError:
                pass
            return None
        if r.status_code in _RETRYABLE_STATUS and attempt < 3:
            time.sleep(attempt)
            continue
        logger.warning(f"Vision API {r.status_code}: {r.text[:200]}")
        return None
    return None


@register(
    DEFINITION,
    sector="image",
    display="🖼️ 识别图片内容...",
    user_desc=(
        "识别图片内容（视觉大模型）。用户发送图片后，用其文件名即可查询内容描述。\n"
        "图片由钉钉自动保存到上传目录，传文件名即可。"
    ),
)
def execute(args: dict) -> str:
    """执行图片识别"""
    image_path = (args.get("image_path") or "").strip()
    if not image_path:
        return json.dumps({"error": "缺少 image_path 参数，请传入图片文件路径或文件名"}, ensure_ascii=False)

    p = _resolve_image_path(image_path)
    if not os.path.isfile(p):
        return json.dumps({"error": f"图片文件不存在或不可读：{image_path}"}, ensure_ascii=False)

    # 路径白名单：只允许读取上传目录内的图片（防提示注入诱导外传任意文件）
    try:
        in_upload = Path(p).resolve().is_relative_to(_UPLOAD_ROOT.resolve())
    except (ValueError, OSError):
        in_upload = False
    if not in_upload:
        return json.dumps({"error": "图片路径必须在服务端上传目录内"}, ensure_ascii=False)

    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError as e:
        return json.dumps({"error": f"读取图片失败：{e}"}, ensure_ascii=False)
    if len(data) > _MAX_IMAGE_BYTES:
        return json.dumps({"error": "图片超过 8MB，暂不支持识图，请先压缩再发送"}, ensure_ascii=False)

    mime = detect_image_mime(data)
    if not mime:
        return json.dumps({"error": "无法识别的图片格式（支持 PNG/JPEG/GIF/WebP/BMP）"}, ensure_ascii=False)

    if not DASHSCOPE_API_KEY:
        return json.dumps({"error": "识图功能未配置（缺少 DASHSCOPE_API_KEY），图片已保存但无法识别"}, ensure_ascii=False)

    b64 = base64.b64encode(data).decode("ascii")
    question = (args.get("question") or "").strip() or "请描述这张图片的内容"
    body = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                {"type": "text", "text": question},
            ],
        }],
        "max_tokens": 1024,
    }

    content = _call_vision(body)
    if content:
        logger.info(f"  识图成功: {p} ({mime}, {len(data)} bytes)")
        return json.dumps({"description": content, "image_path": p}, ensure_ascii=False)

    logger.warning(f"识图失败: {p}")
    return json.dumps({"error": "图片识别失败，请稍后重试"}, ensure_ascii=False)
