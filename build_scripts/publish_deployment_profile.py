# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Select or seed one environment's writable copy of the validated deployment profile."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from build_scripts.prepare_deployment_profile import ProfileManifest, validate_bundle


def _render_seed(*, bundle: Path, destination: Path, release_uri: str, manifest: ProfileManifest) -> None:
    shutil.copytree(bundle, destination)
    config = destination / ".pyrit_conf"
    with config.open("ab") as stream:
        stream.write(
            (
                "\n\n# Environment-local source supplied by the deployment pipeline.\n"
                f"custom_initializers_source: {json.dumps(release_uri + '/internal_initializers')}\n"
            ).encode()
        )
    seeded = {**manifest["files"], ".pyrit_conf": hashlib.sha256(config.read_bytes()).hexdigest()}
    (destination / "deployment-profile.json").write_text(
        json.dumps({**manifest, "baseline_files": manifest["files"], "files": seeded}, indent=2) + "\n",
        encoding="utf-8",
    )


def _indexed_config(*, index_file: Path, digest: str, container_uri: str) -> str:
    index = json.loads(index_file.read_text(encoding="utf-8"))
    if (
        not isinstance(index, dict)
        or type(index.get("schema_version")) is not int
        or index["schema_version"] != 1
        or index.get("profile") != "main"
        or index.get("profile_digest") != digest
    ):
        raise ValueError("Runtime profile index does not match the expected baseline.")
    uri = index.get("config_file_uri")
    if not isinstance(uri, str) or not re.fullmatch(
        re.escape(container_uri) + r"/releases/[0-9]+/[0-9a-f]{32}/main/\.pyrit_conf", uri
    ):
        raise ValueError("Indexed configuration URI is not a release in this environment's container.")
    return uri


def publish_profile(*, bundle: Path, container_uri: str, image: str, slot: str, build_id: str) -> None:
    """Verify the artifact once, publish or reuse its environment-local copy, and export the selected URI."""
    manifest = validate_bundle(bundle_directory=bundle, container_image=image)
    container_uri = container_uri.removesuffix("/")
    match = re.fullmatch(
        r"https://([a-z0-9]{3,24})\.blob\.core\.windows\.net/([a-z0-9][a-z0-9-]{1,61}[a-z0-9])", container_uri
    )
    if not match or slot not in {"test", "prod"} or not re.fullmatch(r"[0-9]+", build_id):
        raise ValueError("Expected a credential-free Blob container URI, a test/prod slot, and a numeric build ID.")
    account, container = match.groups()

    def blob(operation: str, *args: str) -> str:
        target = "--destination" if operation == "upload-batch" else "--container-name"
        command = [
            "az",
            "storage",
            "blob",
            operation,
            "--account-name",
            account,
            target,
            container,
            "--auth-mode",
            "login",
            "--only-show-errors",
            *args,
        ]
        return subprocess.run(command, check=True, stdout=subprocess.PIPE, text=True).stdout.rstrip("\r\n")

    digest = manifest["profile_digest"]
    index_name = f"profiles/main/{digest}.json"
    exists = blob("exists", "--name", index_name, "--query", "exists", "-o", "tsv")
    with tempfile.TemporaryDirectory() as work:
        index_file = Path(work) / "index.json"
        if exists == "true":
            blob("download", "--name", index_name, "--file", str(index_file), "--no-progress", "-o", "none")
            config_uri = _indexed_config(index_file=index_file, digest=digest, container_uri=container_uri)
            mode = "reuse"
        elif exists == "false":
            prefix = f"releases/{build_id}/{uuid.uuid4().hex}/main"
            release_uri = f"{container_uri}/{prefix}"
            config_uri = f"{release_uri}/.pyrit_conf"
            seed = Path(work) / "seed"
            _render_seed(bundle=bundle, destination=seed, release_uri=release_uri, manifest=manifest)
            upload_options = ("--overwrite", "false", "--no-progress", "-o", "none")
            blob("upload-batch", "--source", str(seed), "--destination-path", prefix, *upload_options)
            index = {"schema_version": 1, "profile": "main", "profile_digest": digest, "config_file_uri": config_uri}
            index_file.write_text(json.dumps(index) + "\n", encoding="utf-8")
            try:
                blob("upload", "--name", index_name, "--file", str(index_file), *upload_options)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(
                    "Could not create the index; retry the deployment. No live profile was overwritten."
                ) from error
            mode = "new"
        else:
            raise ValueError("Azure did not confirm whether the runtime profile index exists.")

    # A successful metadata read verifies the indexed file exists without rehashing GUI edits.
    etag = blob(
        "show", "--name", config_uri.removeprefix(container_uri + "/"), "--query", "properties.etag", "-o", "tsv"
    )
    if not re.fullmatch(r'0x[0-9a-fA-F]+|"0x[0-9a-fA-F]+"', etag):
        raise ValueError("Missing or malformed runtime configuration ETag.")
    print(
        f"Deployment profile ({slot}, {mode}): image={image}\n"
        f"Internal commit: {manifest['internal_commit']}; baseline: {digest}\n"
        f"Runtime configuration: {config_uri} (ETag {etag})"
    )
    print(f"##vso[task.setvariable variable=profileConfigFileUri;isOutput=true]{config_uri}")


if __name__ == "__main__":
    publish_profile(
        bundle=Path(os.environ["PYRIT_PROFILE_BUNDLE_DIRECTORY"]),
        container_uri=os.environ["PYRIT_DEPLOYMENT_PROFILES_CONTAINER_URI"],
        image=os.environ["PYRIT_CONTAINER_IMAGE"],
        slot=os.environ["PYRIT_SLOT"],
        build_id=os.environ["PYRIT_BUILD_ID"],
    )
