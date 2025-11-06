FROM python:3.10-slim-buster

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --upgrade --no-cache-dir pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080
EXPOSE $PORT

CMD [ "bash", "-lc", "exec gunicorn --bind 0.0.0.0:${PORT} \
  --workers 2 --threads 8 --timeout 120 main:app" ]
