# Troubleshooting

## Setup

### ensurepip not available / No module named pip

If `make setup` fails with "ensurepip is not available" or "No module named pip":

```bash
sudo apt install python3-venv          # Generic (often pulls the right version)
# Or for Python 3.13 specifically:
sudo apt install python3.13-venv
```

If you previously ran setup and got a partial/broken `srv/` directory:

```bash
make clean-env
make setup
```

Run `make check-tools` to verify all prerequisites before setup.

---

## Health Check

### Failures

```bash
# Check container logs
docker logs health-check-<container-name>

# Verify port availability
netstat -tlnp | grep 8001

# Test manual container startup
docker run -d --rm --ulimit nofile=100000:100000 -p 8001:80 <image-name>

# Check ulimit inside container (should be 100000)
docker exec health-check-<container-name> sh -c 'ulimit -n'
```

The health check verifies that `ulimit -n` (open file descriptors) is 100000 inside each container and prints the actual value. If it is lower, the container fails.

### Why the health check verifies ulimit and large payload support

The health check does more than verify that a container is up:

- **ulimit (`ulimit -n`)**: Ensures the open file descriptor limit is high enough (100,000) for high-concurrency benchmarks.
- **Large payload**: Sends a 10MB HTTP POST (or 1MB WebSocket message) to verify the server can handle the test payloads.

A container that is merely "up" may be misconfigured. Verifying these settings catches subtle misconfigurations early and ensures results are valid.

---

## Docker

```bash
# Check Docker status
docker info

# Clean up containers
docker stop $(docker ps -q)
docker rm $(docker ps -aq)

# Rebuild images
make clean-build
make build
```

---

## Energy Measurement (0.00 J, container not found)

If energy is always 0 and logs say "Container 'X' not found in Scaphandre output":

- The framework runs Scaphandre with `--containers` to attribute energy by container name.
- **Cgroup fallback:** On systems where Scaphandre reports `container: null` for all consumers (e.g. cgroups v2, Debian 13), the scripts use a **cgroup fallback**: they read `/proc/<pid>/cgroup` and match against the Docker container ID. We rely on this until Scaphandre fixes container detection. See [Scaphandre issue #420](https://github.com/hubblo-org/scaphandre/issues/420).
- Ensure Scaphandre runs on the host (not in a container) with `sudo` and has access to `/proc` and cgroups.

### Debug Docker + Scaphandre

```bash
python tools/debug_scaphandre_docker.py --server_image <your-image> --duration 10
```

This starts a container, runs Scaphandre, and prints what container names (if any) appear. Use it to isolate whether the problem is Docker, Scaphandre, or the benchmark scripts.

### Verify Scaphandre

```bash
scaphandre --version
cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj
scaphandre -t 1
```

---

## Performance

```bash
# Increase timeouts
./scripts/check_health.sh --timeout 60 --startup 20

# Check system resources
htop
free -h
df -h
```

---

## Debug Mode

```bash
export DEBUG=1
make run-quick
tail -f logs/run_*.log
```

---

## ulimit and File Descriptor Limits

- All containers use `--ulimit nofile=100000:100000`.
- If you see ulimit or "too many open files" errors:
  - Increase system limits (e.g. `/etc/security/limits.conf`)
  - Configure Docker daemon to allow higher ulimits ([Docker docs](https://docs.docker.com/engine/reference/commandline/dockerd/#default-ulimit))
  - Restart Docker after changes
- Check limit inside a container: `docker exec <container> sh -c 'ulimit -n'`
- The health check fails if the limit is not 100000. See [CONFIGURATION_AUDIT.md](CONFIGURATION_AUDIT.md) for details.
