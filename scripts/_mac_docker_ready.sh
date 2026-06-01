# Shared Docker-readiness helper for macOS. SOURCE this file from
# setup.sh / start.sh -- it's not meant to be executed directly.
#
# Provides:
#   $DOCKER_BIN              -- resolved path to the docker CLI
#   ensure_docker_ready      -- locate Docker, open Docker Desktop if
#                               needed, wait up to 180s for the daemon.
#                               Exits the calling script on hard failure.
#
# Why a helper: brand-new Mac users often install Docker Desktop, never
# open it, then run ./setup.sh and see "Docker not installed" -- because
# the docker CLI lives inside Docker.app's bundle until Docker Desktop
# has been launched at least once and added itself to PATH. The logic
# below distinguishes "Docker.app missing" (really not installed) from
# "CLI not yet on PATH" (installed, just dormant), and starts the app
# automatically.

# Resolve a usable docker CLI into DOCKER_BIN. Tries:
#   1. docker on PATH                       (post-first-launch state)
#   2. Docker.app's bundled binary          (just-installed state)
#   3. /usr/local/bin/docker                (Intel Macs; symlink target)
# Returns 0 on success, 1 on failure. Sets DOCKER_BIN if found.
_find_docker_cli() {
    if command -v docker >/dev/null 2>&1; then
        DOCKER_BIN="$(command -v docker)"
        return 0
    fi
    if [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
        DOCKER_BIN="/Applications/Docker.app/Contents/Resources/bin/docker"
        return 0
    fi
    if [[ -x "/usr/local/bin/docker" ]]; then
        DOCKER_BIN="/usr/local/bin/docker"
        return 0
    fi
    return 1
}

# Is Docker Desktop installed (regardless of whether the CLI is on PATH)?
_docker_desktop_installed() {
    [[ -d "/Applications/Docker.app" ]]
}

# Try to launch Docker Desktop. Does not wait.
_launch_docker_desktop() {
    if command -v open >/dev/null 2>&1; then
        open -a Docker >/dev/null 2>&1 || true
    fi
}

# Block up to 180s (36 iterations × 5s) waiting for `docker info` to
# succeed. Prints a progress dot every iteration so the user knows it's
# alive. Returns 0 on success, 1 on timeout.
_wait_for_docker_daemon() {
    local max_iter=36   # 36 * 5s = 180s
    local i=0
    printf "Waiting for Docker Desktop to start"
    while (( i < max_iter )); do
        if "${DOCKER_BIN}" info >/dev/null 2>&1; then
            printf " ready.\n"
            return 0
        fi
        printf "."
        sleep 5
        ((i++))
    done
    printf " timed out.\n"
    return 1
}

# Top-level: ensure Docker is fully ready or exit the calling script.
# On success, $DOCKER_BIN points at a working docker CLI and the daemon
# is responsive. On failure, prints a clear next-step instruction and
# exits 1 from the calling script.
ensure_docker_ready() {
    # Step 1: do we have a docker CLI at all?
    if _find_docker_cli; then
        # CLI found. Maybe daemon is already running -- short-circuit.
        if "${DOCKER_BIN}" info >/dev/null 2>&1; then
            echo "[OK] Docker Desktop is running. (docker: ${DOCKER_BIN})"
            return 0
        fi
        # CLI works but daemon isn't responding. Likely Docker Desktop
        # is installed but not started. Launch it and wait.
        if _docker_desktop_installed; then
            echo "Docker CLI found but the daemon isn't responding yet."
            echo "Opening Docker Desktop..."
            _launch_docker_desktop
            if _wait_for_docker_daemon; then
                echo "[OK] Docker Desktop is running. (docker: ${DOCKER_BIN})"
                return 0
            fi
            cat >&2 <<'ERR'
[FAIL] Docker Desktop is installed but did not become ready within 180s.
       Open Docker Desktop manually from /Applications, wait until the
       menu-bar whale icon says "Docker is running", then rerun:

           ./setup.sh   (or ./start.sh)

ERR
            exit 1
        fi
        # CLI exists but Docker.app doesn't -- weird state (CLI-only
        # install, Colima, etc.). Tell the user to start their daemon.
        cat >&2 <<'ERR'
[FAIL] Docker CLI is installed but the daemon isn't responding, and
       Docker Desktop isn't in /Applications. If you're using Colima or
       another Docker-compatible engine, start it manually, then rerun.
ERR
        exit 1
    fi

    # Step 2: no CLI on PATH. Is Docker Desktop installed?
    if _docker_desktop_installed; then
        echo "Docker Desktop is installed, but the docker command is not available yet."
        echo "Opening Docker Desktop..."
        _launch_docker_desktop
        # Try again after a brief settle -- Docker Desktop adds its CLI
        # symlink to /usr/local/bin on first launch.
        local tries=0
        while (( tries < 24 )); do   # 24 * 5s = 120s for the symlink
            if _find_docker_cli; then
                break
            fi
            printf "."
            sleep 5
            ((tries++))
        done
        if ! _find_docker_cli; then
            cat >&2 <<'ERR'

[FAIL] Docker Desktop is installed but the docker command never appeared
       on PATH. Open Docker Desktop manually from /Applications, complete
       its first-run setup (it may ask for your password to install the
       CLI helper), then rerun:

           ./setup.sh   (or ./start.sh)

ERR
            exit 1
        fi
        # CLI is now resolvable -- wait for the daemon.
        if _wait_for_docker_daemon; then
            echo "[OK] Docker Desktop is running. (docker: ${DOCKER_BIN})"
            return 0
        fi
        cat >&2 <<'ERR'
[FAIL] Docker Desktop is installed but did not become ready within 180s.
       Open Docker Desktop manually, wait until it says "Docker is
       running", then rerun the script.
ERR
        exit 1
    fi

    # Step 3: neither CLI nor Docker.app. This is the only true
    # "Docker is not installed" path.
    cat >&2 <<'ERR'
[FAIL] Docker Desktop is not installed on this Mac.
       Install Docker Desktop for Mac:
         https://www.docker.com/products/docker-desktop/
       Then rerun the script.
ERR
    exit 1
}
