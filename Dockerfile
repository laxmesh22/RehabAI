# Railway / container image for RehabAI FastAPI studio (consumer + hospital web).
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt

COPY backend backend
COPY agent agent
COPY edge edge
COPY web web
COPY dist dist

ENV PYTHONUNBUFFERED=1 \
    REHABAI_SOURCE=simulation \
    REHABAI_TTS=sarvam \
    REHABAI_STT=auto

EXPOSE 8000

# Railway injects PORT.
CMD ["sh", "-c", "python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
