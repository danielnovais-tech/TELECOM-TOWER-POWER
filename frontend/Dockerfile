FROM node:22-alpine AS build

ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY . .
RUN npx vite build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Railway sets $PORT at runtime; default to 80 for local
ENV PORT=80
ENV BACKEND_URL=http://localhost:8000

EXPOSE ${PORT}

ENTRYPOINT ["/docker-entrypoint.sh"]
