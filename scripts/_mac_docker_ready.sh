# Shared Docker-readiness helper for macOS. SOURCE this file from
# setup.sh / start.sh / stop.sh -- it's not meant to be executed directly.
#
# Provides:
#   ensure_docker_ready      -- locate Docker, open Docker Desktop if
#                               needed, wait up to 180s for the daemon.
#                               Exits the calling script on hard failure.
#   _find_docker_cli         -- internal; sets DOCKER_BIN if docker is
#                               on PATH. stop.sh uses it directly to
#                               avoid blocking on a dead daemon.
#
# The PATH export below is the load-bearing line: Docker Desktop's
# credential helper (docker-credential-desktop) lives in the same bin
# dir as the docker CLI, and `docker compose build` shells out to it.
# If that bin dir isn't on PATH, builds fail with:
#   docker-credential-desktop: executable file not found in $PATH
# Putting Docker.app's bin first guarantees both the CLI and the
# credential helper resolve, regardless of which shell the user is
# running and whether Docker Desktop has run its first-launch PATH
# install step yet.
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"


# Resolve a usable docker CLI. With the PATH export above, this should
# almost always hit on `command -v docker`. The /Applications/... and
# /usr/local/bin fallbacks are belt-and-suspenders for weird PATH
# clobbering in user dotfiles.
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

_docker_desktop_installed() {
    [[ -d "/Applications/Docker.app" ]]
}

_launch_docker_desktop() {
    if command -v open >/dev/null 2>&1; then
        open -a Docker >/dev/null 2>&1 || true
    fi
}

# Block up to 180s (36 iterations × 5s) waiting for `docker info` to
# succeed. Returns 0 on success, 1 on timeout.
_wait_for_docker_daemon() {
    local max_iter=36   # 36 * 5s = 180s
    local i=0
    printf "Waiting for Docker Desktop to start"
    while (( i < max_iter )); do
        if docker info >/dev/null 2>&1; then
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
ensure_docker_ready() {
    # Step 1: do we have a docker CLI on PATH (post-export)?
    if _find_docker_cli; then
        if docker info >/dev/null 2>&1; then
            echo "[OK] Docker Desktop is running."
            return 0
        fi
        # CLI works but daemon isn't responding. Likely Docker Desktop
        # is installed but not started. Launch it and wait.
        if _docker_desktop_installed; then
            echo "Docker CLI found but the daemon isn't responding yet."
            echo "Opening Docker Desktop..."
            _launch_docker_desktop
            if _wait_for_docker_daemon; then
                echo "[OK] Docker Desktop is running."
                return 0
            fi
            cat >&2 <<'ERR'
[FAIL] Docker Desktop is installed but did not become ready within 180s.
       Open Docker Desktop manually from /Applications, wait until the
       menu-bar whale icon says "Docker is running", then rerun the
       script (./setup.sh or ./start.sh).
ERR
            exit 1
        fi
        cat >&2 <<'ERR'
[FAIL] Docker CLI is installed but the daemon isn't responding, and
       Docker Desktop isn't in /Applications. If you're using Colima or
       another Docker-compatible engine, start it manually, then rerun.
ERR
        exit 1
    fi

    # Step 2: no CLI on PATH (even after our export). Is Docker.app there?
    if _docker_desktop_installed; then
        echo "Docker Desktop is installed, but the docker command is not available yet."
        echo "Opening Docker Desktop..."
        _launch_docker_desktop
        # Re-probe after a brief settle. Docker Desktop installs its
        # CLI symlink to /usr/local/bin (which our PATH already covers)
        # on first launch.
        local tries=0
        while (( tries < 24 )); do   # 24 * 5s = 120s
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
       CLI helper), then rerun the script.
ERR
            exit 1
        fi
        if _wait_for_docker_daemon; then
            echo "[OK] Docker Desktop is running."
            return 0
        fi
        cat >&2 <<'ERR'
[FAIL] Docker Desktop is installed but did not become ready within 180s.
       Open Docker Desktop manually, wait until it says "Docker is
       running", then rerun the script.
ERR
        exit 1
    fi

    # Step 3: neither CLI nor Docker.app. The only true "not installed".
    cat >&2 <<'ERR'
[FAIL] Docker Desktop is not installed on this Mac.
       Install Docker Desktop for Mac:
         https://www.docker.com/products/docker-desktop/
       Then rerun the script.
ERR
    exit 1
}
