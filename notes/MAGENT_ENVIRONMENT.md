# Magent Environment Awareness

## Where Am I Running?

After compacting, you may not remember where you are. Here's how to check:

### Check if on hunter

```bash
# Check hostname - should be username@hunter (e.g., justin@hunter, rj@hunter)
hostname

# Or check if you have docker socket access
docker ps 2>/dev/null && echo "You have docker access - likely on hunter"

# Check for magenta-postgres container
docker ps --filter "name=magenta-postgres" --format "{{.Names}}"
```

If you see containers like `justin-arthel`, `rj-arthel`, `magenta-postgres`, `mcp-server`, `memory-lane`, `watcher` - you're in a user container on hunter with docker socket access.

### What You Can Do on Hunter

When you're in a user container on hunter (like `justin@hunter`), you have:

1. **Docker socket access** - You can inspect the host system:
   ```bash
   docker ps                    # See all containers
   docker logs <container>      # View logs
   docker exec <container> ...  # Run commands in other containers
   ```

2. **Database access** - Connect to shared PostgreSQL:
   ```bash
   # Via the postgres container
   docker exec magenta-postgres psql -U magent -d magenta_memory
   ```

3. **Shared services access**:
   - MCP server on port 8000
   - Memory Lane on port 3000
   - Watcher monitors conversations

### Container Hostnames

- `justin@hunter` - Justin's development container on hunter
- `rj@hunter` - RJ's development container on hunter
- Future users follow same pattern: `username@hunter`

## When NOT on Hunter

If `hostname` returns something else (like your laptop name), or `docker ps` fails, you're likely:
- On your laptop
- On maybelle CI/CD server
- In a different environment

Check `$DEVELOPER_NAME` environment variable - it's set on hunter containers.
