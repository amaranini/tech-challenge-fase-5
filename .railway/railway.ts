// Infraestrutura na Railway como código (ADR 016 · docs/deploy.md).
//   railway config plan     # mostra o que mudaria (não altera nada)
//   railway config apply    # aplica
// Variáveis NÃO são gerenciadas aqui (segredos ficam na Railway): veja docs/deploy.md.
import { defineRailway, github, image, project, service, volume } from "railway/iac";

const REGIAO = "us-east4-eqdc4a";
const BRANCH = "main"; // cada push na main publica (gatilho do app GitHub da Railway)

export default defineRailway(() => {
  // checkSuites ("Wait for CI") desligado: a Railway espera TODOS os check suites do
  // commit, e o do app Cursor instalado no GitHub fica "queued" para sempre (deploy falha).
  const repo = github("amaranini/tech-challenge-fase-5", { branch: BRANCH, checkSuites: false });

  // Postgres 18 com pgvector (template oficial); a migration 0001 cria a extensão.
  const pgvectorVolume = volume("pgvector-volume", {
    alerts: { usage: { "100": {}, "80": {}, "95": {} } },
    allowOnlineResize: true,
    region: REGIAO,
    sizeMB: 5000,
  });
  const pgvector = service("pgvector", {
    source: image("pgvector/pgvector:pg18"),
    start: '/bin/sh -c "unset PGPORT; docker-entrypoint.sh postgres --port=5432"',
    replicas: { [REGIAO]: 1 },
    networking: { tcpProxies: { "5432": {} } }, // DATABASE_PUBLIC_URL (seed de demo do host)
    volumeMounts: { "/var/lib/postgresql": pgvectorVolume },
  });

  // API: 1 réplica — debounce e rate limit são in-process (ADR 006, ADR 015).
  const api = service("api", {
    source: repo,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "docker/api.Dockerfile",
      watchPatterns: [
        "src/**",
        "migrations/**",
        "alembic.ini",
        "pyproject.toml",
        "uv.lock",
        "docker/api.Dockerfile",
      ],
    },
    // Migrations e seed idempotente (catálogo + agenda mock) ANTES do novo deploy entrar.
    preDeploy: ["sh -c 'alembic upgrade head && python -m sdr.cli seed'"],
    start:
      "sh -c 'exec uvicorn sdr.main:app --host 0.0.0.0 --port ${PORT:-8000} " +
      "--proxy-headers --forwarded-allow-ips=*'",
    healthcheck: "/health",
    healthcheckTimeout: 120,
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5 },
    replicas: { [REGIAO]: 1 },
  });

  // Worker de follow-up: mesma imagem, só o laço (SKIP LOCKED: escala sem conflito).
  const worker = service("worker", {
    source: repo,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "docker/api.Dockerfile",
      watchPatterns: ["src/**", "pyproject.toml", "uv.lock", "docker/api.Dockerfile"],
    },
    start: "python -m sdr.worker",
    deploy: { restartPolicyType: "ALWAYS" },
    replicas: { [REGIAO]: 1 },
  });

  // Dashboard (Streamlit, só via API).
  const web = service("web", {
    source: repo,
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "docker/web.Dockerfile",
      watchPatterns: ["web/**", ".streamlit/**", "pyproject.toml", "uv.lock", "docker/web.Dockerfile"],
    },
    healthcheck: "/_stcore/health",
    healthcheckTimeout: 120,
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5 },
    replicas: { [REGIAO]: 1 },
  });

  return project("sdr-lia", {
    variables: { managed: false },
    resources: [pgvector, api, worker, web, pgvectorVolume],
  });
});
