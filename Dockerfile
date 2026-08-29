# Migration Intelligence Connector — container image for Cloud Run / GKE.
# Builds the FastAPI connector that exposes Migration Validator's 24 tools to Gemini Enterprise.
# The Streamlit UI (webapp/app.py) can run from the same image — see docs/deployment/gcp-deployment.md.

FROM python:3.11-slim

# unixODBC + msodbcsql17 are required by pyodbc for the MSSQL source connector.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg unixodbc unixodbc-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects PORT; the connector reads it via CMD below.
ENV PORT=8001
EXPOSE 8001

CMD ["sh", "-c", "uvicorn src.gemini_connector.api:app --host 0.0.0.0 --port ${PORT}"]
