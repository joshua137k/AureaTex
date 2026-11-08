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

# A app não corre como root: mesmo que alguém encontre uma falha, fica
# limitado a um utilizador sem privilégios dentro do container.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin aurea
ENV HOME=/home/aurea \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER aurea

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# IMPORTANTE: tem de ser 1 worker. A colaboração em tempo real e os locks de
# compilação vivem na memória do processo; com vários workers cada um teria a
# sua cópia dos documentos e sobrepunham-se uns aos outros.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
