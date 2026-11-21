FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Europe/Istanbul
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ucuzgez ./ucuzgez
VOLUME /data
ENV UG_DB_PATH=/data/ucuzgez.db
EXPOSE 8000
CMD ["python", "-m", "ucuzgez"]
