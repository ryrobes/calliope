"""Cua's published closed-choice prompt/readout with verified adapter loading."""
import base64
from dataclasses import asdict
import io
import time

from models import ROOT, VERSION, BASE_REVISION, ADAPTER_REVISION
from vendor.four_b import FourBModel, Option, assign_letters, build_prompt


class Scorer:
    def __init__(self, modality):
        import torch
        from peft import get_peft_model_state_dict
        from safetensors.torch import load_file
        start = time.monotonic()
        self.modality = modality
        self.backend = FourBModel(base_model=str(ROOT / 'base'), lora_adapter_path=ROOT / 'adapters',
                                 device='cuda', dtype='bfloat16', modality=modality)
        self.backend.load()
        # PEFT can warn and continue with missing keys. Verify every published
        # adapter tensor against what is attached to the actual model instead.
        expected = load_file(str(ROOT / 'adapters' / modality / 'adapter_model.safetensors'))
        actual = get_peft_model_state_dict(self.backend._model)
        if set(expected) != set(actual):
            raise RuntimeError(f'Adapter key mismatch: missing={len(set(expected)-set(actual))}, extra={len(set(actual)-set(expected))}')
        for name, value in expected.items():
            loaded = actual[name].detach().cpu()
            if not torch.equal(loaded, value.to(loaded.dtype)):
                raise RuntimeError('Adapter tensor did not load: ' + name)
        self.adapter_tensors = len(expected)
        self.load_ms = (time.monotonic() - start) * 1000

    def score(self, request):
        import torch
        from PIL import Image
        start = time.monotonic()
        options = [Option(element_id=item.id, role=item.role, label=item.label,
                          action=item.action, entity_id=item.entity_id) for item in request.options]
        assignment = assign_letters(options)
        ids = self.backend._letter_token_ids(assignment)
        messages = build_prompt(assignment, app=request.app, task_family=request.task_family,
                                goal=request.goal, ax_tree=request.observation,
                                screenshot='attached' if request.screenshot else None, modality=self.modality)
        if self.modality == 'multimodal':
            raw = base64.b64decode(request.screenshot, validate=True)
            image = Image.open(io.BytesIO(raw))
            if image.width * image.height > 8_000_000:
                raise ValueError('Screenshot exceeds 8 megapixels')
            image = image.convert('RGB')
            processor = self.backend._processor
            prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = processor(text=[prompt], images=[image], return_tensors='pt')
        else:
            tokenizer = self.backend._tokenizer
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer(prompt, return_tensors='pt')
        tokens = inputs['input_ids'].shape[-1]
        if tokens > 8192:
            raise ValueError('Observation and candidates exceed 8192 model tokens; narrow the observation')
        inputs = inputs.to(self.backend._model.device)
        torch.cuda.synchronize()
        inference_start = time.monotonic()
        with torch.inference_mode():
            # Same final-position letter logits as upstream, without allocating
            # a full 248K-vocabulary logit matrix for every observation token.
            logits = self.backend._model(**inputs, logits_to_keep=1, use_cache=False).logits[0, -1]
            scores = torch.softmax(logits[torch.tensor(ids, device=logits.device)].float(), dim=-1).tolist()
        torch.cuda.synchronize()
        ranked = sorted([{'id': option.element_id, 'letter': letter, 'probability': probability}
                         for option, letter, probability in zip(options, assignment.letters, scores)],
                        key=lambda row: -row['probability'])
        return {'selected_id': ranked[0]['id'], 'scores': ranked, 'model_version': VERSION,
                'metadata': {'modality': self.modality, 'base_revision': BASE_REVISION,
                    'adapter_revision': ADAPTER_REVISION, 'verified_adapter_tensors': self.adapter_tensors,
                    'input_tokens': tokens, 'inference_ms': round((time.monotonic()-inference_start)*1000, 2),
                    'total_ms': round((time.monotonic()-start)*1000, 2),
                    'gpu_allocated_bytes': torch.cuda.memory_allocated()}}
