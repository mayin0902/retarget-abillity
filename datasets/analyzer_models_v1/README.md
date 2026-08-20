# Analyzer model audit v1

This manifest pins the exact local-only detector assets used by the shared
protection analysis. All assets come from the official OpenCV Zoo repository.
Model bytes are downloaded into the Git-ignored `models/analyzers/` directory;
Git stores only this audit manifest, the downloader, and implementation code.

The analyzer performs four complementary passes:

- PPOCRv3 text detection plus Chinese CRNN recognition;
- YuNet face detection;
- YOLOX COCO person, product, and foreground-object detection;
- deterministic compact logo-candidate detection, fused with OCR wordmarks and
  detected product regions. It protects logo-like regions but does not claim
  brand identity recognition.

Run `python scripts/materialize_analyzer_models.py` before a configuration that
sets `analysis.detector_mode: required`. The script rejects changed hashes,
unexpected byte sizes, non-HTTPS URLs, and unapproved download hosts.
