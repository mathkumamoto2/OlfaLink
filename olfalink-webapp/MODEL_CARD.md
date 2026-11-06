# Model Card — OlfaLink 1D-CNN intensity v0.6

- **Status:** Research prototype; not validated for operational deployment.
- **Task:** Regression of 0–5 subjective odor intensity from one whole multichannel sensor recording.
- **Architecture:** Four 1D convolutions with GroupNorm and GELU, phase-based mean/max pooling, small regression head.
- **Model input:** Seven baseline-centered channels + three phase-present indicators, each 128 temporal bins (shape N×10×128).
- **File schemas:** Both 40-column older ESP1 recordings and 23-column newer ESP1 recordings, handled without identity/timestamp/filename inputs.
- **Training:** 869 quality-checked noise-augmented recordings from **nine** distinct underlying real sessions. Recorded labels: 0, 2, 3, 4, 5. No measured label 1.
- **Exclusions:** 100 unlabeled vinegar variants, 100 missing-exposure room variants, 31 implausible noisy NH3 variants.
- **Unusual food-waste recording:** baseline -> refresh -> exposure; the pre-exposure refresh belongs to the baseline context. No after-exposure recovery is fabricated: recovery signal and mask are zero.
- **Diagnostic split:** Entire real measurement origins MYROOM_Z01, SHOE_Z03, FOOD_WASTE_F01 held out, with all their augmentations. Source-holdout raw real n=3: MAE=2.1108 and 0/3 exact integer predictions. This shows poor transfer to held-out measurement origins.
- **Final model:** Refit on all 869 usable synthetics, so training fit is not validation. Training rounded exact match 100% is not a generalization estimate.
- **Known risks:** source/class/strength confounding, calibration drift, sensor hardware differences, small real sample count, potential domain gap between synthetic noise and future real measurements.
- **Safety:** Not for air-quality alerts, healthcare, leak detection, odor health-risk decisions, or validated human perception judgments.
- **Research follow-up:** Multiple independent intensities for same odor across days, instruments, people and environments; source-grouped evaluation.
