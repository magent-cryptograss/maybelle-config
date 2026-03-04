FROM node:20-slim

# Install yt-dlp and ffmpeg (needed for some video processing)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

# Build args for version tracking
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_COMMIT=$GIT_COMMIT
ENV BUILD_TIME=$BUILD_TIME

# Install dependencies
COPY package.json ./
RUN npm install --production

# Copy application (all JS files)
COPY *.js ./

# Create staging directory
RUN mkdir -p /staging

EXPOSE 3001

CMD ["node", "server.js"]
