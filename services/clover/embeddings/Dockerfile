FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV HF_HOME=/models TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2
RUN mkdir /models && chown 65534:65534 /models
USER 65534:65534
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8085", "--workers", "1", "--no-access-log"]
