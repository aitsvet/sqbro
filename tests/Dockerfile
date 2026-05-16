# Build context is the project root; reuses the project's requirements.txt.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY tests/requirements.txt /app/tests/requirements.txt
RUN pip install --no-cache-dir -r /app/tests/requirements.txt
# Project source is bind-mounted to /project at runtime; tests + seed live there.
CMD ["bash", "-c", "cd /project/tests && python seed.py /project /data/code.db && pytest -v"]
