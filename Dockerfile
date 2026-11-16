FROM python:3.12-slim
WORKDIR /app
COPY server.py .
COPY public/ ./public/
RUN mkdir -p /data/uploads
ENV PORT=8000 DATA_DIR=/data SECURE_COOKIES=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python","server.py"]
