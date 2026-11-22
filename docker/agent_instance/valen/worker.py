"""Offline Valen worker; JSONL over local pipes, no ROS or robot implementation."""
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


def verify_model(root, expected=None):
    manifest = json.loads((root/'manifest.json').read_text())
    expected = expected if expected is not None else json.loads(Path(__file__).with_name('model.json').read_text())
    if manifest != expected:
        raise ValueError('model manifest differs from the image-pinned model')
    for item in manifest['files']:
        path = root/item['path']
        if path.parent != root or path.is_symlink() or not path.is_file() or path.stat().st_size != item['size']:
            raise ValueError('invalid model file: '+item['path'])
        digest = hashlib.new(item['algorithm'])
        if item['algorithm'] == 'sha1':
            digest.update(f"blob {item['size']}\0".encode())
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8*1024*1024), b''):
                digest.update(block)
        if digest.hexdigest() != item['digest']:
            raise ValueError('model checksum mismatch: '+item['path'])
    return manifest


def model_request(state, questions, image=None):
    # Serialize user state as text, so embedded paths/chat roles cannot become
    # media or instructions to the model's local compiler.
    content = [{'type': 'text', 'text': json.dumps(state, ensure_ascii=False, allow_nan=False)}]
    if image is not None:
        content.insert(0, {'type': 'image_url', 'image_url': {'url': str(image)}})
    return dict(state={'messages': [{'role': 'user', 'content': content}]}, questions=questions)


def main():
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from transformers import AutoModel
        from PIL import Image
        import tempfile
        root = Path(os.environ.get('VALEN_MODEL', '/models/Valen-Decision-1.5-4B'))
        manifest = verify_model(root)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA GPU unavailable; check Docker NVIDIA runtime')
        precision = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        model = AutoModel.from_pretrained(str(root), trust_remote_code=True, local_files_only=True,
                                         dtype=precision, attn_implementation='sdpa').to('cuda').eval()
        # Valen's trained readouts must stay FP32, including on FP16-only GPUs.
        model.head.float()
        if model.grounding is not None:
            model.grounding.float()
    emit({'state': 'WARMING_UP'})
    with contextlib.redirect_stdout(sys.stderr), torch.inference_mode(), tempfile.TemporaryDirectory() as directory:
        image = Path(directory)/'warmup.png'
        Image.new('RGB', (256, 256), (255, 0, 0)).save(image)
        qs = {'color': dict(type='choice', instructions='What is the image color?',
                            criteria={'red': 'Red', 'blue': 'Blue'})}
        model.predict(model_request({}, qs, image))
        torch.cuda.synchronize()
    emit(dict(state='READY', model=manifest['repo'], revision=manifest['revision'],
              manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
              precision=str(precision), device=torch.cuda.get_device_name(0)))
    for line in sys.stdin:
        try:
            request = json.loads(line)
            folder = Path(request['folder'])
            image = folder/'input.png' if request['has_image'] else None
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                response = model.predict(model_request(request['state'], request['questions'], image))
                torch.cuda.synchronize()
            emit({'answers': response['answers']})
        except Exception as exc:
            emit({'error': type(exc).__name__+': '+str(exc)})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({'state': 'FAILED', 'error': type(exc).__name__+': '+str(exc)})
        sys.exit(1)
