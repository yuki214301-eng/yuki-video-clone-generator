# Prompt Foundry：视频提示词生成器

这是一个本地双模式工具，按 `link-replication-skill` 与 `sd2-5-prompt-writing` 的流程，把参考视频整理成可直接粘贴到 Seedance 2.5 的提示词。

## 两种模式

- **模式一：只输入视频**。解析原视频的钩子、镜头顺序、运镜、动作因果、声音与外层包装，并输出“复刻创意形式”的提示词。
- **模式二：视频 + 产品/角色图**。把图片固定为 `@图片1`，将原视频中对应的产品/角色全部替换为图片主体，同时保留原片动作、位置、尺度、遮挡、光影和节奏。

## 启动

在项目根目录运行：

```bash
python3 video-prompt-generator/app.py
```

浏览器打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。

部署到 Vercel（Preview）：

```bash
vercel deploy --yes
```

Vercel 版本会在浏览器端抽取视频帧，再调用 `/api/analyze`；不会把整段视频上传到 Serverless Function。

服务会用本机 `ffprobe` / `ffmpeg` 读取视频元数据并抽帧。要启用视觉语义分析，可在启动前配置兼容 OpenAI Chat Completions 的视觉模型：

```bash
export OPENAI_API_KEY="你的密钥"
# 可选：兼容网关或内网代理
export OPENAI_BASE_URL="https://api.openai.com/v1"
export VIDEO_PROMPT_VISION_MODEL="gpt-4o-mini"
python3 video-prompt-generator/app.py
```

密钥只从当前进程环境读取，不写入运行目录、提示词或 manifest。没有密钥时工具仍会生成结构完整的提示词，但会在界面和提示词中标出 `[待确认]`，避免把看不清的动作或材质写成事实。

## 输出文件

每次分析会写入 `video-prompt-generator/runs/<run-id>/`：

- `prompt.txt`：最终可提交的完整提示词；
- `analysis.json`：镜头、快速动作、声音和主体分析；
- `metadata.json`：时长、尺寸、编解码、音频等技术信息；
- `manifest.json`：本次运行的索引与告警；
- `frames/`：抽帧证据。

本工具只生成提示词，不调用 WaveSpeed、不上传飞书，也不会自动重跑生成任务。真正生成成片时，继续使用 `link-replication-skill` 的 `generate`、`qa` 和 `review` 步骤。
