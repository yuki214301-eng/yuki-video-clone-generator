#!/usr/bin/env python3
"""本地视频提示词生成器。

用法：python3 video-prompt-generator/app.py
浏览器打开：http://127.0.0.1:8765

服务本身不保存 API Key。若设置 OPENAI_API_KEY，会调用兼容
OpenAI Chat Completions 的视觉模型；未设置时仍可生成带待确认标记的
Seedance 2.5 提示词。
"""

from __future__ import annotations

import base64
import cgi
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)
HOST = os.getenv("VIDEO_PROMPT_HOST", "127.0.0.1")
PORT = int(os.getenv("VIDEO_PROMPT_PORT", "8765"))
MAX_UPLOAD = 300 * 1024 * 1024
ALLOWED_VIDEO = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def safe_name(name: str, fallback: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-.")
    return stem or fallback


def run_cmd(args: List[str], timeout: int = 120) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout


def ffprobe(path: Path) -> Dict[str, Any]:
    try:
        raw = run_cmd(
            [
                shutil.which("ffprobe") or "ffprobe",
                "-v", "error", "-show_entries",
                "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                "-of", "json", str(path),
            ]
        )
        data = json.loads(raw)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError(f"无法读取视频媒体信息：{exc}") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float((data.get("format") or {}).get("duration") or 0)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "aspect_ratio": infer_aspect(width, height),
        "video_codec": video.get("codec_name") or "unknown",
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": bool(audio),
        "size_bytes": int((data.get("format") or {}).get("size") or path.stat().st_size),
        "frame_rate": video.get("r_frame_rate") or "unknown",
    }


def infer_aspect(width: int, height: int) -> str:
    if not width or not height:
        return "9:16"
    ratios = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1, "4:3": 4 / 3, "3:4": 3 / 4, "21:9": 21 / 9}
    ratio = width / height
    return min(ratios, key=lambda key: abs(ratios[key] - ratio))


def extract_frames(video: Path, out_dir: Path, duration: float, count: int = 12) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if duration <= 0:
        return []
    count = max(4, min(count, 16))
    frames: List[Path] = []
    for i in range(count):
        t = 0 if count == 1 else min(duration - 0.02, duration * i / (count - 1))
        target = out_dir / f"frame-{i+1:02d}.jpg"
        try:
            subprocess.run(
                [shutil.which("ffmpeg") or "ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}",
                 "-i", str(video), "-frames:v", "1", "-vf", "scale=768:-2", "-q:v", "4", str(target)],
                check=True, capture_output=True, timeout=60,
            )
            if target.is_file() and target.stat().st_size:
                frames.append(target)
        except (OSError, subprocess.SubprocessError):
            continue
    return frames


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def fallback_analysis(metadata: Dict[str, Any], asset_present: bool) -> Dict[str, Any]:
    duration = max(1, min(30, int(round(metadata.get("duration") or 8))))
    cuts = [0, round(duration * 0.25, 2), round(duration * 0.55, 2), round(duration * 0.8, 2), duration]
    shots = []
    labels = ["开场钩子", "动作推进", "关键动作/转折", "收尾定格"]
    for i in range(4):
        shots.append({
            "start": cuts[i], "end": cuts[i + 1], "label": labels[i],
            "framing": "[待确认景别]", "camera": "[待确认运镜]", "action": "[待确认主体动作]",
            "sound": "保留原片可辨识的环境声/对白；无法确认时留空",
            "negative": "不要重置站位，不要凭空出现道具",
        })
    return {
        "source": "metadata_fallback",
        "wrapper_detected": "[待确认：是否存在手机/屏幕/播放器外层包装]",
        "summary": "[待确认主体] 在 [待确认场景] 中完成一段连续动作，镜头节奏与转场沿用原视频。",
        "subject": "[待确认主体/产品或角色]",
        "environment": "[待确认场景、时间与光线]",
        "shots": shots,
        "fast_actions": ["[若存在快速动作，请补写准备→接触/释放→独立运动→终点→跟随动作]"],
        "dialogue": "[待确认：对白语言、说话人、原句及时间]",
        "sound": "保留原片环境声与已确认的对白/音效逻辑；不擅自新增 BGM。",
        "invariants": [
            "主体轮廓、比例与关键结构保持一致",
            "主体颜色、材质与高光关系保持一致",
            "主体与手/场景的接触关系符合物理逻辑",
        ] if asset_present else [],
    }


