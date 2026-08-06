# API de leitura do Squad 2. Sem GPU: extração e vetorização rodam em outro lugar.
FROM python:3.12-slim

# tini para o PID 1 repassar sinais — sem ele o uvicorn não recebe SIGTERM e o
# `docker compose down` espera o timeout inteiro toda vez
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements antes do código: mudar um .py não invalida a camada de instalação
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY front/ ./front/
COPY scripts/ ./scripts/

# usuário sem privilégio: se a API for comprometida, o atacante não é root
RUN useradd --create-home --uid 10001 sb100 && chown -R sb100:sb100 /app
USER sb100

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8).status==200 else 1)"

# Por variável e não por `--forwarded-allow-ips *`: o Click expande o `*` como
# glob no Windows, o que torna o comando impossível de testar fora do contêiner.
# Confiar em qualquer origem só é seguro porque a API não publica porta — quem
# alcança ela é o caddy, pela rede interna do compose.
ENV FORWARDED_ALLOW_IPS="*"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
