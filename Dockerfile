FROM python:3.13-slim

RUN apt update
RUN apt install -y ffmpeg cron
RUN rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt
RUN echo "0 3 * * 1 pip install -U yt-dlp spotdl >> /var/log/cron.log 2>&1" > /etc/cron.d/pip-update
RUN chmod 0644 /etc/cron.d/pip-update
RUN crontab /etc/cron.d/pip-update
RUN touch /var/log/cron.log

CMD cron && tail -f /var/log/cron.log & python -u bot.py