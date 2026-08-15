FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    texlive-full \
    python3-pygments \
    git \
    curl \
    default-libmysqlclient-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global user.name  "AureaTeX System" && \
    git config --global user.email "system@aureatex.local"

WORKDIR /app

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]