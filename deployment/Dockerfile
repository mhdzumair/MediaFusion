FROM python:3.11-slim-bullseye

ARG GIT_REV="dev"
ENV GIT_REV=$GIT_REV

WORKDIR /mediafusion

# Install dependencies
RUN pip install --upgrade pip && \
    pip install pipenv && \
    apt-get update && \
    apt-get install -y git

COPY ../Pipfile Pipfile.lock ./

RUN pipenv install --deploy --ignore-pipfile

# Copy the source code
COPY .. .

# Expose the port
EXPOSE 80

CMD ["pipenv", "run", "gunicorn", "api.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:80", "--timeout", "120"]