def parse_json_text(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except ValueError:
            return None


def vision_analysis(frames: List[Path], asset: Optional[Path], metadata: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not frames:
        return None, "未配置 OPENAI_API_KEY，已使用媒体元数据兜底；镜头语义和产品不变量需人工确认。"
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("VIDEO_PROMPT_VISION_MODEL", "gpt-4o-mini")
    schema_hint = {
        "wrapper_detected": "描述是否有屏幕/手机/播放器/反应视频外层包装，没有则写无",
        "summary": "一句话概述",
        "subject": "主体与其可见外观",
        "environment": "场景、光线、色彩、空间",
        "shots": [{"start": 0, "end": 2, "label": "", "framing": "", "camera": "", "action": "", "sound": "", "negative": ""}],
        "fast_actions": ["按准备、接触/释放、独立运动、终点、跟随动作描述"],
        "dialogue": "说话人、语言、可辨识原句与时间；无法辨认写无法确认",
        "sound": "环境声、音效、BGM 是否可辨识",
        "invariants": ["仅列出 3–8 个视觉不变量；不确定不要猜"],
    }
    prompt = (
        "你是视频动作证据分析器。只根据上传视频抽帧和产品/角色图输出 JSON，不要写提示词。"
        "先识别并剥离手机屏幕、播放器 UI、桌面、反应视频等外层包装，只描述最内层原创场景。"
        "对快速动作记录准备、手指接触、释放/碰撞、首次分离、独立轨迹、终点和跟随动作。"
        f"视频元数据：{json.dumps(metadata, ensure_ascii=False)}。JSON 字段示例：{json.dumps(schema_hint, ensure_ascii=False)}。"
        "时间轴必须覆盖 0 到视频结束，若不可见就写待确认，不得臆造对白、品牌和材质。"
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for frame in frames:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(frame), "detail": "low"}})
    if asset:
        content.append({"type": "text", "text": "下面这张是模式二的产品/角色图；只用于识别可保持的不变量，不要把其背景带入参考视频场景。"})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(asset), "detail": "high"}})
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.1, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": content}]},
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        text = body["choices"][0]["message"]["content"]
        parsed = parse_json_text(text)
        if parsed:
            parsed["source"] = f"vision:{model}"
            return parsed, None
        return None, "视觉模型返回内容无法解析，已使用元数据兜底。"
    except (requests.RequestException, KeyError, ValueError) as exc:
        return None, f"视觉分析调用失败，已使用元数据兜底：{exc}"


def as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_shots(shots: Any, duration: int) -> List[Dict[str, Any]]:
    if not isinstance(shots, list):
        return fallback_analysis({"duration": duration}, False)["shots"]
    result = []
    cursor = 0.0
    for item in shots:
        if not isinstance(item, dict):
            continue
        start = max(cursor, min(duration, as_float(item.get("start"), cursor)))
        end = max(start + 0.1, min(duration, as_float(item.get("end"), start + duration / max(len(shots), 1))))
        if end <= start:
            continue
        result.append({
            "start": round(start, 2), "end": round(end, 2),
            "label": item.get("label") or "镜头段落",
            "framing": item.get("framing") or "[待确认景别]",
            "camera": item.get("camera") or "[待确认运镜]",
            "action": item.get("action") or "[待确认动作]",
            "sound": item.get("sound") or "保留该段已确认声音逻辑",
            "negative": item.get("negative") or "不要跳切、不要重置空间关系",
        })
        cursor = end
    if not result:
        return fallback_analysis({"duration": duration}, False)["shots"]
    if result[0]["start"] > 0:
        result.insert(0, {"start": 0, "end": result[0]["start"], "label": "开场承接", "framing": "沿用原片开场景别", "camera": "沿用原片开场运镜", "action": "从原片第一帧的状态开始", "sound": "承接原片开场声音", "negative": "不要凭空增加主体"})
    if result[-1]["end"] < duration:
        result.append({"start": result[-1]["end"], "end": duration, "label": "收尾", "framing": "沿用上一段景别", "camera": "自然完成最后构图", "action": "动作落地并保持状态", "sound": "自然收束环境声", "negative": "不要重置主体位置"})
    return result


