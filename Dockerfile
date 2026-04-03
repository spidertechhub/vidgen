FROM python:3.10-slim

# Install system-level FFmpeg (Crucial for yt-dlp and MoviePy)
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bind to Render's dynamic PORT
CMD ["sh", "-c", "python app.py"]
