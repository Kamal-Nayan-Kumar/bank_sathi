# Frontend (Vite static build).
#
# Vercel: New Project -> import this repo -> Root Directory: frontend
#   Build Command:      npm run build
#   Output Directory:   dist
#   Environment Variable: VITE_API_URL = https://<your-render-service>.onrender.com
#
# VITE_API_URL is a build-time variable: Vite inlines it into the bundle, so it
# must be set in Vercel's environment settings, not at runtime.

FROM node:22-alpine AS build
WORKDIR /app

# Lockfile-first so a dependency change busts the cache but a code change does not.
COPY package*.json ./
RUN npm ci

COPY . .
# VITE_API_URL is optional: without it the app calls its own origin, which is
# correct for local dev behind the Vite proxy.
RUN npm run build

FROM nginx:alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