def one_line(value: Any, fallback: str) -> str:
    if isinstance(value, list):
        value = "；".join(str(x) for x in value)
    text = str(value or "").strip()
    return text if text else fallback


def compose_prompt(mode: str, analysis: Dict[str, Any], metadata: Dict[str, Any], asset_present: bool, duration_override: Optional[str], aspect_override: Optional[str], notes: str) -> str:
    duration = int(round(as_float(duration_override, metadata.get("duration") or 8))) if duration_override else int(round(metadata.get("duration") or 8))
    duration = max(1, min(30, duration))
    aspect = aspect_override or metadata.get("aspect_ratio") or "9:16"
    shots = normalize_shots(analysis.get("shots"), duration)
    invariants = analysis.get("invariants") if isinstance(analysis.get("invariants"), list) else []
    invariants = [str(x).strip() for x in invariants if str(x).strip()][:8]
    if asset_present and not invariants:
        invariants = ["@图片1 的主体轮廓、比例和关键结构保持一致", "@图片1 的颜色、材质和高光关系保持一致", "@图片1 的组件数量、相对位置和非对称细节保持一致"]
    lines = [
        "参考已解析的创意形式、镜头节奏、转场方式，",
        "【输出参数】",
        f"生成 {duration} 秒、{aspect} 画幅、720p、直接拍摄原始场景的 Seedance 2.5 视频；沿用原视频可确认的镜头顺序、节奏、动作因果、对白语言与声音逻辑。",
        "",
        "【素材描述】",
        "参考视频仅用于前序解析，不作为最终上传素材；最终提示词不得复刻手机/屏幕/播放器等外层包装。",
    ]
    if asset_present:
        role = "产品/角色图"
        lines.append(f"@图片1：用户上传的{role}，作为替换目标和外观权威参考；只替换原视频中的对应产品/角色，不把图片背景带入参考场景。")
        lines.append("替换规则：原视频中每一次出现的对应产品/角色都替换为@图片1，保留原视频的动作、位置、尺度、遮挡、光影和物理交互。")
        lines.append("外观不变量：" + "；".join(invariants) + "。")
    lines += [
        "",
        "【一句话概述】",
        f"{one_line(analysis.get('subject'), '[待确认主体]')} 在 {one_line(analysis.get('environment'), '[待确认场景]')} 中完成一段连续的动作叙事，整体沿用原视频的题材、节奏与镜头语言。",
        "",
        "【完整时间戳序列】",
    ]
    for shot in shots:
        lines.append(
            f"{shot['start']:.2f}s–{shot['end']:.2f}s｜{shot['label']}：景别/构图={shot['framing']}；运镜={shot['camera']}；动作/表情={shot['action']}；声音={shot['sound']}。反向约束：{shot['negative']}。"
        )
    fast_actions = analysis.get("fast_actions")
    if isinstance(fast_actions, list) and fast_actions:
        lines.extend(["", "【快速动作证据】"])
        for action in fast_actions[:6]:
            lines.append(f"{action}")
    lines += [
        "",
        "【对白与声音】",
        f"对白：{one_line(analysis.get('dialogue'), '[待确认：说话人、语言、原句与时间]')}。",
        f"声音逻辑：{one_line(analysis.get('sound'), '保留原片可辨识的环境声与音效；无法确认的声音不擅自新增。')}",
        "",
        "【全局约束】",
        "全片保持主体身份、空间关系、镜头速度、光线方向、景深、色彩和声音连续；动作必须符合准备→接触/释放→独立运动→终点→跟随动作的因果顺序。",
        "明确排除：无字幕、无 captions、无文字贴纸、无水印、无平台 UI、无手机/电脑屏幕、无播放器控件、无设备边框、无屏幕反光、无摩尔纹、无反应视频布局、无额外品牌能力、无主体变形、无缺失或新增组件、无手部畸变、无身份漂移、无突兀跳切。",
    ]
    if notes.strip():
        lines.extend(["", "【用户补充（仅作为约束）】", notes.strip()])
    return "\n".join(lines).strip() + "\n"


