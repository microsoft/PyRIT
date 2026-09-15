# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Update only the existing CoPyRIT container image and verify its current access mode."""

import os
import re
import subprocess
import sys
import time


def _az(*arguments: str) -> str:
    return subprocess.run(
        ["az", *arguments, "--only-show-errors"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _value(*arguments: str, query: str) -> str:
    value = _az(*arguments, "--query", query, "--output", "tsv")
    if not value or value in {"None", "null"} or "\n" in value or "\t" in value:
        raise ValueError(f"Azure returned no single value for {query}")
    return value


def _health_url(*, app_arguments: tuple[str, ...], environment_id: str, app_name: str) -> tuple[str, str]:
    access = _value("containerapp", "env", "show", "--ids", environment_id, query="properties.publicNetworkAccess")
    if access == "Enabled":
        hostname = _value("containerapp", "show", *app_arguments, query="properties.configuration.ingress.fqdn")
        suffix = ".azurecontainerapps.io"
    elif access == "Disabled":
        resource_group_id = environment_id.split("/providers/")[0]
        hostname = _value(
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com{resource_group_id}/providers/Microsoft.Cdn/profiles/"
            f"{app_name}-afd/afdEndpoints?api-version=2024-09-01",
            query="value[?properties.enabledState=='Enabled'].properties.hostName",
        )
        suffix = ".azurefd.net"
    else:
        raise ValueError(f"Unsupported ACA public network access state: {access}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", hostname) or not hostname.endswith(suffix):
        raise ValueError("Azure returned an unexpected health-check hostname")
    return f"https://{hostname}/api/health", access


def _wait_for_revision(*, app_arguments: tuple[str, ...], revision: str, image: str) -> None:
    arguments = ("containerapp", "revision", "show", *app_arguments, "--revision", revision)
    if _value(*arguments, query="properties.template.containers[0].image") != image:
        raise ValueError("The deployed revision does not contain the requested image")
    for attempt in range(1, 6):
        health = _az(*arguments, "--query", "properties.healthState", "--output", "tsv")
        print(f"Revision {revision} health attempt {attempt}/5: {health or '<not reported>'}", flush=True)
        if health == "Healthy":
            return
        if attempt < 5:
            time.sleep(120)
    raise RuntimeError("Deployed revision did not become healthy; networking was not changed")


def _wait_for_http_health(url: str) -> None:
    deadline = time.monotonic() + 300
    while (remaining := deadline - time.monotonic()) > 0:
        response = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                "--max-time",
                str(min(30, remaining)),
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        status = response.stdout.strip()
        print(f"Application health at {url}: {status or '<connection failed>'}", flush=True)
        if response.returncode:
            print(f"Health request failed: {response.stderr.strip()}", file=sys.stderr)
        elif status == "200":
            return
        time.sleep(max(0, min(30, deadline - time.monotonic())))
    raise RuntimeError("Application health check failed; networking was not changed")


def deploy_code(*, slot: str, resource_group: str, app_name: str, acr_resource_id: str, image: str) -> None:
    """Deploy an immutable image without applying templates or changing access settings."""
    if slot not in {"test", "prod"}:
        raise ValueError("Invalid deployment slot")
    if not re.fullmatch(r"[a-zA-Z0-9_.()-]{1,90}", resource_group) or resource_group.endswith("."):
        raise ValueError("Invalid deployment resource group")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}[a-z0-9]", app_name):
        raise ValueError("Invalid container app name")
    acr = re.fullmatch(
        r"/subscriptions/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r"/resourcegroups/[^/]+/providers/microsoft\.containerregistry/registries/([a-z0-9]{5,50})",
        acr_resource_id.casefold(),
    )
    if acr is None:
        raise ValueError("ACR resource ID is not canonical")
    subscription, registry = acr.groups()
    if not re.fullmatch(
        rf"{registry}\.azurecr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*"
        r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*@sha256:[0-9a-fA-F]{64}",
        image,
    ):
        raise ValueError("Image must be an immutable digest from the configured ACR")
    if _value("account", "show", query="id").casefold() != subscription:
        raise ValueError("Azure subscription does not match ACR")

    app_arguments = ("--resource-group", resource_group, "--name", app_name)
    show = ("containerapp", "show", *app_arguments)
    resource_group_id = f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
    environment_id = f"{resource_group_id}/providers/Microsoft.App/managedEnvironments/{app_name}-env"
    if _value(*show, query="properties.managedEnvironmentId").casefold() != environment_id.casefold():
        raise ValueError("Existing app does not belong to the expected environment")
    if _value(*show, query="properties.configuration.activeRevisionsMode") != "Single":
        raise ValueError("Code-only deployment requires the existing single-revision topology")
    if (
        _value(*show, query="length(properties.template.containers)") != "1"
        or _value(*show, query="properties.template.containers[0].name") != "pyrit-gui"
    ):
        raise ValueError("Code-only deployment requires the existing pyrit-gui container")
    url, access = _health_url(app_arguments=app_arguments, environment_id=environment_id, app_name=app_name)
    previous_image = _value(*show, query="properties.template.containers[0].image")
    print(f"Code-only {slot} deployment; ACA public access: {access}; verification URL: {url}", flush=True)
    print(f"Previous image (not automatically restored): {previous_image}", flush=True)
    if previous_image != image:
        _az(
            "containerapp",
            "update",
            *app_arguments,
            "--container-name",
            "pyrit-gui",
            "--image",
            image,
            "--output",
            "none",
        )
    revision = _value(*show, query="properties.latestRevisionName")
    _wait_for_revision(app_arguments=app_arguments, revision=revision, image=image)
    _wait_for_http_health(url)
    if (
        _value(*show, query="properties.latestRevisionName") != revision
        or _value(*show, query="properties.latestReadyRevisionName") != revision
        or _value(*show, query="properties.template.containers[0].image") != image
    ):
        raise RuntimeError("The verified image revision is not the current ready revision")
    final_url, final_access = _health_url(app_arguments=app_arguments, environment_id=environment_id, app_name=app_name)
    if (final_url, final_access) != (url, access):
        raise RuntimeError("The application's access mode changed during code-only deployment")
    print(f"Code deployment healthy: {revision}; networking unchanged; verified {url}", flush=True)


def main() -> int:
    """Read pipeline inputs and fail without rolling back infrastructure."""
    inputs = {
        "slot": "PYRIT_SLOT",
        "resource_group": "PYRIT_DEPLOYMENT_RESOURCE_GROUP",
        "app_name": "PYRIT_APP_NAME",
        "acr_resource_id": "PYRIT_ACR_RESOURCE_ID",
        "image": "PYRIT_CONTAINER_IMAGE",
    }
    try:
        values = {}
        for name, variable in inputs.items():
            value = os.environ.get(variable, "")
            if not value or value.startswith("$("):
                raise ValueError(f"Required deployment value is missing or unresolved: {variable}")
            values[name] = value
        deploy_code(**values)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"##vso[task.logissue type=error]Code deployment failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        print("No automatic image, database, or infrastructure rollback was attempted.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
