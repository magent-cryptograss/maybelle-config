# Inquiry Notifier

Simple notification service for Justin Holmes EPK inquiries and mailing list signups.

## Features

- Receives booking inquiries from EPK contact form
- Receives mailing list signups
- Sends notifications to Telegram
- CORS-enabled for cross-origin requests

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `PORT` | Server port (default: 3001) | No |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | Yes |
| `TELEGRAM_CHAT_ID` | Chat/channel ID to send notifications to | Yes |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins | No |

## Setup Telegram Bot

1. Message @BotFather on Telegram
2. Send `/newbot` and follow prompts
3. Copy the bot token
4. Create a channel or group, add the bot as admin
5. Get chat ID:
   - For channels: forward a message to @userinfobot
   - For groups: add @userinfobot to the group, it will show the chat ID

## Endpoints

### `POST /inquiry`
Booking inquiry form submission.

```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "message": "I'd like to book Justin for our event..."
}
```

### `POST /subscribe`
Mailing list signup.

```json
{
  "email": "john@example.com"
}
```

### `GET /health`
Health check endpoint.

## Local Development

```bash
npm install
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=xxx npm start
```

## Docker

```bash
docker build -t inquiry-notifier .
docker run -p 3001:3001 \
  -e TELEGRAM_BOT_TOKEN=xxx \
  -e TELEGRAM_CHAT_ID=xxx \
  -e ALLOWED_ORIGINS=https://justinholmes.com,http://localhost:5173 \
  inquiry-notifier
```

## Adding to Maybelle

Add to docker-compose.maybelle.yml:

```yaml
inquiry-notifier:
  build:
    context: ../../inquiry-notifier
    dockerfile: Dockerfile
  container_name: inquiry-notifier
  environment:
    - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
    - ALLOWED_ORIGINS=https://justinholmes.com,https://cryptograss.live
  ports:
    - "127.0.0.1:3001:3001"
  networks:
    - memory-lane-net
  restart: unless-stopped
```

Add Caddy route for `api.cryptograss.live` or similar.
