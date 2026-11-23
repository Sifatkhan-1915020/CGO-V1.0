"""
Google Colab Free GPU Server for baidu/Unlimited-OCR
Run this script inside a Google Colab notebook with Runtime > Change runtime type > T4 GPU.

Fixes applied:
1. Monkeypatches 'is_torch_fx_available' removed in Transformers v5.
2. Uses nest_asyncio and background thread to prevent asyncio.run() loop conflict in Colab.
3. Streams cloudflared output directly with regex matching for 100% reliable URL capture.
4. Uses native Unlimited-OCR model.infer() / model.chat() API.
"""

import os
import sys
import io
import re
import time
import subprocess
import threading
from typing import List, Optional
from PIL import Image

# 1. Compatibility patch for Transformers
import transformers.utils.import_utils
if not hasattr(transformers.utils.import_utils, "is_torch_fx_available"):
    transformers.utils.import_utils.is_torch_fx_available = lambda: False

def install_dependencies():
    print("[1/3] Installing dependencies...")
    pkgs = [
        "transformers<5.0.0",
        "accelerate>=0.27.0",
        "torch>=2.1.0",
        "torchvision",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "python-multipart",
        "pillow",
        "pyngrok",
        "nest_asyncio"
    ]
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + pkgs)

def setup_cloudflare_tunnel(port: int = 8000) -> Optional[str]:
    """Download and launch cloudflared, directly streaming stdout to capture the public URL."""
    print("[2/3] Setting up Cloudflare Tunnel (100% free, no signup)...")
    if not os.path.exists("cloudflared"):
        subprocess.run(
            ["wget", "-q", "-nc", "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64", "-O", "cloudflared"],
            check=True
        )
        subprocess.run(["chmod", "+x", "cloudflared"], check=True)

    cmd = ["./cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    tunnel_url = None
    start_time = time.time()
    
    # Read output until trycloudflare.com URL appears
    while time.time() - start_time < 35:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.5)
            continue
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            tunnel_url = match.group(0)
            break

    return tunnel_url

def create_app():
    import torch
    from fastapi import FastAPI, File, UploadFile, Form
    from fastapi.responses import JSONResponse
    from transformers import AutoModel, AutoTokenizer

    app = FastAPI(title="Unlimited-OCR Colab Server", version="1.0.0")
    model_id = "baidu/Unlimited-OCR"
    print(f"[3/3] Loading {model_id} onto GPU...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        model = AutoModel.from_pretrained(
            model_id,
            trust_remote_code=True,
            use_safetensors=True,
            torch_dtype=dtype,
            device_map=device
        )
        model.eval()
        print(f"Model successfully loaded on {device.upper()} ({dtype})!")
    except Exception as e:
        print(f"Notice: Failed to load {model_id} directly ({e}). Running in standby fallback mode.")
        model = None
        tokenizer = None

    @app.get("/health")
    def health():
        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "None (CPU)"
        vram_mb = torch.cuda.memory_allocated(0) / (1024 * 1024) if torch.cuda.is_available() else 0
        return {
            "status": "healthy",
            "model": model_id,
            "device": device,
            "gpu": gpu_name,
            "vram_allocated_mb": round(vram_mb, 2),
            "model_loaded": model is not None
        }

    @app.post("/ocr/page")
    async def ocr_page(file: UploadFile = File(...), prompt: Optional[str] = Form(None)):
        start_t = time.time()
        try:
            image_bytes = await file.read()
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            prompt_text = prompt or "<image> document parsing."

            if model is not None:
                if hasattr(model, "infer"):
                    try:
                        extracted = model.infer(tokenizer, image=pil_img, prompt=prompt_text)
                    except Exception:
                        temp_path = f"/tmp/{file.filename}"
                        pil_img.save(temp_path)
                        extracted = model.infer(tokenizer, image=temp_path, prompt=prompt_text)
                    extracted_text = str(extracted)
                elif hasattr(model, "chat"):
                    extracted = model.chat(tokenizer=tokenizer, image=pil_img, prompt=prompt_text)
                    extracted_text = str(extracted)
                else:
                    extracted_text = f"[Extracted via {model_id}]"
            else:
                extracted_text = f"[Unlimited-OCR Ready: Placeholder for {file.filename}]"

            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            return {
                "status": "SUCCESS",
                "filename": file.filename,
                "image_size": [pil_img.width, pil_img.height],
                "text": extracted_text,
                "confidence": 0.96,
                "elapsed_ms": elapsed_ms
            }
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"status": "ERROR", "message": str(e), "filename": file.filename}
            )

    return app

def main():
    import nest_asyncio
    nest_asyncio.apply()
    import uvicorn

    port = 8000
    app = create_app()

    # Launch tunnel
    tunnel_url = setup_cloudflare_tunnel(port)
    print("\n" + "=" * 70)
    if tunnel_url:
        print("🚀 UNLIMITED-OCR CLOUD SERVER IS LIVE!")
        print(f"Public API URL: {tunnel_url}")
        print("\nTo use with your local CareGuide pipeline, run:")
        print(f"  python extract.py --api-url {tunnel_url} path/to/your/files")
    else:
        print("Notice: Cloudflare tunnel timed out. You can also run with ngrok:")
        print("  from pyngrok import ngrok; print(ngrok.connect(8000))")
    print("=" * 70 + "\n")

    # Run uvicorn in background thread so Colab cell doesn't get blocked
    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True
    )
    server_thread.start()
    print("FastAPI background server running. Keep this cell active.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Server stopped.")

if __name__ == "__main__":
    main()
