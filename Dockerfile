FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM node:24-bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY requirements-agent-lock.txt ./
RUN python3 -m venv .venv-agent && .venv-agent/bin/python -m pip install --no-cache-dir -r requirements-agent-lock.txt
COPY --from=build /app/dist-app ./dist-app
COPY --from=build /app/build-server ./build-server
COPY python ./python
COPY integrations ./integrations
ENV HOST=0.0.0.0 PORT=3030 EASYFLOW_DATA_DIR=/app/data A4B_FOCUSED_DEMO=1
EXPOSE 3030
CMD ["node","build-server/index.mjs"]
