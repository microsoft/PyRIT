# Troubleshooting: User Docker Installation

Common issues when running PyRIT with Docker.

## JupyterLab Not Accessible

**Problem**: Cannot access `http://localhost:8888`

**Solutions**:
1. Check if the container is running:
   ```bash
   docker ps
   ```

2. View container logs:
   ```bash
   docker-compose logs pyrit
   ```

3. Ensure port 8888 is not already in use:
   ```bash
   # On Linux/macOS
   lsof -i :8888

   # On Windows (PowerShell)
   netstat -ano | findstr :8888
   ```

## Permission Errors

**Problem**: Permission denied errors when accessing notebooks or data

**Solution**: Set appropriate permissions:

```bash
chmod -R 777 notebooks/ data/ ../assets/
```

## Missing Environment Files

**Problem**: Container fails with missing environment file errors

**Solution**: Ensure all environment files are created:

```bash
ls -la ../.env ../.env.local .env.container.settings
```

If any are missing, create them from the examples as shown in the [Docker Installation](../install_docker.md) guide.

## Container Build Fails

**Problem**: Docker build fails with dependency errors

**Solutions**:
1. Clear Docker cache and rebuild:
   ```bash
   docker-compose build --no-cache
   ```

2. Ensure you have sufficient disk space:
   ```bash
   docker system df
   ```

3. Prune old images if needed:
   ```bash
   docker system prune -a
   ```
