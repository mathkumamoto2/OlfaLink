import { getDashboard, inferNow } from '/assets/api.js';

const byId = id => document.getElementById(id);
const levelLabels = ['なし', 'ごく弱い', '弱い', '中程度', '強い', '非常に強い'];
const timeFormatter = new Intl.DateTimeFormat('ja-JP', { month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit' });
const descriptorNames = new Map([
  ['sweet','甘い'], ['sour','酸っぱい'], ['pungent','刺激的'],
  ['fruity','フルーティー'], ['floral','花のような'], ['woody','木のような'],
  ['minty','ミントのような'], ['smoky','煙のような']
]);

function setText(id, value) {
  const el = byId(id);
  if (el) el.textContent = value;
}
function showIntensity(value) {
  const valid = Number.isInteger(value) && value >= 0 && value <= 5;
  setText('intensity-value', valid ? String(value) : '—');
  byId('intensity-value').classList.toggle('is-empty', !valid);
  setText('intensity-word', valid ? levelLabels[value] : '未取得');
  const meter = byId('level-steps');
  if (valid) meter.setAttribute('aria-valuenow', String(value));
  else meter.removeAttribute('aria-valuenow');
  meter.querySelectorAll('.step').forEach((el, i) => el.classList.toggle('filled', valid && i < value));
}
function setFeatures(scores) {
  const entries = Object.entries(scores || {})
    .filter(([key, score]) => descriptorNames.has(key) && Number.isFinite(score) && score >= 0 && score <= 1)
    .sort((a,b) => b[1]-a[1]).slice(0,3);
  const target = byId('feature-chips');
  target.replaceChildren();
  for (const [i,[key]] of entries.entries()) {
    const el = document.createElement('span');
    el.className = `chip ${i % 2 ? 'chip-gold' : 'chip-green'}`;
    el.textContent = descriptorNames.get(key);
    target.append(el);
  }
  byId('features').hidden = entries.length === 0;
}
function render(payload, { freshResult = false } = {}) {
  const latest = payload?.latest;
  const analysis = payload?.analysis;
  const valid = latest?.source === 'device' &&
    Number.isInteger(latest.intensity) && latest.intensity >= 0 && latest.intensity <= 5 &&
    analysis?.status === 'ready' && analysis?.evidence?.source_reading_ids?.includes(latest.id);
  showIntensity(valid ? latest.intensity : null);
  setText('description-title', valid ? analysis.message :
    (analysis?.message || '解析ボタンを押すと、測定結果を表示します。'));
  byId('description-body').hidden = true;
  byId('description-body').textContent = '';
  setFeatures(valid ? analysis.evidence?.descriptor_scores : {});
  byId('status-dot').classList.toggle('is-live', freshResult && valid);
  setText('status-label', freshResult && valid ? '解析完了' : (valid ? '前回の解析結果' : '解析待ち'));
  const stamp = valid ? new Date(latest.timestamp) : null;
  setText('updated-at', stamp && Number.isFinite(stamp.getTime()) ? `測定日時 ${timeFormatter.format(stamp)}` : '');
}

const action = byId('analyze-now');
const actionMessage = byId('analysis-action-message');
let inFlight = false;

async function analyze() {
  if (inFlight) return;
  inFlight = true;
  action.disabled = true;
  action.classList.add('is-busy');
  action.textContent = '解析しています…';
  actionMessage.textContent = '直近30秒間の測定値を確認しています。';
  try {
    const result = await inferNow();
    render(result, { freshResult: true });
    actionMessage.textContent = '解析が完了しました。';
  } catch (error) {
    actionMessage.textContent = error.message || '解析できませんでした。';
  } finally {
    inFlight = false;
    action.disabled = false;
    action.classList.remove('is-busy');
    action.textContent = 'においを解析する';
  }
}
action.addEventListener('click', analyze);

// Initial read only; no interval and no inference until the button is clicked.
getDashboard().then(payload => render(payload)).catch(() => {
  render(null);
  actionMessage.textContent = '以前の解析結果を読み込めませんでした。';
});
