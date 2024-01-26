FROM python:3.11-slim-bullseye

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

CMD ["pipenv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "80", "--no-access-log"]
