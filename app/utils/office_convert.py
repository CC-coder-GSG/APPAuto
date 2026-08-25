from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 可被 LibreOffice 转换为 PDF 预览的 Office 扩展名。
CONVERTIBLE_EXTS = {".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}


def soffice_available() -> bool:
    """LibreOffice 是否就绪（用于优雅降级判定）。"""
    return shutil.which(settings.libreoffice_bin) is not None


def convert_to_pdf(src_path: Path, out_dir: Path) -> Path | None:
    """
    用 LibreOffice headless 把 Office 文档转成 PDF，落到 out_dir。

    成功返回生成的 PDF 路径；LibreOffice 缺失、超时或失败时返回 None
    （调用方据此降级为"仅下载"，不抛异常打断上传流程）。
    """
    src_path = Path(src_path)
    if src_path.suffix.lower() not in CONVERTIBLE_EXTS:
        return None
    if not soffice_available():
        logger.info("convert_to_pdf: '%s' 未安装，跳过 PDF 预览生成", settings.libreoffice_bin)
        return None

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_pdf = out_dir / f"{src_path.stem}.pdf"
    try:
        subprocess.run(
            [
                settings.libreoffice_bin,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(src_path),
            ],
            check=True,
            capture_output=True,
            timeout=settings.libreoffice_convert_timeout_seconds,
        )
    except (subprocess.SubprocessError, OSError) as exc:  # noqa: BLE001
        logger.warning("convert_to_pdf 失败 src=%s err=%s", src_path, exc)
        return None

    if expected_pdf.exists():
        return expected_pdf
    logger.warning("convert_to_pdf 未产出预期 PDF: %s", expected_pdf)
    return None


__all__ = ["CONVERTIBLE_EXTS", "soffice_available", "convert_to_pdf"]
