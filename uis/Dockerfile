FROM node:22-alpine

RUN apk add --no-cache bash

WORKDIR /app/website
COPY website/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY website/ ./
RUN npm run build

WORKDIR /app/backoffice
COPY backoffice/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY backoffice/ ./
RUN npm run build

WORKDIR /app
COPY start.sh ./start.sh
RUN chmod +x ./start.sh

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000 3001

CMD ["/app/start.sh"]