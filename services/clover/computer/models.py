"""Immutable model revisions shared by download and inference."""
import os
from pathlib import Path

BASE_ID = 'Qwen/Qwen3.5-4B'
BASE_REVISION = '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'
ADAPTER_ID = 'cua-ai/cua-s1-4b-0.2'
ADAPTER_REVISION = '16818868b0cc7813808aae4e87b417657046ab79'
ROOT = Path(os.environ.get('COMPUTER_MODEL_ROOT', '/models'))
VERSION = 'cua-s1-4b-0.2/' + ADAPTER_REVISION[:12]


def download():
    from huggingface_hub import snapshot_download
    for name, revision, target in [(BASE_ID, BASE_REVISION, 'base'), (ADAPTER_ID, ADAPTER_REVISION, 'adapters')]:
        snapshot_download(name, revision=revision, local_dir=ROOT / target,
                          allow_patterns=['*.json', '*.safetensors', '*.jinja', '*.txt', '*.model', 'text/*', 'multimodal/*'])


if __name__ == '__main__':
    download()
