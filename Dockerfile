FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed, and keep layers clean
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project source code
COPY . .

# Install the package normally (not editable) for container stability
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

