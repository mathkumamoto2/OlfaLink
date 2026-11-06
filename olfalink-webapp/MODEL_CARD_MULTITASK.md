# Model Card: OlfaLink multitask 1D-CNN v0.7

**Version:** olfalink-multitask-1dcnn-v0.7-research

## Prediction contract

One entire multi-sensor CSV → shared Conv1d encoder → 7-class odor head (CrossEntropyLoss) + 0–5 scalar intensity head (SmoothL1Loss). Total training loss = classification loss + 0.5 × intensity loss.

Class IDs and Japanese names are in `ml/cnn_multitask.py`: room (室内の空気), vinegar (酢), shoe (靴), ammonia (アンモニア), tabasco (タバスコ), sardine_oil (イワシ油), food_waste (生ごみ).

**Preprocessing:** `ml/session_timeseries_v06.py`. Seven physiological/sensor channels baseline-centered + three phase masks, phasewise median downsampling to 24+64+40=128 samples. Tensors shape N×10×128. No filenames, dates, human labels, or device identifiers as model features. 23/40 column CSV schemas accepted. `refresh` before `exposure` is pre-exposure background. If there is no recovery after exposure, the recovery mask remains absent rather than fabricated. Inputs with absent exposure or excessive physically implausible readings are rejected.

## Data lineage / leakage

11 source CSVs, synthetic augmentation ×100. For supervised training: 9 usable original source recordings → 869 synthetic sessions. The nine source recordings **are not independent repetitions for every class/intensity**. 7 odor classes; levels 0,2,3,4,5; no intensity 1. Each synthetic variant inherits the original class and intensity. The 100 synthetic variants of ROOM_R01 (no exposure), 100 VINEGAR_V01 (unlabeled intensity), and 31 invalid NH3 variants are not used.

**Diagnostic:** Source-grouped, predeclared holdout of ROOM_R02 and CH3COOH_Z05 (200 synthetic variants); diagnostic train 669. No derived copy of either held-out source in diagnostic train. Model trained 32 epochs. Held-out odor accuracy **0.0000**, intensity MAE **1.5888**, and joint rounded accuracy **0.0000**. These 200 synthetic variants derive from just **2 original sessions**, so not an independent-real performance estimate. Other classes lack enough independent origins to estimate unseen-session classification reliably.

**Final checkpoint:** trained 23 epochs on all 869 usable synthetic sessions. Its fitted training set shows odor accuracy 100%, intensity MAE 0.0368, joint rounded match 100%. These are **in-sample fits**, not validation. Retraining source split is available in `ml/train_cnn_multitask.py`.

## Risks

- Poor out-of-origin transfer demonstrated by diagnostic test (0% accuracy).
- Strong odor/strength confounding and single-source-per-class for five of seven classes.
- Model must produce some of seven known labels for any valid input; **no unknown detector**.
- Softmax scores are **uncalibrated**, cannot be interpreted as reliable confidence.
- No human-observer validated intensity 1, and all subjective intensity labels may vary by person, distance and concentration.
- Sensor cross-device variation, temporal drift, humidity and temperature distribution shift.
- This model must not inform environmental health, safety, chemical identity or medical decisions.

## Integration

- `predicted_intensity_0_5` → research UI, alongside prominent experiment-only warning.
- `llm_input.predicted_odor_label` → future LLM explanation service. The model output is a **hypothesis**; LLM should explain known general characteristics conditionally and must not claim a measured volatile compound.
- Actual LLM request/response generation is **not implemented**; setting an LLM key does not silently invoke any provider.
