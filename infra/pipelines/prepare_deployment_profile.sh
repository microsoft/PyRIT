#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

source "${BASH_SOURCE[0]%/*}/deployment_common.sh"

main() (
  set -euo pipefail
  require_values PYRIT_INTERNAL_SOURCE_DIRECTORY PYRIT_INTERNAL_SOURCE_VERSION \
    PYRIT_PROFILE_BUNDLE_DIRECTORY PYRIT_CONTAINER_IMAGE
  local source_commit source_status
  source_commit=$(git -C "$PYRIT_INTERNAL_SOURCE_DIRECTORY" rev-parse HEAD)
  source_status=$(git -C "$PYRIT_INTERNAL_SOURCE_DIRECTORY" status --porcelain)
  if [[ "$source_commit" != "$PYRIT_INTERNAL_SOURCE_VERSION" || -n "$source_status" ]]; then
    deployment_error "Deployment profiles require a clean checkout at the resolved internal commit"
  fi
  local profile_directory="$PYRIT_INTERNAL_SOURCE_DIRECTORY/pyrit_internal/deployment/main"
  local output_parent output_name
  output_parent=$(dirname "$PYRIT_PROFILE_BUNDLE_DIRECTORY")
  output_name=$(basename "$PYRIT_PROFILE_BUNDLE_DIRECTORY")
  mkdir -p "$output_parent"
  docker run --rm --entrypoint python \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --volume "$profile_directory:/profiles/main:ro" \
    --volume "$output_parent:/release" \
    "$PYRIT_CONTAINER_IMAGE" \
    -m build_scripts.prepare_deployment_profile \
    --profile-directory /profiles/main \
    --output-directory "/release/$output_name" \
    --internal-commit "$source_commit" \
    --container-image "$PYRIT_CONTAINER_IMAGE"
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
