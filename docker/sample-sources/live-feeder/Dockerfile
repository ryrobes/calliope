# Live mutating-source feeder: polls public feeds and keeps the MariaDB fixture
# churning. Stdlib HTTP; the only dependency is the MySQL driver.
ARG PYTHON_BASE_IMAGE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /feeder
COPY requirements.txt /feeder/requirements.txt
RUN pip install -r /feeder/requirements.txt
COPY feeder.py /feeder/feeder.py
USER nobody
CMD ["python", "/feeder/feeder.py"]
