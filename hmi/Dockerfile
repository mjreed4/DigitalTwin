FROM python:3.12-slim

WORKDIR /app

# Install dependencies for Flask HMI
RUN pip install --no-cache-dir flask pymodbus requests

# Copy HMI code
COPY . .

EXPOSE 8000

CMD ["python", "app.py"]