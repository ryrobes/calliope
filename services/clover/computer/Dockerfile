FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y python3 python3-pip libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY vendor ./vendor
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "1"]
