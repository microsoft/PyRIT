# Troubleshooting: Contributor Docker Installation

Common issues when using DevContainers with VS Code for PyRIT development.

## Container Build Fails

**Problem**: DevContainer fails to build

**Solutions**:
1. Ensure Docker is running
2. Check that you have sufficient disk space
3. Try rebuilding: `Dev Containers: Rebuild Container Without Cache`

## Extension Not Loading

**Problem**: VS Code extensions don't load in the container

**Solution**: Check the `.devcontainer/devcontainer.json` file to ensure extensions are listed. Rebuild the container if needed.

## Performance Issues

**Problem**: Container runs slowly

**Solutions**:
1. Allocate more resources to Docker in Docker Desktop settings
2. On Windows, ensure you're using WSL 2 backend for better performance
3. Close unnecessary applications to free up system resources