def save_upload(field: Any, run_dir: Path, allowed: set[str], label: str, required: bool = False) -> Optional[Path]:
    if field is None or not getattr(field, "filename", None):
        if required:
            raise ValueError(f"请上传{label}")
        return None
    filename = field.filename
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed:
        raise ValueError(f"{label}格式不支持：{suffix or '无扩展名'}")
    target = run_dir / f"{label}-{safe_name(Path(filename).stem, 'upload')}{suffix}"
    data = field.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"{label}超过 300MB 限制")
    target.write_bytes(data)
    return target


class Handler(SimpleHTTPRequestHandler):
    server_version = "VideoPromptGenerator/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self.send_json({"ok": True, "vision_configured": bool(os.getenv("OPENAI_API_KEY"))})
            return
        if path.startswith("/runs/"):
            rel = path[len("/runs/"):].lstrip("/")
            target = (RUNS / rel).resolve()
            if not str(target).startswith(str(RUNS.resolve())) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(target.stat().st_size))
            self.end_headers()
            with target.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/analyze":
            self.send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD * 2:
                raise ValueError("上传请求为空或超过限制")
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(length)})
            mode = (form.getfirst("mode") or "mode1").strip()
            if mode not in {"mode1", "mode2"}:
                raise ValueError("mode 必须是 mode1 或 mode2")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            run_dir = RUNS / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            video = save_upload(form["video"] if "video" in form else None, run_dir, ALLOWED_VIDEO, "reference-video", True)
            asset = save_upload(form["asset"] if "asset" in form else None, run_dir, ALLOWED_IMAGE, "asset", mode == "mode2")
            metadata = ffprobe(video)
            duration_override = form.getfirst("duration") or ""
            aspect_override = form.getfirst("aspect") or ""
            notes = form.getfirst("notes") or ""
            frames = extract_frames(video, run_dir / "frames", metadata["duration"])
            analysis, warning = vision_analysis(frames, asset, metadata)
            if analysis is None:
                analysis = fallback_analysis(metadata, bool(asset))
            prompt = compose_prompt(mode, analysis, metadata, bool(asset), duration_override, aspect_override, notes)
            (run_dir / "analysis.json").write_bytes(json_bytes(analysis) + b"\n")
            (run_dir / "metadata.json").write_bytes(json_bytes(metadata) + b"\n")
            (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            manifest = {
                "run_id": run_id, "mode": mode, "created_at": datetime.now().astimezone().isoformat(),
                "metadata": metadata, "analysis_source": analysis.get("source", "unknown"),
                "vision_warning": warning, "prompt_file": f"/runs/{run_id}/prompt.txt",
                "analysis_file": f"/runs/{run_id}/analysis.json", "frame_count": len(frames),
            }
            (run_dir / "manifest.json").write_bytes(json_bytes(manifest) + b"\n")
            self.send_json({"ok": True, "run_id": run_id, "mode": mode, "prompt": prompt, "analysis": analysis, "metadata": metadata, "warning": warning, "files": {"prompt": manifest["prompt_file"], "analysis": manifest["analysis_file"], "manifest": f"/runs/{run_id}/manifest.json"}})
        except (ValueError, OSError, KeyError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # 守住前端，不泄露环境变量
            self.send_json({"ok": False, "error": f"处理失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    print(f"Video Prompt Generator running at http://{HOST}:{PORT}")
    if not os.getenv("OPENAI_API_KEY"):
        print("提示：未设置 OPENAI_API_KEY，将使用元数据兜底并在提示词中标出待确认项。")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
