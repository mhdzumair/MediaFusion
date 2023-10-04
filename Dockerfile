FROM mcr.microsoft.com/playwright/python:v1.38.0-jammy

# Install python 3.11
RUN apt-get update && apt-get install -y python3.11 python3-pip

RUN pip install pipenv

WORKDIR /mediafusion

# Install Python dependencies
COPY Pipfile Pipfile.lock ./

RUN pipenv install

# Copy the rest of the code
COPY . .

ARG PORT=8000
ENV PORT=$PORT


# Run the server
CMD ["pipenv", "run", "uvicorn", "--host", "0.0.0.0", "--port", "${PORT}", "app.main:app"]