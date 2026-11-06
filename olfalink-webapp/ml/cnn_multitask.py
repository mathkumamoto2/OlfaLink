"""OlfaLink v0.7 research multitask CNN: one CSV -> odor class + intensity.

Training examples are FULL independent-origin measurements; noisy variants are
NOT independent measurements. Labels and session identity never enter features.
"""
from __future__ import annotations
from pathlib import Path
from collections.abc import Sequence, Mapping
import numpy as np
import torch
from torch import nn
from ml.cnn_intensity import fit_channel_scales, prepare_inputs, PHASE_LENGTHS, TIMESTEPS
from ml.session_timeseries_v06 import CHANNELS, PHASE_BINS, parse_csv, session_trace

MODEL_VERSION = 'olfalink-multitask-1dcnn-v0.7-research'
CLASS_LABELS = ('room','vinegar','shoe','ammonia','tabasco','sardine_oil','food_waste')
CLASS_NAMES_JA = ('室内の空気','酢','靴','アンモニア','タバスコ','イワシ油','生ごみ')
WARNING = ('研究用の推定結果です。学習元は9実測セッションのノイズ拡張であり、'
           '未知の実測への分類精度・強度推定精度は未確立です。強度1は未学習。'
           '未学習のにおいも既知7種類のいずれかに分類されるため、物質の特定や安全性判断には使えません。')

class MultiTaskCNN(nn.Module):
    def __init__(self, n_classes: int = len(CLASS_LABELS)):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(10,24,kernel_size=5,padding=2), nn.GroupNorm(4,24),nn.GELU(),
            nn.Conv1d(24,32,kernel_size=5,padding=2), nn.GroupNorm(4,32),nn.GELU(),
            nn.Conv1d(32,48,kernel_size=5,padding=2), nn.GroupNorm(6,48),nn.GELU(),
            nn.Conv1d(48,48,kernel_size=3,padding=1), nn.GroupNorm(6,48),nn.GELU(),
        )
        self.shared = nn.Sequential(nn.Linear(48*2*3,64), nn.GELU(), nn.Dropout(0.1))
        self.classifier = nn.Linear(64,n_classes)
        self.regressor = nn.Linear(64,1)

    def forward(self,x: torch.Tensor):
        if x.ndim != 3 or x.shape[1:] != (10,TIMESTEPS):
            raise ValueError('Expected a batch of (10,128) session traces')
        z=self.encoder(x)
        pooled=[]
        off=0
        for count in PHASE_LENGTHS:
            segment=z[:,:,off:off+count]
            pooled.extend((segment.mean(dim=-1),segment.amax(dim=-1)))
            off+=count
        shared=self.shared(torch.cat(pooled,dim=1))
        return self.classifier(shared), self.regressor(shared).squeeze(1)


def save_model(path:Path,model:MultiTaskCNN,scales:np.ndarray,metadata:dict)->None:
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    torch.save({'version':MODEL_VERSION,'state_dict':{k:v.detach().cpu() for k,v in model.state_dict().items()},
                'channel_scales':torch.tensor(scales,dtype=torch.float32),'channels':list(CHANNELS),
                'phase_bins':dict(PHASE_BINS),'classes':list(CLASS_LABELS),'metadata':metadata},path)


def load_model(path:Path)->tuple[MultiTaskCNN,np.ndarray,dict]:
    data=torch.load(Path(path),map_location='cpu',weights_only=True)
    if data.get('version')!=MODEL_VERSION or data.get('channels')!=list(CHANNELS) or data.get('phase_bins')!=PHASE_BINS or data.get('classes')!=list(CLASS_LABELS):
        raise ValueError('Incompatible model checkpoint or preprocessing schema')
    model=MultiTaskCNN();model.load_state_dict(data['state_dict'],strict=True);model.eval()
    scales=data['channel_scales'].detach().cpu().numpy().astype(np.float32)
    if scales.shape!=(len(CHANNELS),) or not np.isfinite(scales).all() or np.any(scales<=0):
        raise ValueError('Invalid stored scales')
    return model, scales, data['metadata']


@torch.inference_mode()
def predict_trace(model:MultiTaskCNN,scales:np.ndarray,trace:np.ndarray)->dict:
    x=torch.from_numpy(prepare_inputs(trace,scales))
    logits,raw_strength=model(x)
    prob=torch.softmax(logits,dim=1)[0].detach().cpu().numpy()
    i=int(prob.argmax())
    raw=float(raw_strength.item())
    if not np.isfinite(prob).all() or not np.isfinite(raw):
        raise ValueError('Nonfinite prediction')
    strength=float(np.clip(raw,0,5))
    top=np.argsort(-prob)[:3]
    return {
        'predicted_odor_label':CLASS_LABELS[i],
        'predicted_odor_ja':CLASS_NAMES_JA[i],
        'classification_score_uncalibrated':round(float(prob[i]),4),
        'top3_candidates':[{'label':CLASS_LABELS[int(j)],'name_ja':CLASS_NAMES_JA[int(j)],
                            'score_uncalibrated':round(float(prob[j]),4)} for j in top],
        'predicted_intensity_continuous':round(strength,3),
        'predicted_intensity_0_5':int(np.floor(strength+0.5)),
        'model_version':MODEL_VERSION,
        'unseen_odor_detection_supported':False,
        'independent_real_validation':False,
        'observed_intensity_levels':[0,2,3,4,5],
        'warning_ja':WARNING,
        # Hand this grounded classification hypothesis, not raw sensor signals,
        # to an LLM. No LLM call is made inside this function.
        'llm_input':{
           'predicted_odor_label':CLASS_LABELS[i],
           'predicted_odor_ja':CLASS_NAMES_JA[i],
           'classification_status':'experimental_unverified',
           'instruction_hint':'予測ラベルから一般的なにおいの特徴を日本語で説明する。実測していない成分・安全性を断定しない。',
        },
    }


def predict_csv_text(model:MultiTaskCNN,scales:np.ndarray,csv_text:str)->dict:
    trace,counts=session_trace(parse_csv(csv_text,'inference csv'))
    result=predict_trace(model,scales,trace)
    result['phase_row_counts']=counts
    return result
