"""Vercel Serverless API：接收浏览器抽取的帧和产品图，生成 Seedance 提示词。"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict

# Vercel 的 Python 函数工作目录通常是 api/，显式加入项目根目录，复用本地版的提示词规则。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import compose_prompt, fallback_analysis, vision_analysis  # noqa: E402


def decode_data_url(value: str, target: Path) -> bool:
    if not isinstance(value, str) or "," not in value:
        return False
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        return False
    try:
        target.write_bytes(base64.b64decode(encoded, validate=True))
        return target.is_file() and target.stat().st_size > 0
    except (ValueError, OSError):
        return False


def json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4_500_000:
                raise ValueError("请求过大；请减少视频抽帧数量或图片尺寸")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            mode = payload.get("mode", "mode1")
            if mode not in {"mode1", "mode2"}:
                raise ValueError("mode 必须是 mode1 或 mode2")
            metadata = payload.get("metadata") or {}
            frames_payload = payload.get("frames") or []
            if not frames_payload:
                raise ValueError("没有收到视频抽帧")
            if mode == "mode2" and not payload.get("asset"):
                raise ValueError("模式二需要产品/角色图")

            with tempfile.TemporaryDirectory(prefix="seedance-prompt-") as temp_dir:
                temp = Path(temp_dir)
                frames = []
                for index, data_url in enumerate(frames_payload[:10]):
                    target = temp / f"frame-{index:02d}.jpg"
                    if decode_data_url(data_url, target):
                        frames.append(target)
                asset = None
                if payload.get("asset"):
                    asset = temp / "asset.jpg"
                    if not decode_data_url(payload["asset"], asset):
                        raise ValueError("产品/角色图无法解析")
                analysis, warning = vision_analysis(frames, asset, metadata)
                if analysis is None:
                    analysis = fallback_analysis(metadata, bool(asset))
                prompt = compose_prompt(
                    mode,
                    analysis,
                    metadata,
                    bool(asset),
                    payload.get("duration") or "",
                    payload.get("aspect") or "",
                    payload.get("notes") or "",
                )
            json_response(self, {"ok": True, "run_id": "vercel-" + str(abs(hash(prompt)))[:10], "mode": mode, "prompt": prompt, "analysis": analysis, "metadata": metadata, "warning": warning or "", "files": {}})
        except (ValueError, json.JSONDecodeError, KeyError) as exc:
            json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # 不把环境变量或完整堆栈返回给浏览器
            json_response(self, {"ok": False, "error": f"处理失败：{exc}"}, 500)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

