FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY . .

EXPOSE 3000

ENV HOST=0.0.0.0

ENTRYPOINT ["docker-entrypoint.sh"]

