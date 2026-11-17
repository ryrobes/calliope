# Computer scorer provenance

Imported from `clover-zoo-computer-poc`, commit `ffeced9`, retaining its pinned
base/adapter revisions and adapter-tensor verification. Packaging changes add
worker bearer authentication and readiness checks for downloaded model files.

`vendor/four_b.py` is the Cua-S1 prompt and scoring implementation distributed
with [cua-ai/cua-s1-4b-0.2](https://huggingface.co/cua-ai/cua-s1-4b-0.2).
Its MIT software license is preserved in `vendor/LICENSE.cua-s1`.
The [Qwen3.5-4B base model](https://huggingface.co/Qwen/Qwen3.5-4B) is downloaded
separately at the immutable revision recorded in `models.py`.

This service selects among supplied action candidates. It does not operate a
desktop, launch browsers, or provide the complete computer-use harness.
