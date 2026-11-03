# Build the interface with Node, serve it with nginx (ADR 0051).
FROM node:22-alpine AS build
RUN npm install --global pnpm@12.15.0
WORKDIR /web
# Dependencies first, so a code change does not reinstall them.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
