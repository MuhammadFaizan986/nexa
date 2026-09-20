# Development image for the Next.js UI (a production build comes in Week 8).
FROM node:24-alpine

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]
