FROM node:20-alpine
WORKDIR /app

COPY package.json package-lock.json* .npmrc* ./
RUN npm install --silent --no-progress || npm install --silent --no-progress

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
