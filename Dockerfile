# Base image: Python 3.9 slim version
FROM python:3.9-slim

# Working directory set karo
WORKDIR /app

# requirements.txt copy karo aur dependencies install karo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki files (bot.py) copy karo
COPY . .

# Command jo bot ko run karega
CMD ["python", "bot.py"]