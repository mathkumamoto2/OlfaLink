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
  const isDemo = latest?.source === 'demo' && analysis?.input_origin === 'synthetic_demo';
  const valid = (latest?.source === 'device' || isDemo) &&
    Number.isInteger(latest.intensity) && latest.intensity >= 0 && latest.intensity <= 5 &&
    analysis?.evidence?.source_reading_ids?.includes(latest.id);
  showIntensity(valid ? latest.intensity : null);
  const llmDemo = isDemo && analysis?.generated_by_llm === true;
  byId('demo-warning').hidden = !isDemo;
  const adcCorrected = valid && !isDemo && payload?.features?.sensor_correction_applied === true;
  byId('correction-warning').hidden = !adcCorrected;
  const demoHeadline = byId('demo-warning')?.querySelector('strong');
  if (demoHeadline) demoHeadline.textContent = llmDemo ? '合成データによるLLMデモ' : '合成データで判定しました';
  const prefix = isDemo ? '【合成デモ・実測ではありません】' : '';
  const odor = typeof analysis?.predicted_odor_ja === 'string'
    ? analysis.predicted_odor_ja : '';
  if (valid && odor) {
    setText('description-title', `${prefix}CNNの推定：${odor}（強度 ${latest.intensity}/5）`);
    byId('description-body').textContent = analysis.message || '';
    byId('description-body').hidden = !analysis.message;
    const provenance = analysis.description_source === 'llm' ?
      (isDemo ? '生成元：Qwen＋LoRA（合成データからのCNN予測に基づくデモ）' : '生成元：Qwen＋LoRA（LLM）') :
      analysis.description_source === 'dictionary' ? '生成元：OlfaLink説明辞書（参考情報）' :
      analysis.description_source === 'no_signal' ? '生成元：センサー反応に基づく定型メッセージ' :
      '生成元：確認できません';
    setText('description-provenance', provenance);
    byId('description-provenance').hidden = false;
  } else {
    setText('description-title', valid ? analysis.message :
      (analysis?.message || '解析ボタンを押すと、測定結果を表示します。'));
    byId('description-body').hidden = true;
    byId('description-body').textContent = '';
    setText('description-provenance', '');
    byId('description-provenance').hidden = true;
  }
  setFeatures(valid && analysis?.status === 'ready' ? analysis.evidence?.descriptor_scores : {});
  byId('status-dot').classList.toggle('is-live', freshResult && valid);
  setText('status-label', isDemo ? 'デモデータで判定（実測ではありません）' :
    adcCorrected ? '補正あり・暫定CNN推論' :
    freshResult && valid ? 'CNN推論完了' : (valid ? '前回の解析結果' : '解析待ち'));
  const stamp = valid ? new Date(latest.timestamp) : null;
  setText('updated-at', stamp && Number.isFinite(stamp.getTime()) ?
    `${isDemo ? '合成日時' : '測定日時'} ${timeFormatter.format(stamp)}` : '');
}

const action = byId('analyze-now');
const actionMessage = byId('analysis-action-message');
let inFlight = false;
let inferenceRequested = false;

async function analyze() {
  if (inFlight) return;
  inferenceRequested = true;
  inFlight = true;
  action.disabled = true;
  action.classList.add('is-busy');
  action.textContent = '判定中です…';
  action.setAttribute('aria-busy', 'true');
  byId('analysis-progress').hidden = false;
  byId('result-cards').hidden = true;
  byId('demo-warning').hidden = true;
  byId('correction-warning').hidden = true;
  setText('status-label', '判定中です');
  byId('status-dot').classList.remove('is-live');
  actionMessage.textContent = 'AWSから直近の測定値を取得しています。';
  try {
    const result = await inferNow();
    render(result, { freshResult: true });
    actionMessage.textContent = result.latest
      ? (result.analysis?.input_origin === 'synthetic_demo' ? (result.analysis?.generated_by_llm ? '合成データでCNN推論し、LLM説明を生成しました。実測結果ではありません。' : 'デモ用の合成値でCNN推論しました。実測結果ではありません。') : result.analysis?.description_source === 'llm'
        ? '判定が完了しました。LLMの説明を表示しています。'
        : result.analysis?.description_source === 'dictionary'
          ? '判定が完了しました。LLMの代わりに説明辞書を使用しました。'
          : 'CNNによる判定が完了しました。')
      : '推論結果を取得できませんでした。';
  } catch (error) {
    actionMessage.textContent = error.message || '解析できませんでした。';
    setText('status-label', '判定できませんでした');
  } finally {
    byId('analysis-progress').hidden = true;
    byId('result-cards').hidden = false;
    action.removeAttribute('aria-busy');
    inFlight = false;
    action.disabled = false;
    action.classList.remove('is-busy');
    action.textContent = 'においを解析する';
  }
}
action.addEventListener('click', analyze);

// Initial read only; no interval and no inference until the button is clicked.
getDashboard().then(payload => {
  if (!inferenceRequested) render(payload);
}).catch(() => {
  if (!inferenceRequested) {
    render(null);
    actionMessage.textContent = '以前の解析結果を読み込めませんでした。';
  }
});
