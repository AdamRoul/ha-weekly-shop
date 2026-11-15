ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM
RUN apk add --no-cache python3 py3-flask py3-pillow py3-requests font-dejavu
COPY app /app
WORKDIR /app
CMD ["python3", "-u", "/app/main.py"]
