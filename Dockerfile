# Runs on x86_64 and on IBM LinuxONE / IBM Z (s390x).
# numpy has no s390x wheel on PyPI, so it comes from Ubuntu's own packages.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-numpy \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip3 install --no-cache-dir --break-system-packages "fastapi>=0.110" "uvicorn>=0.29"

COPY guardian ./guardian
COPY model ./model
COPY web ./web

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "guardian.server:app", "--host", "0.0.0.0", "--port", "8000"]
