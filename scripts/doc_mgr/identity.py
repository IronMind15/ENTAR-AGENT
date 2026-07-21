"""文档身份与内容指纹工具。"""

import hashlib
import os


def file_sha256(file_path: str, block_size: int = 1024 * 1024) -> str:
    """流式计算文件 SHA-256，避免把大文件一次性读入内存。"""
    digest = hashlib.sha256()
    with open(file_path, "rb") as source:
        while True:
            block = source.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def stable_document_id(file_path: str) -> str:
    """根据规范化源路径生成稳定文档 ID。"""
    normalized = os.path.normcase(os.path.realpath(os.path.abspath(file_path)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
