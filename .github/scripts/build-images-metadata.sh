#!/usr/bin/env bash
set -euo pipefail

mode="${1:-}"

write_output() {
  local line="$1"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "$line" >> "$GITHUB_OUTPUT"
  else
    echo "$line"
  fi
}

case "$mode" in
  push-gate)
    is_main_push="${IS_MAIN_PUSH:-false}"
    write_output "is_main_push=$is_main_push"

    if [[ "$is_main_push" == "true" && -n "${REGISTRY:-}" && -n "${REGISTRY_USERNAME:-}" && -n "${REGISTRY_PASSWORD:-}" ]]; then
      write_output "enabled=true"
    else
      write_output "enabled=false"
    fi
    ;;

  image-tags)
    if [[ -z "${GITHUB_SHA:-}" ]]; then
      echo "image-tags mode: GITHUB_SHA is required" >&2
      exit 1
    fi
    if [[ "${#GITHUB_SHA}" -lt 12 ]]; then
      echo "image-tags mode: GITHUB_SHA must be at least 12 characters" >&2
      exit 1
    fi

    if [[ -z "${IMAGE_NAME:-}" ]]; then
      echo "image-tags mode: IMAGE_NAME is required" >&2
      exit 1
    fi

    if [[ ! "${IMAGE_NAME}" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]]; then
      echo "image-tags mode: IMAGE_NAME must use lowercase letters, numbers, dots, underscores, and dashes only" >&2
      exit 1
    fi

    short_sha="${GITHUB_SHA:0:12}"
    if [[ -n "${REGISTRY:-}" ]]; then
      image_repo="${REGISTRY}/${IMAGE_NAME}"
    else
      image_repo="local/${IMAGE_NAME}"
    fi
    tags=("${image_repo}:${GITHUB_SHA}" "${image_repo}:${short_sha}")

    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
      {
        echo "tags<<EOF"
        printf '%s\n' "${tags[@]}"
        echo "EOF"
      } >> "$GITHUB_OUTPUT"
    else
      printf '%s\n' "${tags[@]}"
    fi
    ;;

  skip-message)
    echo "Image push skipped: set vars.ACR_LOGIN_SERVER and configure ACR_USERNAME/ACR_PASSWORD secrets to enable registry push."
    ;;

  *)
    echo "Usage: $0 {push-gate|image-tags|skip-message}" >&2
    exit 1
    ;;
esac
