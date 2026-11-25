FROM python:3.11-slim

WORKDIR /app

# Install pymodbus and flask dependencies
RUN pip install --no-cache-dir pymodbus flask

COPY attack.py .

# Ensure standard output streams directly to Azure Log Analytics
ENV PYTHONUNBUFFERED=1

# Expose port 5000 for Flask application
EXPOSE 5000

CMD ["python", "attack.py"]