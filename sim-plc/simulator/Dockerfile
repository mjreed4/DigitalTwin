FROM python:3.12-slim

WORKDIR /app

# Install dependencies for process simulator
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy simulator code
COPY . .

EXPOSE 5020

CMD ["python", "pump_sim.py", "--no-console"]