# charts/app

Helm chart that deploys the two stateless application components to Kubernetes:

| Component | Description |
|-----------|-------------|
| **frontend** | Vite/React dev server (`frontend/Dockerfile`) — exposed via `Service` and optionally an `Ingress` |
| **temporal-worker** | Python Temporal worker (`temporal/Dockerfile`) — headless; no `Service` or `Ingress` |

---

## Prerequisites

- Helm 3.x
- Kubernetes 1.24+
- Two Kubernetes `Secret` objects in the target namespace (see [Required Secrets](#required-secrets))

---

## Required Secrets

Before installing, create the secrets that the pods reference via `secretKeyRef`:

```bash
# Secret for the frontend pod
kubectl create secret generic frontend-secrets \
  --from-literal=VITE_SUPABASE_ANON_KEY=<your-anon-key>

# Secret for the temporal-worker pod
kubectl create secret generic temporal-worker-secrets \
  --from-literal=SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
```

The secret names and keys are configurable via `values.yaml`
(`frontend.secrets.*` and `temporalWorker.secrets.*`).

---

## Installing the Chart

```bash
# Render manifests to stdout (dry-run)
helm template my-release charts/app

# Render using environment profiles
helm template my-release charts/app -f charts/app/values-dev.yaml
helm template my-release charts/app -f charts/app/values-test.yaml
helm template my-release charts/app -f charts/app/values-prod.yaml

# Install into the current namespace
helm install my-release charts/app

# Install with custom image tags
helm install my-release charts/app \
  --set frontend.image.repository=ghcr.io/your-org/frontend \
  --set frontend.image.tag=1.2.3 \
  --set temporalWorker.image.repository=ghcr.io/your-org/temporal-worker \
  --set temporalWorker.image.tag=1.2.3

# Install with image digests (ADR-0010 digest-pinning — preferred for test/prod)
# When image.digest is set, the image is referenced as repo@sha256:… and the tag
# is used for audit/display only. Use pullPolicy: IfNotPresent with digests.
helm install my-release charts/app \
  --set frontend.image.repository=ghcr.io/your-org/frontend \
  --set frontend.image.digest=sha256:abc123... \
  --set frontend.image.pullPolicy=IfNotPresent \
  --set temporalWorker.image.repository=ghcr.io/your-org/temporal-worker \
  --set temporalWorker.image.digest=sha256:def456... \
  --set temporalWorker.image.pullPolicy=IfNotPresent

# Enable the frontend Ingress
helm install my-release charts/app \
  --set frontend.ingress.enabled=true \
  --set frontend.ingress.className=nginx \
  --set frontend.ingress.hosts[0].host=app.example.com \
  --set frontend.ingress.hosts[0].paths[0].path=/ \
  --set frontend.ingress.hosts[0].paths[0].pathType=Prefix
```

---

## Environment Profiles

The chart includes static values profiles for the proposed namespaces:

- `charts/app/values-dev.yaml` (`<DEV_NAMESPACE>`)
- `charts/app/values-test.yaml` (`<TEST_NAMESPACE>`)
- `charts/app/values-prod.yaml` (`<PROD_NAMESPACE>`)

Use them with explicit namespace selection:

```bash
helm upgrade --install app-dev charts/app -n <DEV_NAMESPACE> -f charts/app/values-dev.yaml
helm upgrade --install app-test charts/app -n <TEST_NAMESPACE> -f charts/app/values-test.yaml
helm upgrade --install app-prod charts/app -n <PROD_NAMESPACE> -f charts/app/values-prod.yaml
```

---

## Values Reference

### Global

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `nameOverride` | string | `""` | Override chart name |
| `fullnameOverride` | string | `""` | Override full release name |
| `imageRegistry` | string | `""` | Global image registry prefix (e.g. `ghcr.io`) |

### Frontend

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `frontend.replicaCount` | int | `1` | Number of pod replicas |
| `frontend.image.registry` | string | `""` | Registry (overrides `imageRegistry`) |
| `frontend.image.repository` | string | `"your-org/frontend"` | Image repository |
| `frontend.image.tag` | string | `"latest"` | Image tag |
| `frontend.image.pullPolicy` | string | `"IfNotPresent"` | Image pull policy |
| `frontend.imagePullSecrets` | list | `[]` | Pull-secret names |
| `frontend.podSecurityContext` | object | `runAsNonRoot`, uid/gid `101`, `seccompProfile: RuntimeDefault` | Pod security context |
| `frontend.securityContext` | object | `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]` | Container security context |
| `frontend.service.type` | string | `"ClusterIP"` | Service type |
| `frontend.service.port` | int | `3000` | Service port |
| `frontend.ingress.enabled` | bool | `false` | Enable Ingress |
| `frontend.ingress.className` | string | `""` | Ingress class |
| `frontend.ingress.annotations` | object | `{}` | Ingress annotations |
| `frontend.ingress.hosts` | list | see values.yaml | Ingress host rules |
| `frontend.ingress.tls` | list | `[]` | Ingress TLS config |
| `frontend.resources` | object | 100m/128Mi req, 500m/512Mi lim | Pod resource requests/limits |
| `frontend.livenessProbe` | object | HTTP GET `/` :3000 | Liveness probe config |
| `frontend.readinessProbe` | object | HTTP GET `/` :3000 | Readiness probe config |
| `frontend.env.supabaseUrl` | string | `"http://supabase:8000"` | `VITE_SUPABASE_URL` value |
| `frontend.env.apiUrl` | string | `"http://supabase:8000/functions/v1"` | `VITE_API_URL` value |
| `frontend.secrets.supabaseAnonKey.secretName` | string | `"frontend-secrets"` | Secret containing anon key |
| `frontend.secrets.supabaseAnonKey.key` | string | `"VITE_SUPABASE_ANON_KEY"` | Key within the Secret |

### Temporal Worker

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `temporalWorker.replicaCount` | int | `1` | Number of pod replicas |
| `temporalWorker.image.registry` | string | `""` | Registry (overrides `imageRegistry`) |
| `temporalWorker.image.repository` | string | `"your-org/temporal-worker"` | Image repository |
| `temporalWorker.image.tag` | string | `"latest"` | Image tag |
| `temporalWorker.image.pullPolicy` | string | `"IfNotPresent"` | Image pull policy |
| `temporalWorker.imagePullSecrets` | list | `[]` | Pull-secret names |
| `temporalWorker.podSecurityContext` | object | `runAsNonRoot`, uid/gid `10001`, `seccompProfile: RuntimeDefault` | Pod security context |
| `temporalWorker.securityContext` | object | `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]` | Container security context |
| `temporalWorker.resources` | object | 100m/128Mi req, 500m/512Mi lim | Pod resource requests/limits |
| `temporalWorker.livenessProbe` | object | exec `python -c "import os; os.kill(1, 0)"` | Liveness probe config |
| `temporalWorker.readinessProbe` | object | exec `python -c "import os; os.kill(1, 0)"` | Readiness probe config |
| `temporalWorker.temporal.address` | string | `"temporal:7233"` | Temporal server address |
| `temporalWorker.temporal.namespace` | string | `"default"` | Temporal namespace |
| `temporalWorker.temporal.taskQueue` | string | `"main"` | Temporal task queue |
| `temporalWorker.supabase.url` | string | `"http://supabase:8000"` | `SUPABASE_URL` value |
| `temporalWorker.secrets.supabaseServiceRoleKey.secretName` | string | `"temporal-worker-secrets"` | Secret containing service-role key |
| `temporalWorker.secrets.supabaseServiceRoleKey.key` | string | `"SUPABASE_SERVICE_ROLE_KEY"` | Key within the Secret |

### Operations API

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `opsApi.replicaCount` | int | `1` | Number of pod replicas |
| `opsApi.image.registry` | string | `""` | Registry (overrides `imageRegistry`) |
| `opsApi.image.repository` | string | `"your-org/temporal-worker"` | Image repository (same image as worker) |
| `opsApi.image.tag` | string | `"latest"` | Image tag |
| `opsApi.image.pullPolicy` | string | `"Always"` | Image pull policy |
| `opsApi.imagePullSecrets` | list | `[]` | Pull-secret names |
| `opsApi.podSecurityContext` | object | `runAsNonRoot`, uid/gid `10001`, `seccompProfile: RuntimeDefault` | Pod security context |
| `opsApi.securityContext` | object | `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]` | Container security context |
| `opsApi.service.type` | string | `"ClusterIP"` | Service type |
| `opsApi.service.port` | int | `8000` | Service port |
| `opsApi.resources` | object | 100m/128Mi req, 500m/512Mi lim | Pod resource requests/limits |
| `opsApi.livenessProbe` | object | HTTP GET `/api/ops/health` :8000 | Liveness probe config |
| `opsApi.readinessProbe` | object | HTTP GET `/api/ops/health` :8000 | Readiness probe config |
| `opsApi.temporal.address` | string | `"temporal:7233"` | Temporal server address |
| `opsApi.temporal.namespace` | string | `"default"` | Temporal namespace |
| `opsApi.supabase.url` | string | `"http://supabase:8000"` | `SUPABASE_URL` value |
| `opsApi.secrets.supabaseServiceRoleKey.secretName` | string | `"temporal-worker-secrets"` | Secret containing service-role key |
| `opsApi.secrets.supabaseServiceRoleKey.key` | string | `"SUPABASE_SERVICE_ROLE_KEY"` | Key within the Secret |

---

## Validation

```bash
# Lint the chart
helm lint charts/app

# Render all manifests with default values
helm template my-release charts/app

# Render with ingress enabled
helm template my-release charts/app --set frontend.ingress.enabled=true
```
