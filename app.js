const state = { mode: 'mode1', video: null, asset: null, last: null };
const $ = (s) => document.querySelector(s);

const videoInput = $('#videoInput');
const assetInput = $('#assetInput');
const videoDrop = $('#videoDrop');
const assetDrop = $('#assetDrop');
const generateBtn = $('#generateBtn');

function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  window.clearTimeout(toast.timer); toast.timer = window.setTimeout(() => el.classList.remove('show'), 2600);
}

function formatBytes(bytes) {
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB']; let n = bytes; let i = 0;
  while (n > 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function formatDuration(seconds) {
  const n = Number(seconds || 0); if (!n) return '—';
  return `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
}

function updateButton() { generateBtn.disabled = !state.video || (state.mode === 'mode2' && !state.asset); }

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.mode-tab').forEach((tab) => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle('active', active); tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $('#assetPanel').classList.toggle('mode2-required', mode === 'mode2');
  updateButton();
}

function setVideo(file) {
  if (!file) return;
  if (!file.type.startsWith('video/') && !/\.(mp4|mov|webm|m4v|mkv)$/i.test(file.name)) { toast('请选择视频文件'); return; }
  state.video = file;
  $('#videoPreview').src = URL.createObjectURL(file);
  $('#videoName').textContent = file.name;
  $('#videoMeta').textContent = `${formatBytes(file.size)} · 等待读取时长`;
  $('#videoPreviewCard').classList.remove('hidden'); videoDrop.classList.add('has-file');
  $('#videoPreview').onloadedmetadata = () => {
    const v = $('#videoPreview');
    $('#videoMeta').textContent = `${formatDuration(v.duration)} · ${v.videoWidth}×${v.videoHeight} · ${formatBytes(file.size)}`;
  };
  updateButton();
}

function setAsset(file) {
  if (!file) return;
  if (!file.type.startsWith('image/') && !/\.(png|jpe?g|webp)$/i.test(file.name)) { toast('请选择 PNG / JPG / WebP 图片'); return; }
  state.asset = file;
  $('#assetImage').src = URL.createObjectURL(file); $('#assetName').textContent = file.name;
  $('#assetPreview').classList.remove('hidden'); assetDrop.classList.add('has-file'); updateButton();
}

function clearVideo() { state.video = null; videoInput.value = ''; $('#videoPreview').removeAttribute('src'); $('#videoPreviewCard').classList.add('hidden'); updateButton(); }
function clearAsset() { state.asset = null; assetInput.value = ''; $('#assetImage').removeAttribute('src'); $('#assetPreview').classList.add('hidden'); updateButton(); }

function wireDrop(drop, input, setter) {
  ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
  drop.addEventListener('drop', (e) => setter(e.dataTransfer.files[0]));
  input.addEventListener('change', () => setter(input.files[0]));
}

function dataUrlFromCanvas(canvas, quality = 0.58) {
  return canvas.toDataURL('image/jpeg', quality);
}

async function extractClientFrames(file, count = 8) {
  const url = URL.createObjectURL(file);
  const video = document.createElement('video');
  video.preload = 'metadata'; video.muted = true; video.playsInline = true; video.src = url;
  await new Promise((resolve, reject) => { video.onloadedmetadata = resolve; video.onerror = () => reject(new Error('无法读取视频画面')); });
  const duration = Number.isFinite(video.duration) ? video.duration : 8;
  const width = video.videoWidth || 768; const height = video.videoHeight || 432;
  const scale = Math.min(1, 768 / width); const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale)); canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext('2d', { willReadFrequently: false }); const frames = [];
  for (let i = 0; i < count; i += 1) {
    const time = count === 1 ? 0 : Math.min(Math.max(0, duration - 0.05), duration * i / (count - 1));
    video.currentTime = time;
    await new Promise((resolve) => { video.onseeked = resolve; });
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height); frames.push(dataUrlFromCanvas(canvas));
  }
  URL.revokeObjectURL(url); video.remove();
  return { frames, metadata: { duration: Number(duration.toFixed(3)), width, height, aspect_ratio: inferAspect(width, height), has_audio: Boolean(video.audioTracks?.length) } };
}

function inferAspect(width, height) {
  if (!width || !height) return '9:16';
  const ratios = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1, '4:3': 4 / 3, '3:4': 3 / 4, '21:9': 21 / 9 };
  const ratio = width / height; return Object.keys(ratios).sort((a, b) => Math.abs(ratios[a] - ratio) - Math.abs(ratios[b] - ratio))[0];
}

async function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
}

function renderResult(data) {
  state.last = data;
  $('#emptyState').classList.add('hidden'); $('#resultState').classList.remove('hidden');
  $('#resultMode').textContent = state.mode === 'mode2' ? 'MODE 02 / REPLACE' : 'MODE 01 / DECONSTRUCT';
  const m = data.metadata || {};
  $('#metricRow').innerHTML = [
    ['时长', `${Number(m.duration || 0).toFixed(1)}s`], ['画幅', m.aspect_ratio || '—'], ['尺寸', `${m.width || '—'}×${m.height || '—'}`], ['音频', m.has_audio ? (m.audio_codec || '有') : '无'], ['抽帧', `${data.analysis ? (data.analysis.shots || []).length : 0} 段`],
  ].map(([label, value]) => `<span class="metric">${label} <b>${value}</b></span>`).join('');
  const warning = $('#warningBox');
  if (data.warning) { warning.textContent = data.warning; warning.classList.remove('hidden'); } else warning.classList.add('hidden');
  const a = data.analysis || {};
  $('#analysisSummary').innerHTML = [
    ['主体', a.subject || '[待确认]'], ['场景', a.environment || '[待确认]'], ['外层包装', a.wrapper_detected || '未检测到'], ['分析来源', a.source || '本地兜底'],
  ].map(([label, value]) => `<div class="summary-card"><span>${label}</span><p>${escapeHtml(String(value))}</p></div>`).join('');
  $('#promptOutput').value = data.prompt || '';
  $('#fileLinks').innerHTML = Object.entries(data.files || {}).map(([key, path]) => `<a href="${path}" target="_blank" rel="noopener">打开 ${key}</a>`).join('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function escapeHtml(value) { return value.replace(/[&<>'"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch])); }

async function generate() {
  if (!state.video) return toast('先上传参考视频');
  if (state.mode === 'mode2' && !state.asset) return toast('模式二还需要产品/角色图');
  generateBtn.disabled = true; generateBtn.querySelector('span').textContent = '正在抽帧与分析…';
  try {
    const isLocalServer = ['127.0.0.1', 'localhost'].includes(window.location.hostname);
    let response;
    if (isLocalServer) {
      const body = new FormData(); body.append('mode', state.mode); body.append('video', state.video);
      if (state.asset) body.append('asset', state.asset);
      body.append('duration', $('#durationInput').value); body.append('aspect', $('#aspectInput').value); body.append('notes', $('#notesInput').value);
      response = await fetch('/api/analyze', { method: 'POST', body });
    } else {
      const extracted = await extractClientFrames(state.video, 8);
      const payload = { mode: state.mode, metadata: extracted.metadata, frames: extracted.frames, duration: $('#durationInput').value, aspect: $('#aspectInput').value, notes: $('#notesInput').value };
      if (state.asset) payload.asset = await fileAsDataUrl(state.asset);
      response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    }
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || '分析失败');
    renderResult(data); toast('提示词已生成');
  } catch (error) { toast(error.message || '分析失败'); }
  finally { generateBtn.disabled = false; generateBtn.querySelector('span').textContent = '生成 Seedance 提示词'; updateButton(); }
}

async function copyPrompt() {
  const text = $('#promptOutput').value; if (!text) return;
  try { await navigator.clipboard.writeText(text); toast('已复制到剪贴板'); }
  catch { $('#promptOutput').select(); document.execCommand('copy'); toast('已复制到剪贴板'); }
}

function downloadPrompt() {
  if (!state.last?.prompt) return;
  const blob = new Blob([state.last.prompt], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${state.last.run_id || 'seedance-prompt'}.txt`; a.click(); URL.revokeObjectURL(a.href);
}

document.querySelectorAll('.mode-tab').forEach((tab) => tab.addEventListener('click', () => setMode(tab.dataset.mode)));
wireDrop(videoDrop, videoInput, setVideo); wireDrop(assetDrop, assetInput, setAsset);
$('#clearVideo').addEventListener('click', clearVideo); $('#clearAsset').addEventListener('click', clearAsset);
generateBtn.addEventListener('click', generate); $('#copyBtn').addEventListener('click', copyPrompt); $('#downloadBtn').addEventListener('click', downloadPrompt);
$('#newRunBtn').addEventListener('click', () => { $('#resultState').classList.add('hidden'); $('#emptyState').classList.remove('hidden'); window.scrollTo({ top: 0, behavior: 'smooth' }); });

fetch('/api/health').then((r) => r.json()).then((data) => {
  $('#visionStatus').textContent = data.vision_configured ? '视觉分析已连接' : '本地分析服务';
  $('#visionHint').textContent = data.vision_configured ? '会识别动作与主体' : '未配置视觉模型';
}).catch(() => { $('#visionHint').textContent = '请使用本地服务启动'; });
