FROM python:3.11-slim

# Tor o'rnatish
RUN apt-get update && apt-get install -y tor && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Tor + bot ni birga ishga tushiruvchi script
CMD tor --RunAsDaemon 1 --SocksPort 9050 && sleep 3 && python bot.py
