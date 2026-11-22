"""Persistent Python 3.12 model worker. Only the ROS node writes its stdin.

The local file protocol never crosses the container boundary. stdout is JSONL;
model/library output goes to stderr. Weights are pre-provisioned, never fetched.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback


def emit(value):
    sys.stdout.write(json.dumps(value, allow_nan=False)+'\n')
    sys.stdout.flush()


def main():
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        from PIL import Image
        import torch
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
        checkpoint = Path(os.environ['SAM3_CHECKPOINT'])
        if not checkpoint.is_file():
            raise RuntimeError('SAM3_CHECKPOINT must be a local authorized checkpoint')
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA GPU unavailable; check Docker NVIDIA runtime')
        # Turing (e.g. RTX 2080 Ti) has FP16 tensor cores, not native BF16.
        amp_dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
        digest = hashlib.sha256()
        with checkpoint.open('rb') as stream:
            for chunk in iter(lambda: stream.read(8*1024*1024), b''):
                digest.update(chunk)
        model = build_sam3_image_model(checkpoint_path=str(checkpoint), load_from_HF=False, device='cuda')
        processor = Sam3Processor(model, device='cuda')
    emit({'state': 'WARMING_UP'})
    # Full image and text inference, before the node accepts any requests.
    with contextlib.redirect_stdout(sys.stderr), torch.inference_mode(), torch.autocast('cuda', dtype=amp_dtype):
        state = processor.set_image(Image.new('RGB', (256, 256)))
        processor.set_text_prompt(prompt='object', state=state)
        torch.cuda.synchronize()
    emit({'state': 'READY', 'precision': str(amp_dtype), 'checkpoint_sha256': digest.hexdigest(),
          'revision': Path('/opt/sam3-model-revision').read_text().strip()})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode(), torch.autocast('cuda', dtype=amp_dtype):
                folder = Path(request['folder'])
                image = Image.open(folder/'input.png').convert('RGB')
                processor.set_confidence_threshold(request['score_threshold'])
                state = processor.set_image(image)
                output = processor.set_text_prompt(prompt=request['text_prompt'], state=state)
                if not torch.isfinite(output['scores']).all() or not torch.isfinite(output['boxes']).all():
                    raise RuntimeError('non-finite model scores or boxes')
                indices = output['scores'].argsort(descending=True)[:request['max_instances']]
                masks = output['masks'][indices].squeeze(1).cpu().numpy().astype(np.uint8)*255
                boxes = output['boxes'][indices].cpu().tolist()
                scores = output['scores'][indices].cpu().tolist()
                instances = []
                overlay = np.array(image).copy()
                (folder/'masks').mkdir(exist_ok=True)
                for i, (mask, box, score) in enumerate(zip(masks, boxes, scores)):
                    Image.fromarray(mask).save(folder/'masks'/f'{i}.png')
                    active = mask > 0
                    overlay[active] = (overlay[active].astype(np.float32)*0.55 + np.array([0,255,80])*0.45).astype(np.uint8)
                    instances.append(dict(instance_id=i, score=score, box_xyxy=box))
                Image.fromarray(overlay).save(folder/'overlay.png')
                torch.cuda.synchronize()
            emit({'instances': instances})
        except Exception as exc:
            emit({'error': type(exc).__name__+': '+str(exc)})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({'state': 'FAILED', 'error': type(exc).__name__+': '+str(exc)})
        sys.exit(1)
