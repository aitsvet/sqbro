FROM python:3.12-slim

WORKDIR /app/backend
COPY requirements.txt main.py index.html ./

ENV PATH="/opt/venv/bin:$PATH"
RUN python3 -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt

EXPOSE 59620
CMD ["python", "main.py", "/data"]