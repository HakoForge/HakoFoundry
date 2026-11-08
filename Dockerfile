# For more information, please refer to https://aka.ms/vscode-docker-python
FROM python:3.11-alpine
RUN apk add smartmontools
# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

WORKDIR /app
COPY . /app

# Version shown in the UI, /api/v1/status and /metrics; CI passes the image tag and commit.
ARG APP_VERSION=dev
ARG APP_REVISION=unknown
ENV APP_VERSION=$APP_VERSION APP_REVISION=$APP_REVISION

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /app

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug

CMD ["python3", "main.py"]
