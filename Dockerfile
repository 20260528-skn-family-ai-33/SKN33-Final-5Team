FROM node:24-bookworm-slim AS build
WORKDIR /app/Frontend
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci
COPY Frontend/ ./
RUN npm run build
FROM node:24-bookworm-slim
WORKDIR /app/Frontend
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/*
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci --omit=dev
COPY Backend/requirements-agent-lock.txt /app/Backend/requirements-agent-lock.txt
RUN python3 -m venv /app/Backend/.venv-agent && /app/Backend/.venv-agent/bin/python -m pip install --no-cache-dir -r /app/Backend/requirements-agent-lock.txt
COPY --from=build /app/Frontend/dist-app ./dist-app
COPY --from=build /app/Frontend/build-server ./build-server
COPY Backend/agent_builder/ /app/Backend/agent_builder/
COPY Backend/scripts/ /app/Backend/scripts/
COPY Backend/integrations/ /app/Backend/integrations/
COPY docs/examples/ /app/docs/examples/
ENV HOST=0.0.0.0 PORT=3030 EASYFLOW_DATA_DIR=/app/data A4B_FOCUSED_DEMO=1
EXPOSE 3030
CMD ["node","build-server/index.mjs"]
