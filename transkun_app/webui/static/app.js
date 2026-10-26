'use strict';

const $ = (id) => document.getElementById(id);

const MODEL = '2.0';
const STATUS_TEXT = {
  queued: '排队中',
  running: '转谱中',
  done: '完成',
  failed: '失败',
  cancelled: '已取消',
};
const FORMATS = ['WAV', 'MP3', 'FLAC', 'OGG', 'M4A', 'AAC', 'AIFF', 'OPUS'];
const ROLL_COLORS = ['#5b7cff', '#7a6bff', '#4fa3ff', '#8b5cf6', '#22b8cf'];

const state = {
  info: null,
  jobs: [],
  pending: [],
  uploading: false,
  xhr: null,
};

// ------------------------------------------------------------------ 小工具

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`;
  return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
}

function fmtTime(seconds) {
  const date = new Date((seconds || 0) * 1000);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function fmtClock(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const m = String(Math.floor(total / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function errorMessage(xhr) {
  try {
    const body = JSON.parse(xhr.responseText);
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg || JSON.stringify(item)).join('；');
    }
  } catch (err) {
    // 不是 JSON：退回状态码。
  }
  return `HTTP ${xhr.status}${xhr.status === 401 ? '（令牌失效，请重新用带 ?t= 的链接打开）' : ''}`;
}

async function apiJson(response) {
  let body = null;
  try {
    body = await response.json();
  } catch (err) {
    // 不是 JSON（很少见）。
  }
  if (!response.ok) {
    const detail = body && body.detail;
    if (typeof detail === 'string') throw new Error(detail);
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg || JSON.stringify(item)).join('；'));
    }
    throw new Error(`HTTP ${response.status}`);
  }
  return body;
}

async function api(path, options) {
  const response = await fetch(path, options || {});
  if (response.status === 401) {
    location.reload();
    throw new Error('需要访问令牌');
  }
  return response;
}

// ------------------------------------------------------------------ 主题

function initTheme() {
  const forced = new URLSearchParams(location.search).get('theme');
  if (forced === 'dark' || forced === 'light') {
    document.documentElement.dataset.theme = forced;
    return;
  }
  const saved = localStorage.getItem('transkun-theme');
  const dark = saved
    ? saved === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

function toggleTheme() {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('transkun-theme', root.dataset.theme);
}

// ------------------------------------------------------------------ 服务信息

function fillInfo(info) {
  state.info = info;

  const gpu = /cuda|gpu/i.test(info.device || '');
  const pill = $('devicePill');
  pill.className = gpu ? 'pill gpu' : 'pill';
  pill.replaceChildren(el('i', { class: 'dot' }),
    document.createTextNode(` ${info.device || '未知设备'}`));

  $('versionPill').textContent = info.version && info.version !== 'dev'
    ? `v${info.version}` : '开发版';

  const available = info.models && info.models[MODEL];
  const model = document.querySelector('.model');
  if (model) {
    model.disabled = !available;
    if (!available) {
      model.append(el('p', { class: 'hint', text: '服务器上没有模型权重。' }));
    }
  }

  fillLimits(info.limits || {});

  const defaults = info.rearrange_defaults || {};
  if (defaults.velocity !== undefined) $('velocity').value = defaults.velocity;
  if (defaults.min_ms !== undefined) $('minMs').value = defaults.min_ms;
  if (defaults.chord_ms !== undefined) $('chordMs').value = defaults.chord_ms;
  if (defaults.repeat_ms !== undefined) $('repeatMs').value = defaults.repeat_ms;
  updateHint();
}

function fillLimits(limits) {
  const parts = [
    `单文件 ≤ ${fmtBytes(limits.max_bytes)}`,
    `时长 ≤ ${limits.max_minutes} 分钟`,
    `队列 ${limits.queue_limit}`,
    `结果保留 ${limits.keep_hours} 小时`,
  ];
  $('limits').replaceChildren(...parts.flatMap((text, index) => (
    index ? [el('span', { class: 'sep' }), el('span', { text })] : [el('span', { text })]
  )));
}

function updateHint() {
  const parts = [];
  if (!$('rearrange').checked) {
    parts.push('不勾选 MIDI 整理时只输出原始 .mid；勾选后会另存一份 .rearranged.mid，原文件保留。');
  }
  parts.push('转谱会保留模型预测的延音踏板（CC64）。');
  $('rearrangeHint').textContent = parts.join(' ');
}

// ------------------------------------------------------------------ 文件选择

function updateFileBar() {
  const list = state.pending;
  $('fileInfo').classList.toggle('empty', !list.length);
  if (!list.length) {
    $('fileName').textContent = '还没有选择文件';
    $('fileSize').textContent = '';
    $('selectedInfo').textContent = '还没有选择文件';
  } else {
    const total = list.reduce((sum, file) => sum + file.size, 0);
    $('fileName').textContent = list.length === 1
      ? list[0].name : `${list[0].name} 等 ${list.length} 个文件`;
    $('fileSize').textContent = fmtBytes(total);
    $('selectedInfo').textContent = list.length === 1
      ? `将转谱：${list[0].name} · ${fmtBytes(total)}`
      : `将转谱：${list.length} 个文件 · ${fmtBytes(total)}`;
  }
  const disabled = !list.length || state.uploading || !state.info;
  $('start').disabled = disabled;
  $('start').textContent = list.length > 1
    ? `开始转谱（${list.length} 个）` : '开始转谱';
}

function setPending(files) {
  state.pending = Array.from(files || []);
  $('uploadRow').hidden = true;
  updateFileBar();
}

// ------------------------------------------------------------------ 上传

function uploadOne(file, index, total) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    form.append('model', MODEL);
    form.append('rearrange', $('rearrange').checked ? 'true' : 'false');
    form.append('velocity', $('velocity').value || '30');
    form.append('min_ms', $('minMs').value || '30');
    form.append('chord_ms', $('chordMs').value || '60');
    form.append('repeat_ms', $('repeatMs').value || '120');

    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((100 * event.loaded) / event.total);
      $('uploadBar').style.width = `${percent}%`;
      $('uploadStatus').textContent =
        `第 ${index}/${total} 个：${file.name}（${fmtBytes(event.loaded)} / ${fmtBytes(event.total)}）`;
    };
    xhr.onload = () => {
      state.xhr = null;
      if (xhr.status === 202) resolve();
      else reject(new Error(errorMessage(xhr)));
    };
    xhr.onerror = () => {
      state.xhr = null;
      reject(new Error('网络错误'));
    };
    xhr.onabort = () => {
      state.xhr = null;
      reject(new Error('已取消上传'));
    };
    xhr.send(form);
  });
}

async function startUploads() {
  if (state.uploading || !state.pending.length) return;
  const queue = state.pending.slice();
  state.pending = [];
  state.uploading = true;
  $('start').disabled = true;
  $('uploadRow').hidden = false;
  $('uploadBar').style.width = '0%';
  $('abortUpload').hidden = false;
  updateFileBar();

  const errors = [];
  for (let index = 0; index < queue.length; index += 1) {
    try {
      await uploadOne(queue[index], index + 1, queue.length);
    } catch (err) {
      errors.push(`${queue[index].name}：${err.message}`);
      if (err.message === '已取消上传') break;
    }
  }

  state.uploading = false;
  $('abortUpload').hidden = true;
  $('file').value = '';
  $('uploadBar').style.width = errors.length ? '0%' : '100%';
  $('uploadStatus').textContent = errors.length
    ? errors.join('；')
    : `已提交 ${queue.length} 个文件，下面的任务列表里能看到进度。`;
  await refreshJobs();
  updateFileBar();
}

// ------------------------------------------------------------------ 任务

function metaLine(job) {
  const parts = [];
  if (job.percent !== null && job.percent !== undefined) {
    parts.push(el('span', {}, el('b', { text: `${job.percent}%` })));
    parts.push(el('span', { class: 'sep' }));
  }
  if (job.total) parts.push(el('span', { class: 'num', text: `${job.done} / ${job.total} 片段` }));
  if (job.started) parts.push(el('span', { class: 'num', text: `已用 ${fmtClock(job.elapsed)}` }));
  if (job.status === 'running' && job.percent !== null && job.done > 0 && job.total) {
    const remaining = (job.elapsed / job.done) * (job.total - job.done);
    parts.push(el('span', { class: 'num', text: `预计还剩 ${fmtClock(remaining)}` }));
  }
  return parts;
}

function renderActive(job) {
  const card = $('activeCard');
  if (!job) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $('activeName').textContent = job.filename;
  $('activeModel').textContent = `Transkun ${job.model}`;
  $('activeElapsed').textContent = fmtClock(job.elapsed);
  $('activeBar').style.width = `${job.percent === null ? 30 : job.percent}%`;
  $('activeBar').classList.toggle('busy', true);
  $('activeMeta').replaceChildren(...metaLine(job));
  $('activeLog').textContent = (job.log || []).join('\n');
  $('activeCancel').onclick = () => {
    cancelJob(job.id).catch((err) => alert(err.message));
  };
}

function rollSvg(roll) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 320 40');
  svg.setAttribute('class', 'rollsvg');
  for (const [x, y, w] of roll) {
    const rect = document.createElementNS(NS, 'rect');
    rect.setAttribute('x', (x * 320).toFixed(1));
    rect.setAttribute('y', (2 + (1 - y) * 34).toFixed(1));
    rect.setAttribute('width', Math.max(1.6, w * 320).toFixed(1));
    rect.setAttribute('height', '3.6');
    rect.setAttribute('rx', '1.8');
    rect.setAttribute('fill', ROLL_COLORS[Math.min(ROLL_COLORS.length - 1, Math.floor(y * ROLL_COLORS.length))]);
    svg.append(rect);
  }
  return svg;
}

function statusChip(job) {
  const className = {
    queued: 'chip wait', running: 'chip run', done: 'chip ok',
    failed: 'chip bad', cancelled: 'chip',
  }[job.status] || 'chip';
  let text = STATUS_TEXT[job.status] || job.status;
  if (job.status === 'queued' && job.queue_position) {
    text += ` · 第 ${job.queue_position} 位`;
  }
  return el('span', { class: className, text });
}

function renderJob(job) {
  const row = el('div', { class: 'qrow' });
  row.append(el('div', { class: 'line1' },
    el('span', { class: 'name', text: job.filename }),
    el('span', { class: 'chip', text: `Transkun ${job.model}` }),
    statusChip(job),
  ));

  if (job.status === 'queued') {
    row.append(el('div', { class: 'bar slim' }, el('i')));
  }

  if (job.status === 'done' && Array.isArray(job.roll) && job.roll.length) {
    row.append(rollSvg(job.roll));
  }

  const info = [];
  if (job.created) {
    info.push(el('span', { class: 'num', text: fmtTime(job.created) }));
  }
  if (job.status === 'done' || job.status === 'failed' || job.status === 'cancelled') {
    info.push(el('span', { class: 'num', text: fmtDuration(job.elapsed) }));
    if (job.notes !== null && job.notes !== undefined) {
      info.push(el('span', { text: '·' }));
      info.push(el('span', { class: 'num', text: `${job.notes} 个音符` }));
    }
    if (job.pedals) {
      info.push(el('span', { text: '·' }));
      info.push(el('span', { class: 'num', text: `${job.pedals} 段踏板` }));
    }
  }

  const acts = [];
  if (job.raw) {
    acts.push(el('a', { class: 'btn small', href: `/api/jobs/${job.id}/midi?kind=raw`, download: '' }, '原始 MIDI'));
  }
  if (job.rearranged) {
    acts.push(el('a', { class: 'btn small soft', href: `/api/jobs/${job.id}/midi?kind=rearranged`, download: '' }, '整理版'));
  }
  if (job.status === 'queued' || job.status === 'running') {
    acts.push(el('button', { class: 'btn small ghost', type: 'button', text: '取消', onclick: () => cancelJob(job.id).catch((err) => alert(err.message)) }));
  } else {
    acts.push(el('button', {
      class: 'btn small',
      type: 'button',
      text: '重新转谱',
      title: '用当前「转谱选项」再跑一遍，不用重新上传',
      onclick: () => retryJob(job.id).catch((err) => alert(err.message)),
    }));
    acts.push(el('button', { class: 'btn small ghost danger', type: 'button', text: '删除', onclick: () => deleteJob(job.id).catch((err) => alert(err.message)) }));
  }

  const line2 = el('div', { class: 'line2' }, ...info);
  if (job.rearrange_detail) {
    line2.append(el('span', { text: `· ${job.rearrange_detail}` }));
  }
  line2.append(el('span', { class: 'acts' }, ...acts));
  row.append(line2);

  if (job.error) {
    row.append(el('div', { class: 'error', text: job.error }));
  }
  if (job.log && job.log.length) {
    row.append(el('details', { class: 'log' },
      el('summary', { text: '日志' }),
      el('pre', { text: job.log.join('\n') })));
  }
  return row;
}

function renderJobs(jobs) {
  state.jobs = jobs;
  const running = jobs.find((job) => job.status === 'running');
  renderActive(running || null);
  const rest = jobs.filter((job) => job.status !== 'running');
  $('jobs').replaceChildren(...rest.map(renderJob));
  $('emptyJobs').hidden = jobs.length > 0;
  $('queuePill').textContent = String(jobs.length);
}

async function retryJob(id) {
  const query = new URLSearchParams({
    model: MODEL,
    rearrange: $('rearrange').checked ? 'true' : 'false',
    velocity: $('velocity').value || '30',
    min_ms: $('minMs').value || '30',
    chord_ms: $('chordMs').value || '60',
    repeat_ms: $('repeatMs').value || '120',
  });
  await apiJson(await api(`/api/jobs/${id}/retry?${query}`, { method: 'POST' }));
  await refreshJobs();
}

async function cancelJob(id) {
  await api(`/api/jobs/${id}/cancel`, { method: 'POST' });
  await refreshJobs();
}

async function deleteJob(id) {
  await api(`/api/jobs/${id}`, { method: 'DELETE' });
  await refreshJobs();
}

async function refreshJobs() {
  try {
    const response = await api('/api/jobs');
    const body = await response.json();
    renderJobs(body.jobs || []);
  } catch (err) {
    // 网络抖动时保留上一屏，下一轮再试。
  }
}

// ------------------------------------------------------------------ 启动

function buildEq() {
  const box = $('eq');
  if (!box) return;
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < 36; index += 1) {
    const bar = document.createElement('span');
    bar.style.animationDelay = `${(-index * 0.055).toFixed(2)}s`;
    bar.style.animationDuration = `${(0.9 + (index % 6) * 0.09).toFixed(2)}s`;
    fragment.append(bar);
  }
  box.replaceChildren(fragment);
}

function bind() {
  const drop = $('drop');
  const input = $('file');

  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      input.click();
    }
  });
  for (const type of ['dragenter', 'dragover']) {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add('over');
    });
  }
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    setPending(event.dataTransfer.files);
  });
  input.addEventListener('change', () => setPending(input.files));

  $('start').addEventListener('click', () => {
    startUploads().catch((err) => {
      $('uploadRow').hidden = false;
      $('uploadStatus').textContent = err.message;
    });
  });
  $('abortUpload').addEventListener('click', () => {
    if (state.xhr) state.xhr.abort();
  });
  $('rearrange').addEventListener('change', updateHint);
  $('theme').addEventListener('click', toggleTheme);

  for (const type of ['dragover', 'drop']) {
    document.addEventListener(type, (event) => {
      if (!drop.contains(event.target)) event.preventDefault();
    });
  }

  setInterval(() => {
    if (!document.hidden) refreshJobs();
  }, 1500);
}

async function main() {
  initTheme();
  buildEq();
  $('formats').replaceChildren(...FORMATS.map((ext) => el('span', { class: 'fmt', text: ext })));
  bind();
  updateFileBar();
  updateHint();
  try {
    const response = await api('/api/info');
    fillInfo(await response.json());
  } catch (err) {
    $('uploadRow').hidden = false;
    $('uploadStatus').textContent = `无法读取服务信息：${err.message}`;
  }
  updateFileBar();
  await refreshJobs();
}

main();
