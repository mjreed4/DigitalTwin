FROM python:3.11-slim

WORKDIR /app

# Install pymodbus client dependency
RUN pip install --no-cache-dir pymodbus

COPY attack_setpoint.py .

# Ensure standard output streams directly to Azure Log Analytics
ENV PYTHONUNBUFFERED=1

CMD ["python", "attack_setpoint.py"]