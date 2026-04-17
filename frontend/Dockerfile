FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY . .
RUN npx vite build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf.template

# Railway sets $PORT at runtime; default to 80 for local
ENV PORT=80
ENV BACKEND_URL=http://localhost:8000

EXPOSE ${PORT}

# At startup: substitute env vars into nginx config, then launch nginx
CMD /bin/sh -c "envsubst '\$PORT \$BACKEND_URL' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"
