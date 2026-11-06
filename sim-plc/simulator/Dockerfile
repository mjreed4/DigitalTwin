FROM python:3.12-slim

WORKDIR /app

# Install dependencies for process simulator
RUN pip install --no-cache-dir pymodbus>=3.12.1

# Copy simulator code
COPY . .

EXPOSE 5020

CMD ["python", "pump_sim.py"]