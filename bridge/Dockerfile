FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir azure-digitaltwins-core azure-identity pymodbus

COPY adt_bridge.py .

ENV PLC_HOST="pump-station-openplc" \
    PLC_PORT="502" \
    POLL_INTERVAL="2.0"

CMD ["python", "adt_bridge.py"]