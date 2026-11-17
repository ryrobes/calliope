FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 65534:65534
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8094", "--workers", "1", "--no-access-log"]
