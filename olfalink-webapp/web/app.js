import { getDashboard } from '/assets/api.js';

const byId = (id) => document.getElementById(id);
const levelLabels = ['なし', 'ごく弱い', '弱い', '中程度', '強い', '非常に強い'];
const timeFormatter = new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const descriptorNames = new Map([
  ['sweet', '甘い'], ['sour', '酸っぱい'], ['pungent', '刺激的'],
  ['fruity', 'フルーティー'], ['floral', '花のような'], ['woody', '木のような'],
  ['minty', 'ミントのような'], ['smoky', '煙のような']
]);

let busy = false;
let hasMeasurement = false;
let toastTimer;

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function toast(message) {
  const target = byId('toast');
  target.textContent = message;
  target.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => target.classList.remove('visible'), 3000);
}

function showIntensity(level) {
  const valid = Number.isInteger(level) && level >= 0 && level <= 5;
  setText('intensity-value', valid ? String(level) : '—');
  byId('intensity-value').classList.toggle('is-empty', !valid);
  setText('intensity-word', valid ? levelLabels[level] : '未取得');
  const meter = byId('level-steps');
  if (valid) meter.setAttribute('aria-valuenow', String(level));
  else meter.removeAttribute('aria-valuenow');
  meter.querySelectorAll('.step').forEach((step, index) => {
    step.classList.toggle('filled', valid && index < level);
  });
}

function setFeatures(features) {
  const list = byId('features');
  const chips = byId('feature-chips');
  chips.replaceChildren();
  list.hidden = features.length === 0;
  for (const [index, name] of features.entries()) {
    const item = document.createElement('span');
    item.className = `chip ${index % 2 ? 'chip-gold' : 'chip-green'}`;
    item.textContent = name;
    chips.append(item);
  }
}

function showAnalysis(analysis, validReading, latestId) {
  const ready = validReading && analysis?.status === 'ready' &&
    typeof analysis.message === 'string' && analysis.message.trim() !== '' &&
    Array.isArray(analysis.evidence?.source_reading_ids) &&
    analysis.evidence.source_reading_ids.includes(latestId);

  const body = byId('description-body');
  body.hidden = true;
  body.textContent = '';

  if (ready) {
    setText('description-title', analysis.message);
    const scores = analysis.evidence?.descriptor_scores || {};
    const features = Object.entries(scores)
      .filter(([key, score]) => descriptorNames.has(key) && typeof score === 'number' && Number.isFinite(score) && score >= 0 && score <= 1)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([key]) => descriptorNames.get(key));
    setFeatures(features);
    return;
  }

  setFeatures([]);
  const unavailableMessage = analysis?.status === 'not_available' && typeof analysis.message === 'string' ? analysis.message : null;
  setText('description-title', unavailableMessage || (validReading ? 'においの特徴を解析できません。' : '測定結果を待っています。'));
}

function render(payload) {
  const latest = payload?.latest;
  const historical = payload?.mode === 'aws';
  const validReading = payload?.mode !== 'demo' &&
    (historical || payload?.device?.connected === true) &&
    latest?.source === 'device' &&
    Number.isInteger(latest.intensity) && latest.intensity >= 0 && latest.intensity <= 5;

  hasMeasurement = validReading;
  showIntensity(validReading ? latest.intensity : null);
  showAnalysis(payload?.analysis, validReading, latest?.id);

  byId('status-dot').classList.toggle('is-live', validReading && !historical);
  setText('status-label', historical ? (validReading ? '保存済み測定を解析' : 'AWS保存データを確認中') : (validReading ? '測定中' : '接続待ち'));
  const updated = validReading && latest.timestamp ? new Date(latest.timestamp) : null;
  setText('updated-at', updated && !Number.isNaN(updated.getTime()) ? `${historical ? '測定日時' : '最終取得'} ${timeFormatter.format(updated)}` : '');
}

async function refresh({ notify = false } = {}) {
  if (busy) return;
  busy = true;
  byId('refresh').disabled = true;
  try {
    render(await getDashboard());
    if (notify) toast('更新しました');
  } catch (error) {
    console.error('OlfaLink data refresh failed', error);
    if (!hasMeasurement) {
      showIntensity(null);
      showAnalysis(null, false, null);
    }
    byId('status-dot').classList.remove('is-live');
    setText('status-label', '通信できません');
    if (notify) toast('更新できませんでした');
  } finally {
    busy = false;
    byId('refresh').disabled = false;
  }
}

byId('refresh').addEventListener('click', () => refresh({ notify: true }));
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 30000);
