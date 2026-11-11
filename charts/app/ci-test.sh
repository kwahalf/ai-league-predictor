#!/usr/bin/env bash
# charts/app/ci-test.sh
#
# Validate the app Helm chart and its environment-specific values profiles.
# Runs helm lint + helm template checks for base, dev, test, and prod profiles.
#
# Usage (from repo root):
#   bash charts/app/ci-test.sh
#
# Requirements: helm 3.x on PATH.

set -euo pipefail

CHART="charts/app"
RELEASE="ci-test"
PASS=0
FAIL=0

pass() { printf "  ✅ %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "  ❌ FAIL: %s\n" "$1"; FAIL=$((FAIL + 1)); }

# assert_contains <rendered_manifest> <label> <extended_regexp>
assert_contains() {
  local manifest="$1" label="$2" pattern="$3"
  if grep -qE "$pattern" <<<"$manifest"; then
    pass "$label"
  else
    fail "$label — expected pattern not found: $pattern"
  fi
}

# assert_not_contains <rendered_manifest> <label> <extended_regexp>
assert_not_contains() {
  local manifest="$1" label="$2" pattern="$3"
  if grep -qE "$pattern" <<<"$manifest"; then
    fail "$label — unexpected pattern found: $pattern"
  else
    pass "$label"
  fi
}

# ── helm lint ─────────────────────────────────────────────────────────────────
echo "=== helm lint ==="

lint_check() {
  local label="$1"; shift
  if helm lint "$@" >/dev/null 2>&1; then
    pass "lint: $label"
  else
    fail "lint: $label"
    helm lint "$@" || true
  fi
}

lint_check "base chart"                  "$CHART"
lint_check "values-dev.yaml"  "$CHART" -f "$CHART/values-dev.yaml"
lint_check "values-test.yaml" "$CHART" -f "$CHART/values-test.yaml"
lint_check "values-prod.yaml" "$CHART" -f "$CHART/values-prod.yaml"

# ── base chart (default values) ───────────────────────────────────────────────
echo ""
echo "=== base chart (default values) ==="
BASE=$(helm template "$RELEASE" "$CHART")

assert_contains     "$BASE" "base: frontend Deployment present"         "kind: Deployment"
assert_contains     "$BASE" "base: Service present"                     "kind: Service"
assert_contains     "$BASE" "base: ops-api Deployment present"          "name: ${RELEASE}-app-ops-api"
assert_contains     "$BASE" "base: ops-api Service present"             "name: ${RELEASE}-app-ops-api"
assert_contains     "$BASE" "base: secretKeyRef used for frontend key"  "secretKeyRef"
# No Ingress by default
assert_not_contains "$BASE" "base: no Ingress rendered by default"      "kind: Ingress"
# Sensitive env vars must not appear as plain value: fields
assert_not_contains "$BASE" "base: VITE_SUPABASE_ANON_KEY not literal"      "value:.*VITE_SUPABASE_ANON_KEY"
assert_not_contains "$BASE" "base: SUPABASE_SERVICE_ROLE_KEY not literal"    "value:.*SUPABASE_SERVICE_ROLE_KEY"
# Default tag is "latest" (mutable) → pullPolicy must NOT be IfNotPresent (ADR-0010)
assert_not_contains "$BASE" "base: pullPolicy not IfNotPresent with mutable tag" "imagePullPolicy: IfNotPresent"
assert_contains     "$BASE" "base: pod runAsNonRoot enabled"             "runAsNonRoot: true"
assert_contains     "$BASE" "base: frontend runAsUser non-root"          "runAsUser: 101"
assert_contains     "$BASE" "base: worker runAsUser non-root"            "runAsUser: 10001"
assert_contains     "$BASE" "base: seccomp runtime default"              "type: RuntimeDefault"
assert_contains     "$BASE" "base: priv-esc disabled"                    "allowPrivilegeEscalation: false"
assert_contains     "$BASE" "base: root fs readonly"                     "readOnlyRootFilesystem: true"
assert_contains     "$BASE" "base: all capabilities dropped"             "drop:"
assert_contains     "$BASE" "base: frontend nginx cache writable mount"  "mountPath: /var/cache/nginx"
assert_contains     "$BASE" "base: frontend run dir writable mount"      "mountPath: /var/run"
assert_contains     "$BASE" "base: worker tmp writable mount"            "name: temporal-worker-tmp"
assert_contains     "$BASE" "base: ops-api tmp writable mount"           "name: ops-api-tmp"
assert_not_contains "$BASE" "base: ops-api SUPABASE_SERVICE_ROLE_KEY not literal" "value:.*SUPABASE_SERVICE_ROLE_KEY"

# ops-api-scoped hardening assertions — these fail if ops-api loses its security contexts
OPS_API_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: ops-api/' <<<"$BASE")
assert_contains "$OPS_API_DEPLOY" "base: ops-api podSecurityContext runAsNonRoot"    "runAsNonRoot: true"
assert_contains "$OPS_API_DEPLOY" "base: ops-api podSecurityContext runAsUser=10001" "runAsUser: 10001"
assert_contains "$OPS_API_DEPLOY" "base: ops-api seccomp RuntimeDefault"             "type: RuntimeDefault"
assert_contains "$OPS_API_DEPLOY" "base: ops-api allowPrivilegeEscalation=false"     "allowPrivilegeEscalation: false"
assert_contains "$OPS_API_DEPLOY" "base: ops-api readOnlyRootFilesystem"             "readOnlyRootFilesystem: true"
assert_contains "$OPS_API_DEPLOY" "base: ops-api capabilities.drop ALL"              "drop:"

# frontend-scoped hardening assertions — guardrail: fail if frontend loses its security contexts
FRONTEND_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: frontend/' <<<"$BASE")
assert_contains "$FRONTEND_DEPLOY" "base: frontend podSecurityContext runAsNonRoot"    "runAsNonRoot: true"
assert_contains "$FRONTEND_DEPLOY" "base: frontend podSecurityContext runAsUser=101"   "runAsUser: 101"
assert_contains "$FRONTEND_DEPLOY" "base: frontend podSecurityContext runAsGroup=101"  "runAsGroup: 101"
assert_contains "$FRONTEND_DEPLOY" "base: frontend seccomp RuntimeDefault"             "type: RuntimeDefault"
assert_contains "$FRONTEND_DEPLOY" "base: frontend allowPrivilegeEscalation=false"     "allowPrivilegeEscalation: false"
assert_contains "$FRONTEND_DEPLOY" "base: frontend readOnlyRootFilesystem"             "readOnlyRootFilesystem: true"
assert_contains "$FRONTEND_DEPLOY" "base: frontend capabilities.drop ALL"              "drop:"
# frontend writable-path mounts — entrypoint writes to /tmp; nginx needs /var/cache/nginx and /var/run
assert_contains "$FRONTEND_DEPLOY" "base: frontend /tmp writable emptyDir mount"       "mountPath: /tmp"
assert_contains "$FRONTEND_DEPLOY" "base: frontend /var/cache/nginx writable mount"    "mountPath: /var/cache/nginx"
assert_contains "$FRONTEND_DEPLOY" "base: frontend /var/run writable mount"            "mountPath: /var/run"

# temporal-worker-scoped hardening assertions — guardrail: fail if worker loses its security contexts
WORKER_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: temporal-worker/' <<<"$BASE")
assert_contains "$WORKER_DEPLOY" "base: temporal-worker podSecurityContext runAsNonRoot"     "runAsNonRoot: true"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker podSecurityContext runAsUser=10001"  "runAsUser: 10001"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker podSecurityContext runAsGroup=10001" "runAsGroup: 10001"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker seccomp RuntimeDefault"              "type: RuntimeDefault"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker allowPrivilegeEscalation=false"      "allowPrivilegeEscalation: false"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker readOnlyRootFilesystem"              "readOnlyRootFilesystem: true"
assert_contains "$WORKER_DEPLOY" "base: temporal-worker capabilities.drop ALL"               "drop:"
# temporal-worker writable-path mount — worker needs /tmp for transient files
assert_contains "$WORKER_DEPLOY" "base: temporal-worker /tmp writable emptyDir mount"        "mountPath: /tmp"

# ── digest rendering (inline render with --set) ────────────────────────────────
echo ""
echo "=== digest rendering ==="
DIGEST_SHA="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
DIGEST_RENDER=$(helm template "$RELEASE" "$CHART" \
  --set "imageRegistry=example.azurecr.io" \
  --set "frontend.image.repository=frontend" \
  --set "frontend.image.digest=${DIGEST_SHA}" \
  --set "temporalWorker.image.repository=temporal-worker" \
  --set "temporalWorker.image.digest=${DIGEST_SHA}" \
  --set "opsApi.image.repository=temporal-worker" \
  --set "opsApi.image.digest=${DIGEST_SHA}")

assert_contains     "$DIGEST_RENDER" "digest: frontend image uses @sha256: form"         "image: example.azurecr.io/frontend@sha256:"
assert_contains     "$DIGEST_RENDER" "digest: worker image uses @sha256: form"           "image: example.azurecr.io/temporal-worker@sha256:"
assert_contains     "$DIGEST_RENDER" "digest: ops-api image uses @sha256: form"          "image: example.azurecr.io/temporal-worker@sha256:"
assert_not_contains "$DIGEST_RENDER" "digest: no :tag suffix when digest is set"         "image: example.azurecr.io/frontend:latest"

# ── dev profile ───────────────────────────────────────────────────────────────
echo ""
echo "=== values-dev.yaml ==="
DEV=$(helm template "$RELEASE" "$CHART" -f "$CHART/values-dev.yaml")
DEV_VALUES=$(cat "$CHART/values-dev.yaml")

assert_contains     "$DEV" "dev: frontend Deployment renders"           "kind: Deployment"
assert_contains     "$DEV" "dev: frontend replicas=1"                   "replicas: 1"
assert_not_contains "$DEV" "dev: no Ingress (ingress.enabled=false)"    "kind: Ingress"
# Scope to the frontend Service document in the multi-doc helm template output.
DEV_FRONTEND_SERVICE=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Service/ && /app.kubernetes.io\/component: frontend/' <<<"$DEV")
if [ -z "$DEV_FRONTEND_SERVICE" ]; then
  fail "dev: frontend Service manifest extracted"
else
  pass "dev: frontend Service manifest extracted"
fi
assert_contains     "$DEV_FRONTEND_SERVICE" "dev: frontend service type=LoadBalancer" "type: LoadBalancer"
assert_contains     "$DEV" "dev: frontend image tag=dev-latest"         "image: (.*/)?frontend:dev-latest"
assert_contains     "$DEV" "dev: worker image tag=dev-latest"           "image: (.*/)?temporal-worker:dev-latest"
assert_contains     "$DEV" "dev: ops-api image tag=dev-latest"          "image: (.*/)?temporal-worker:dev-latest"
assert_contains     "$DEV" "dev: temporal namespace=<DEV_NAMESPACE>"          "<DEV_NAMESPACE>"
assert_contains     "$DEV" "dev: temporal taskQueue=<DEV_NAMESPACE>-main"     "<DEV_NAMESPACE>-main"
assert_contains     "$DEV" "dev: secretKeyRef present"                  "secretKeyRef"
assert_contains     "$DEV" "dev: frontend secret=frontend-secrets-<DEV_NAMESPACE>"       "frontend-secrets-<DEV_NAMESPACE>"
assert_contains     "$DEV" "dev: worker secret=temporal-worker-secrets-<DEV_NAMESPACE>"  "temporal-worker-secrets-<DEV_NAMESPACE>"
assert_contains     "$DEV" "dev: ops-api health endpoint configured"    "/api/ops/health"
assert_contains     "$DEV_VALUES" "dev values: frontend Supabase URL uses HTTPS"    "supabaseUrl: \"https://"
assert_contains     "$DEV_VALUES" "dev values: frontend API URL uses HTTPS"         "apiUrl: \"https://"
assert_not_contains "$DEV" "dev: VITE_SUPABASE_ANON_KEY not literal"   "value:.*VITE_SUPABASE_ANON_KEY"
assert_not_contains "$DEV" "dev: SUPABASE_SERVICE_ROLE_KEY not literal" "value:.*SUPABASE_SERVICE_ROLE_KEY"

# dev profile: scoped hardening guardrails for frontend and temporal-worker
DEV_FRONTEND_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: frontend/' <<<"$DEV")
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend runAsNonRoot"              "runAsNonRoot: true"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend runAsUser=101"             "runAsUser: 101"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend seccomp RuntimeDefault"    "type: RuntimeDefault"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend allowPrivilegeEscalation"  "allowPrivilegeEscalation: false"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend readOnlyRootFilesystem"    "readOnlyRootFilesystem: true"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend /tmp writable mount"       "mountPath: /tmp"
DEV_WORKER_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: temporal-worker/' <<<"$DEV")
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker runAsNonRoot"             "runAsNonRoot: true"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker runAsUser=10001"          "runAsUser: 10001"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker seccomp RuntimeDefault"   "type: RuntimeDefault"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker allowPrivilegeEscalation" "allowPrivilegeEscalation: false"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker readOnlyRootFilesystem"   "readOnlyRootFilesystem: true"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker /tmp writable mount"      "mountPath: /tmp"

# dev profile: live-env deploy wiring — acr-pull imagePullSecret, in-cluster Temporal, resource sizing
# These assertions guard the settings that keep the live dev environment working after PR #106/#407.
DEV_OPS_API_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: ops-api/' <<<"$DEV")
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend imagePullSecrets=acr-pull"    "name: acr-pull"
assert_contains "$DEV_WORKER_DEPLOY"   "dev: temporal-worker imagePullSecrets=acr-pull" "name: acr-pull"
assert_contains "$DEV_OPS_API_DEPLOY"  "dev: ops-api imagePullSecrets=acr-pull"     "name: acr-pull"
assert_contains "$DEV_WORKER_DEPLOY" "dev: temporal-worker temporal address=in-cluster svc" \
  "temporal-frontend\\.dev\\.svc\\.cluster\\.local:7233"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend memory request=512Mi"         "memory: 512Mi"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend memory limit=1Gi"             "memory: 1Gi"
assert_contains "$DEV_FRONTEND_DEPLOY" "dev: frontend cpu request=100m"             "cpu: 100m"

# ── test profile ──────────────────────────────────────────────────────────────
echo ""
echo "=== values-test.yaml ==="
TEST=$(helm template "$RELEASE" "$CHART" -f "$CHART/values-test.yaml")

assert_contains     "$TEST" "test: frontend Deployment renders"          "kind: Deployment"
assert_contains     "$TEST" "test: frontend replicas=2"                  "replicas: 2"
assert_contains     "$TEST" "test: Ingress enabled"                      "kind: Ingress"
assert_contains     "$TEST" "test: ingress host=frontend.<TEST_DOMAIN>"  "frontend\\.<TEST_DOMAIN>"
assert_contains     "$TEST" "test: ingress className=nginx"              "ingressClassName: nginx"
assert_contains     "$TEST" "test: frontend image tag prefix=test-"      "/frontend:test-"
assert_contains     "$TEST" "test: worker image tag prefix=test-"        "/temporal-worker:test-"
assert_contains     "$TEST" "test: ops-api image tag prefix=test-"       "/temporal-worker:test-"
assert_contains     "$TEST" "test: temporal namespace=<TEST_NAMESPACE>"        "<TEST_NAMESPACE>"
assert_contains     "$TEST" "test: temporal taskQueue=<TEST_NAMESPACE>-main"   "<TEST_NAMESPACE>-main"
assert_contains     "$TEST" "test: secretKeyRef present"                 "secretKeyRef"
assert_contains     "$TEST" "test: frontend secret=frontend-secrets-<TEST_NAMESPACE>"       "frontend-secrets-<TEST_NAMESPACE>"
assert_contains     "$TEST" "test: worker secret=temporal-worker-secrets-<TEST_NAMESPACE>"  "temporal-worker-secrets-<TEST_NAMESPACE>"
assert_not_contains "$TEST" "test: VITE_SUPABASE_ANON_KEY not literal"   "value:.*VITE_SUPABASE_ANON_KEY"
assert_not_contains "$TEST" "test: SUPABASE_SERVICE_ROLE_KEY not literal" "value:.*SUPABASE_SERVICE_ROLE_KEY"

# test profile: scoped hardening guardrails for frontend and temporal-worker
TEST_FRONTEND_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: frontend/' <<<"$TEST")
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend runAsNonRoot"              "runAsNonRoot: true"
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend runAsUser=101"             "runAsUser: 101"
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend seccomp RuntimeDefault"    "type: RuntimeDefault"
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend allowPrivilegeEscalation"  "allowPrivilegeEscalation: false"
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend readOnlyRootFilesystem"    "readOnlyRootFilesystem: true"
assert_contains "$TEST_FRONTEND_DEPLOY" "test: frontend /tmp writable mount"       "mountPath: /tmp"
TEST_WORKER_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: temporal-worker/' <<<"$TEST")
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker runAsNonRoot"             "runAsNonRoot: true"
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker runAsUser=10001"          "runAsUser: 10001"
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker seccomp RuntimeDefault"   "type: RuntimeDefault"
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker allowPrivilegeEscalation" "allowPrivilegeEscalation: false"
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker readOnlyRootFilesystem"   "readOnlyRootFilesystem: true"
assert_contains "$TEST_WORKER_DEPLOY" "test: temporal-worker /tmp writable mount"      "mountPath: /tmp"

# ── prod profile ──────────────────────────────────────────────────────────────
echo ""
echo "=== values-prod.yaml ==="
PROD=$(helm template "$RELEASE" "$CHART" -f "$CHART/values-prod.yaml")

assert_contains     "$PROD" "prod: frontend Deployment renders"          "kind: Deployment"
assert_contains     "$PROD" "prod: frontend replicas=3"                  "replicas: 3"
assert_contains     "$PROD" "prod: worker replicas=2"                    "replicas: 2"
assert_contains     "$PROD" "prod: Ingress enabled"                      "kind: Ingress"
assert_contains     "$PROD" "prod: ingress host=frontend.<PROD_DOMAIN>"  "frontend\\.<PROD_DOMAIN>"
assert_contains     "$PROD" "prod: ingress className=nginx"              "ingressClassName: nginx"
assert_contains     "$PROD" "prod: frontend image tag prefix=prod-"      "/frontend:prod-"
assert_contains     "$PROD" "prod: worker image tag prefix=prod-"        "/temporal-worker:prod-"
assert_contains     "$PROD" "prod: ops-api image tag prefix=prod-"       "/temporal-worker:prod-"
assert_contains     "$PROD" "prod: temporal namespace=<PROD_NAMESPACE>"        "<PROD_NAMESPACE>"
assert_contains     "$PROD" "prod: temporal taskQueue=<PROD_NAMESPACE>-main"   "<PROD_NAMESPACE>-main"
assert_contains     "$PROD" "prod: secretKeyRef present"                 "secretKeyRef"
assert_contains     "$PROD" "prod: frontend secret=frontend-secrets-<PROD_NAMESPACE>"       "frontend-secrets-<PROD_NAMESPACE>"
assert_contains     "$PROD" "prod: worker secret=temporal-worker-secrets-<PROD_NAMESPACE>"  "temporal-worker-secrets-<PROD_NAMESPACE>"
assert_not_contains "$PROD" "prod: VITE_SUPABASE_ANON_KEY not literal"   "value:.*VITE_SUPABASE_ANON_KEY"
assert_not_contains "$PROD" "prod: SUPABASE_SERVICE_ROLE_KEY not literal" "value:.*SUPABASE_SERVICE_ROLE_KEY"

# prod profile: scoped hardening guardrails for frontend and temporal-worker
PROD_FRONTEND_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: frontend/' <<<"$PROD")
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend runAsNonRoot"              "runAsNonRoot: true"
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend runAsUser=101"             "runAsUser: 101"
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend seccomp RuntimeDefault"    "type: RuntimeDefault"
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend allowPrivilegeEscalation"  "allowPrivilegeEscalation: false"
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend readOnlyRootFilesystem"    "readOnlyRootFilesystem: true"
assert_contains "$PROD_FRONTEND_DEPLOY" "prod: frontend /tmp writable mount"       "mountPath: /tmp"
PROD_WORKER_DEPLOY=$(awk 'BEGIN{RS="---\n"; ORS=""} /kind: Deployment/ && /component: temporal-worker/' <<<"$PROD")
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker runAsNonRoot"             "runAsNonRoot: true"
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker runAsUser=10001"          "runAsUser: 10001"
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker seccomp RuntimeDefault"   "type: RuntimeDefault"
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker allowPrivilegeEscalation" "allowPrivilegeEscalation: false"
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker readOnlyRootFilesystem"   "readOnlyRootFilesystem: true"
assert_contains "$PROD_WORKER_DEPLOY" "prod: temporal-worker /tmp writable mount"      "mountPath: /tmp"

# ── deploy-dev.yml workflow assertions ────────────────────────────────────────
# Deterministic CI-local checks that the deploy workflow still wires the required
# dev secrets, values file, and image-tag overrides after the live-dev changes
# introduced in PR #106 and extended in PR #407.
#
# Assertions are scoped to the specific named step blocks — not whole-file grep —
# so a mention in a comment or another step cannot satisfy them.
echo ""
echo "=== deploy-dev.yml workflow assertions ==="
WORKFLOW_FILE=".github/workflows/deploy-dev.yml"
if [ ! -f "$WORKFLOW_FILE" ]; then
  fail "workflow: deploy-dev.yml exists at .github/workflows/deploy-dev.yml"
else
  pass "workflow: deploy-dev.yml exists"

  # Extract the deploy job's kubeconfig-configure step (stops at the next step header).
  KUBECONFIG_STEP=$(awk '
    /^      - name: Configure kubeconfig \(namespace-scoped gha-deployer\)/{capturing=1; print; next}
    capturing && /^      - name: /{capturing=0}
    capturing{print}
  ' "$WORKFLOW_FILE")

  # Extract the Helm upgrade step (stops at next step header or new job key).
  HELM_UPGRADE_STEP=$(awk '
    /^      - name: Helm upgrade \(<DEV_NAMESPACE>\)/{capturing=1; print; next}
    capturing && (/^  [a-z]/ || /^      - name: /){capturing=0}
    capturing{print}
  ' "$WORKFLOW_FILE")

  if [ -z "$KUBECONFIG_STEP" ]; then
    fail "workflow: 'Configure kubeconfig (namespace-scoped gha-deployer)' step extracted"
  else
    pass "workflow: 'Configure kubeconfig (namespace-scoped gha-deployer)' step extracted"
    assert_contains "$KUBECONFIG_STEP" "workflow: configure step writes KUBE_CONFIG_DEV to kubeconfig" \
      'secrets\.KUBE_CONFIG_DEV'
  fi

  if [ -z "$HELM_UPGRADE_STEP" ]; then
    fail "workflow: 'Helm upgrade (<DEV_NAMESPACE>)' step extracted"
  else
    pass "workflow: 'Helm upgrade (<DEV_NAMESPACE>)' step extracted"
    assert_contains "$HELM_UPGRADE_STEP" "workflow: helm upgrade step uses values-dev.yaml"              "charts/app/values-dev\\.yaml"
    assert_contains "$HELM_UPGRADE_STEP" "workflow: helm upgrade step sets frontend.image.tag"           "frontend\\.image\\.tag"
    assert_contains "$HELM_UPGRADE_STEP" "workflow: helm upgrade step sets temporalWorker.image.tag"     "temporalWorker\\.image\\.tag"
    assert_contains "$HELM_UPGRADE_STEP" "workflow: helm upgrade step sets opsApi.image.tag"             "opsApi\\.image\\.tag"
  fi
fi

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Summary: ${PASS} passed, ${FAIL} failed ==="

# Optional machine-readable summary for the CI test-trend history (ci-history branch).
# Written only when CI_HISTORY_JSON points somewhere; default behavior is unchanged.
if [ -n "${CI_HISTORY_JSON:-}" ]; then
  outcome=passed
  [ "$FAIL" -ne 0 ] && outcome=failed
  printf '{"outcome":"%s","expected":%d,"unexpected":%d}\n' "$outcome" "$PASS" "$FAIL" > "$CI_HISTORY_JSON"
fi

if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
