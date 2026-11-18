#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

set -euo pipefail
source "${BASH_SOURCE[0]%/*}/deployment_common.sh"

require_values PYRIT_ACR_NAME PYRIT_ACR_LOGIN_SERVER PYRIT_IMAGE_NAME PYRIT_SOURCE_VERSION \
  PYRIT_VERSION BUILD_BUILDID SYSTEM_JOBATTEMPT
repository_pattern='^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$'
if [[ ! "$PYRIT_ACR_NAME" =~ ^[a-z0-9]{5,50}$ \
  || "$PYRIT_ACR_LOGIN_SERVER" != "$PYRIT_ACR_NAME.azurecr.io" \
  || ! "$PYRIT_IMAGE_NAME" =~ $repository_pattern \
  || ! "$PYRIT_SOURCE_VERSION" =~ ^[0-9a-f]{40}$ \
  || ! "$BUILD_BUILDID" =~ ^[1-9][0-9]*$ || ! "$SYSTEM_JOBATTEMPT" =~ ^[1-9][0-9]*$ ]]; then
  deployment_error "Invalid registry, repository, source version, or build ID"
fi
source_commit=$(git rev-parse HEAD)
source_status=$(git status --porcelain)
if [[ "$source_commit" != "$PYRIT_SOURCE_VERSION" || -n "$source_status" ]]; then
  deployment_error "Published images require a clean checkout at the source commit"
fi

if [[ "$PYRIT_VERSION" == main ]]; then
  source_args=(--build-arg PYRIT_SOURCE=local --build-arg GIT_COMMIT="$source_commit" --build-arg GIT_MODIFIED=false)
  tag=$source_commit
else
  [[ "$PYRIT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || deployment_error "Expected a numeric PyPI release"
  python3 build_scripts/select_pypi_version.py --version "$PYRIT_VERSION"
  source_args=(--build-arg PYRIT_SOURCE=pypi --build-arg PYRIT_VERSION="$PYRIT_VERSION")
  tag="v$PYRIT_VERSION"
fi

repository="$PYRIT_ACR_LOGIN_SERVER/$PYRIT_IMAGE_NAME"
profile=none
if [[ "$USE_PROFILE" == [Tt]rue ]]; then
  require_values PROFILE_DIRECTORY PROFILE_RUN_ID PROFILE_COMMIT
  python3 infra/pipelines/deployment_profile.py check-artifact "$PROFILE_DIRECTORY" "$PROFILE_COMMIT"
  repository+="-internal"
  profile="run=$PROFILE_RUN_ID commit=$PROFILE_COMMIT"
fi
image="$repository:$tag-$BUILD_BUILDID-$SYSTEM_JOBATTEMPT"
docker build -f .devcontainer/Dockerfile -t pyrit-devcontainer .devcontainer/
docker build --no-cache -f docker/Dockerfile -t pyrit-deployment-base \
  --build-arg BASE_IMAGE=pyrit-devcontainer --build-arg PYRIT_EXTRAS=all "${source_args[@]}" .

if [[ "$USE_PROFILE" == [Tt]rue ]]; then
  # Only the allowlisted artifact and this public wrapper enter the private layer.
  cp infra/pipelines/deployment_profile.py "$PROFILE_DIRECTORY/"
  docker build -f infra/pipelines/profile.Dockerfile -t "$image" "$PROFILE_DIRECTORY"
  # Imports/validation errors may contain private source. Never stream them to CI.
  if ! docker run --rm --network none --entrypoint /opt/venv/bin/python "$image" \
      /opt/copyrit/deployment_profile.py check-image >/dev/null 2>&1; then
    deployment_error "Private profile failed configuration or initializer discovery in the selected image"
  fi
else
  docker tag pyrit-deployment-base "$image"
fi

# Read the installed package (including its frontend), never restamp a release with the pipeline SHA.
compatibility=$(docker run --rm -i --network none --entrypoint python "$image" - "$PYRIT_VERSION" "$source_commit" <<'PY'
import json
import sys
from pathlib import Path

import pyrit
from pyrit._compatibility import get_compatibility_id

selected, pipeline_commit = sys.argv[1:]
identity = get_compatibility_id()
root = Path(pyrit.__file__).parent
frontend = root / "backend/frontend"
if (json.loads((root / "_compatibility.json").read_text())["dirty"] is not False
        or (selected == "main" and identity != f"{pyrit.__version__}+g{pipeline_commit}")
        or (selected != "main" and selected != pyrit.__version__)
        or not (frontend / "index.html").is_file()
        or json.loads((frontend / "compatibility.json").read_text()) != {"compatibility_id": identity}):
    sys.exit("Selected Python and frontend artifacts must carry matching clean compatibility stamps")
print(identity)
PY
)
if [[ "$USE_PROFILE" == [Tt]rue ]]; then
  anonymous=$(az acr show --name "$PYRIT_ACR_NAME" --query anonymousPullEnabled -o json)
  [[ "$anonymous" == false ]] || deployment_error "Private profile images require ACR anonymous pull explicitly disabled"
fi
az acr login --name "$PYRIT_ACR_NAME"
push_output=$(docker push "$image")
digest=$(sed -nE 's/.*digest: (sha256:[0-9a-f]{64}).*/\1/p' <<< "$push_output" | tail -n 1)
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || deployment_error "Docker push did not return an immutable digest"
immutable_image="$repository@$digest"
echo "PyRIT=$PYRIT_VERSION compatibility=$compatibility public-pipeline-commit=$source_commit profile=$profile image=$immutable_image"
echo "##vso[task.setvariable variable=immutableImage;isOutput=true]$immutable_image"
