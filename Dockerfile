FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY fetch_reddit.py .
COPY tests/ tests/

ENTRYPOINT ["python", "fetch_reddit.py"]
