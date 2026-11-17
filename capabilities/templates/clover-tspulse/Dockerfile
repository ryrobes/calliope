FROM python:3.12-slim
WORKDIR /app
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
    HF_HOME=/models/huggingface HF_HUB_DISABLE_XET=1 PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION_FOR_GRANITE_TSFM=0.3.2
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# v0.3.2's wheel omits models/toolkit subpackages. Overlay the checksummed
# source from that same release (the existing Zoo uses this workaround too).
COPY install_source.py ./
RUN python install_source.py
COPY main.py ./
RUN mkdir -p /models/huggingface && chown -R 65534:65534 /models
USER 65534:65534
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
