#!/usr/bin/env bash
# Calliope installer: checks this machine, asks a few questions, then installs
# Calliope with Docker Compose. Run it again later to upgrade.
#
#   curl -fsSL https://usecalliope.com/install.sh | bash
#
# To read it first:
#   curl -fsSLO https://usecalliope.com/install.sh && less install.sh && bash install.sh
#
# Needs: Linux x86-64, Docker Engine with Compose 2.30 or newer, curl and tar.
# Nothing else: the one-time configuration step runs inside a Calliope image.
#
# Unattended installs answer the questions with environment variables:
#   OPENROUTER_API_KEY, CALLIOPE_ADMIN_EMAIL,
#   CALLIOPE_PUBLIC_URL (https://your.host, or http://localhost:PORT)
#   CALLIOPE_ADMIN_PASSWORD is optional; when unset, one is generated and printed.
# plus any other setting from settings.env.example (CALLIOPE_TLS, ...).
# Other options:
#   CALLIOPE_DIR        where to install (default: ./calliope)
#   CALLIOPE_VERSION    release to install (default: the newest release)
#   CALLIOPE_BUNDLE     a release tarball already on disk, instead of downloading
#   CALLIOPE_REPO       GitHub repository with the releases (default: ryrobes/calliope)
#   CALLIOPE_YES=1      don't ask for confirmation
#   CALLIOPE_SKIP_BACKUP=1   upgrade without the pre-upgrade database dump

set -euo pipefail

REPO="${CALLIOPE_REPO:-ryrobes/calliope}"
MIN_COMPOSE="2.30"
MIN_MEM_GB=15
MIN_DISK_GB=50

if [ -t 1 ]; then
    B=$'\e[1m'; D=$'\e[2m'; R=$'\e[31m'; Y=$'\e[33m'; G=$'\e[32m'; N=$'\e[0m'
else
    B=""; D=""; R=""; Y=""; G=""; N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$G" "$N" "$B" "$*" "$N"; }
warn() { printf '%swarning:%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# With `curl | bash`, stdin is this script, so questions read the terminal.
interactive() { [ -z "${CALLIOPE_YES:-}" ] && { : </dev/tty; } 2>/dev/null; }

ask() { # ask VAR "Question" [default]
    local reply
    if ! interactive; then
        [ -n "${3:-}" ] || die "$2 (set it in the environment for an unattended install)"
        printf -v "$1" '%s' "$3"; return
    fi
    read -r -p "$2${3:+ ${D}[$3]${N}}: " reply </dev/tty
    printf -v "$1" '%s' "${reply:-${3:-}}"
}

ask_secret() { # ask_secret VAR "Question"
    local reply
    interactive || die "$2 (set it in the environment for an unattended install)"
    read -r -s -p "$2: " reply </dev/tty
    printf '\n' >/dev/tty
    printf -v "$1" '%s' "$reply"
}

confirm() { # confirm "Question" (default yes)
    local reply
    interactive || return 0
    read -r -p "$1 ${D}[Y/n]${N}: " reply </dev/tty
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

version_ge() { # version_ge 2.31.0 2.30
    local IFS=.
    local -a a=($1) b=($2)
    local i
    for i in 0 1 2; do
        (( ${a[i]:-0} > ${b[i]:-0} )) && return 0
        (( ${a[i]:-0} < ${b[i]:-0} )) && return 1
    done
    return 0
}

# Under `set -o pipefail` a reader that stops early (grep -q, head) fails the
# whole pipeline with SIGPIPE, so command output is captured before it's tested.
port_in_use() {
    if command -v ss >/dev/null 2>&1; then
        [ -n "$(ss -ltnH "( sport = :$1 )" 2>/dev/null)" ]
    else
        (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
    fi
}

free_port() { # free_port 5432 -> 5432, or the next free port above it
    local p=$1
    while port_in_use "$p"; do p=$((p + 1)); done
    printf '%s' "$p"
}

local_http_bind() { # local_http_bind URL -> 127.0.0.1:port (80 when an http URL omits the port)
    local rest=${1#*://}
    rest=${rest%%[/?#]*}
    local port=80
    if [[ "$rest" == *:* ]]; then
        port=${rest##*:}
    fi
    [[ "$port" =~ ^[0-9]+$ ]] || die "CALLIOPE_PUBLIC_URL has no usable port: $1"
    printf '127.0.0.1:%s' "$port"
}

random_password() {
    local p=""
    while [ "${#p}" -lt 20 ]; do
        p+=$(head -c 64 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' || true)
    done
    printf '%s' "${p:0:20}"
}

check_machine() {
    step "Checking this machine"
    [ "$(uname -s)" = Linux ] || die "Calliope runs on Linux (this is $(uname -s))."
    [ "$(uname -m)" = x86_64 ] || die "Calliope's images are built for x86-64 (this is $(uname -m))."
    local cmd
    for cmd in curl tar sha256sum docker; do
        command -v "$cmd" >/dev/null 2>&1 || die "$cmd is not installed."
    done
    local info
    if ! info=$(docker info 2>&1); then
        if grep -qi "permission denied" <<<"$info"; then
            die "This user can't use Docker. Add it to the docker group (sudo usermod -aG docker \$USER, then log in again) or run the installer with sudo."
        fi
        die "Docker isn't running: ${info##*$'\n'}"
    fi
    local compose
    compose=$(docker compose version --short 2>/dev/null) || die "Docker Compose v2 isn't installed (the docker compose plugin)."
    compose=${compose#v}
    version_ge "$compose" "$MIN_COMPOSE" || die "Docker Compose $compose is too old; Calliope needs $MIN_COMPOSE or newer."
    say "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), Compose $compose"

    local mem_gb disk_gb
    mem_gb=$(( $(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
    if (( mem_gb < MIN_MEM_GB )); then
        warn "This machine has ${mem_gb} GB of memory; Calliope is sized for 16 GB."
        confirm "Continue anyway?" || exit 1
    fi
    mkdir -p "$ROOT"
    disk_gb=$(( $(df -Pk "$ROOT" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
    if (( disk_gb < MIN_DISK_GB )); then
        warn "$ROOT has ${disk_gb} GB free; plan for about 50 GB."
        confirm "Continue anyway?" || exit 1
    fi
}

latest_version() { # newest tag without a leading v. API first; releases/latest redirect if that fails.
    # api.github.com allows 60 unauthenticated requests an hour per IP and is
    # blocked on some networks. /releases/latest is the fallback: it redirects
    # to /releases/tag/<tag> (the newest stable release; the API also returns
    # release candidates). CALLIOPE_VERSION is applied by the caller.
    local releases tag effective
    if releases=$(curl -fsSL --retry 1 --max-time 30 "https://api.github.com/repos/$REPO/releases?per_page=1"); then
        tag=$(json_field tag_name <<<"$releases")
        tag=${tag#v}
        if [ -n "$tag" ]; then
            printf '%s' "$tag"
            return 0
        fi
    fi
    warn "GitHub's release API didn't return a version; trying the releases/latest redirect."
    if ! effective=$(curl -fsSL -I -o /dev/null --retry 1 --max-time 30 -w '%{url_effective}' -L \
            "https://github.com/$REPO/releases/latest"); then
        return 1
    fi
    effective=${effective%/}
    # A repository with no releases redirects to /releases, not /releases/tag/<tag>.
    case "$effective" in
        */releases/tag/*) ;;
        *) return 1 ;;
    esac
    tag=${effective##*/}
    tag=${tag%%[?#]*}
    tag=${tag#v}
    [ -n "$tag" ] || return 1
    printf '%s' "$tag"
}

resolve_version() {
    if [ -n "${CALLIOPE_BUNDLE:-}" ]; then
        [ -f "$CALLIOPE_BUNDLE" ] || die "CALLIOPE_BUNDLE=$CALLIOPE_BUNDLE is not a file."
        local manifest
        manifest=$(tar -xzOf "$CALLIOPE_BUNDLE" --wildcards 'calliope-*/release.json') \
            || die "$CALLIOPE_BUNDLE isn't a Calliope release bundle."
        VERSION=$(release_version <<<"$manifest")
    elif [ -n "${CALLIOPE_VERSION:-}" ]; then
        VERSION=${CALLIOPE_VERSION#v}
    else
        VERSION=$(latest_version) || die "Couldn't find the latest release of $REPO; set CALLIOPE_VERSION."
    fi
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*$ ]] || die "Unexpected release version: '$VERSION'"
}

fetch_bundle() { # unpack release $VERSION into $ROOT/versions/$VERSION
    local target="$ROOT/versions/$VERSION"
    if [ -f "$target/compose.yaml" ]; then
        say "Release $VERSION is already unpacked in $target"
        return
    fi
    local work tarball name="calliope-$VERSION-linux-amd64.tar.gz"
    work=$(mktemp -d "$ROOT/.download.XXXXXX")
    CLEANUP+=("$work")
    if [ -n "${CALLIOPE_BUNDLE:-}" ]; then
        tarball=$CALLIOPE_BUNDLE
        if [ -f "$CALLIOPE_BUNDLE.sha256" ]; then
            (cd "$(dirname "$CALLIOPE_BUNDLE")" && sha256sum -c --quiet "$(basename "$CALLIOPE_BUNDLE").sha256") \
                || die "$CALLIOPE_BUNDLE doesn't match its checksum."
        fi
    else
        local url="https://github.com/$REPO/releases/download/v$VERSION"
        say "Downloading Calliope $VERSION"
        curl -fL --progress-bar -o "$work/$name" "$url/$name" || die "Download failed: $url/$name"
        curl -fsSL -o "$work/$name.sha256" "$url/$name.sha256" || die "Download failed: $url/$name.sha256"
        (cd "$work" && sha256sum -c --quiet "$name.sha256") || die "The download doesn't match its published checksum."
        tarball="$work/$name"
    fi
    tar -xzf "$tarball" -C "$work"
    [ -f "$work/calliope-$VERSION/compose.yaml" ] || die "The release bundle has no compose.yaml."
    mkdir -p "$ROOT/versions"
    mv "$work/calliope-$VERSION" "$target"
}

json_field() { # json_field NAME < json: the first "NAME": "value" (string fields only)
    local text match
    text=$(cat)
    match=$(grep -o "\"$1\": *\"[^\"]*\"" <<<"$text" || true)
    match=${match%%$'\n'*}
    match=${match%\"}
    printf '%s' "${match##*\"}"
}

openrouter_unfunded() { # openrouter_unfunded FILE — true when remaining limit or budget credit is zero or less
    # Null means "no cap", not zero. Only numeric limit_remaining / remaining_usd count.
    local file=$1 field line value
    [ -f "$file" ] || return 1
    for field in limit_remaining remaining_usd; do
        while IFS= read -r line; do
            value=${line#*:}
            value=${value//[[:space:]]/}
            if awk -v n="$value" 'BEGIN { exit !(n+0 <= 0) }'; then
                return 0
            fi
        done < <(grep -oE "\"${field}\"[[:space:]]*:[[:space:]]*-?[0-9]+([.][0-9]+)?" "$file" || true)
    done
    return 1
}

release_version() { # the bundle version from a release.json on stdin (older bundles: the product version)
    local manifest version
    manifest=$(cat)
    version=$(json_field bundle_version <<<"$manifest")
    [ -n "$version" ] || version=$(json_field calliope_version <<<"$manifest")
    printf '%s' "$version"
}

server_image() { # the calliope-server image pinned by a bundle
    grep -m1 -oE 'image: *[^ ]+/calliope-server[:@][^ ]+' "$1/compose.yaml" | sed -E 's/image: *//'
}

configure_bundle() { # run the bundle's configure.py in its own server image: no Python on the host
    local dir=$1 image
    image=$(server_image "$dir")
    [ -n "$image" ] || die "Couldn't find the server image in $dir/compose.yaml."
    say "Preparing the configuration tool (the Calliope server image)"
    docker pull -q "$image" >/dev/null
    docker run --rm --entrypoint python3 --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$dir:$dir" -w "$dir" "$image" configure.py --settings settings.env >/dev/null
}

collect_settings() {
    step "A few questions"
    # 1. Model provider key.
    OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
    while [ -z "$OPENROUTER_API_KEY" ]; do
        say "Calliope uses your OpenRouter key for its models (https://openrouter.ai/keys)."
        ask_secret OPENROUTER_API_KEY "OpenRouter API key"
    done
    # A bad key otherwise surfaces minutes later as an unhealthy memory service.
    # The key goes to OpenRouter only, as a header on stdin (never in argv).
    local status body
    body=$(mktemp)
    CLEANUP+=("$body")
    status=$(printf 'Authorization: Bearer %s\n' "$OPENROUTER_API_KEY" \
        | curl -s -o "$body" -w '%{http_code}' -H @- https://openrouter.ai/api/v1/key || true)
    case "$status" in
        200)
            say "${G}✓${N} OpenRouter accepted the key"
            if openrouter_unfunded "$body"; then
                OPENROUTER_UNFUNDED=1
                warn "OpenRouter accepted the key, but it shows no remaining limit or credit. Model calls and Hindsight need a funded key: https://openrouter.ai/settings/credits"
            fi
            ;;
        401|403) die "OpenRouter rejected that key (https://openrouter.ai/keys)." ;;
        *) warn "Couldn't check the key with OpenRouter (HTTP $status); continuing." ;;
    esac
    rm -f "$body"

    # 2. Administrator login.
    CALLIOPE_ADMIN_EMAIL=${CALLIOPE_ADMIN_EMAIL:-}
    while ! [[ "$CALLIOPE_ADMIN_EMAIL" =~ ^[^[:space:],@]+@[^[:space:],@]+\.[^[:space:],@]+$ ]]; do
        ask CALLIOPE_ADMIN_EMAIL "Your email (the administrator login)"
    done
    GENERATED_PASSWORD=""
    if [ -z "${CALLIOPE_ADMIN_PASSWORD:-}" ]; then
        if ! interactive; then
            CALLIOPE_ADMIN_PASSWORD=$(random_password)
            GENERATED_PASSWORD=$CALLIOPE_ADMIN_PASSWORD
        else
            local again=""
            while :; do
                ask_secret CALLIOPE_ADMIN_PASSWORD "Choose a password, 12+ characters (Enter to generate one)"
                if [ -z "$CALLIOPE_ADMIN_PASSWORD" ]; then
                    CALLIOPE_ADMIN_PASSWORD=$(random_password)
                    GENERATED_PASSWORD=$CALLIOPE_ADMIN_PASSWORD
                    break
                fi
                if (( ${#CALLIOPE_ADMIN_PASSWORD} < 12 )); then say "That's shorter than 12 characters."; continue; fi
                ask_secret again "Same password again"
                [ "$again" = "$CALLIOPE_ADMIN_PASSWORD" ] && break
                say "Those didn't match."
            done
        fi
    fi
    (( ${#CALLIOPE_ADMIN_PASSWORD} >= 12 )) || die "CALLIOPE_ADMIN_PASSWORD must be at least 12 characters."

    # 3. How people reach it.
    EXTRA_SETTINGS=()
    if [ -z "${CALLIOPE_PUBLIC_URL:-}" ]; then
        say ""
        say "How will people reach Calliope?"
        say "  1) Only from this computer (http://localhost)"
        say "  2) At a domain name that points to this server (automatic HTTPS)"
        say "  3) Through my own HTTPS reverse proxy"
        local choice
        ask choice "Choose 1, 2 or 3" "1"
        case "$choice" in
            1)
                local port
                port=$(free_port 8080)
                CALLIOPE_PUBLIC_URL="http://localhost:$port"
                ;;
            2)
                local host
                ask host "Domain name (for example calliope.example.com)"
                host=${host#https://}; host=${host%%/*}
                [ -n "$host" ] || die "A domain name is required for automatic HTTPS."
                CALLIOPE_PUBLIC_URL="https://$host"
                if ! getent hosts "$host" >/dev/null 2>&1; then
                    warn "$host doesn't resolve yet. Point its DNS at this server before people sign in; automatic HTTPS needs it."
                fi
                local p
                for p in 80 443 3001; do
                    port_in_use "$p" && die "Port $p is already in use; automatic HTTPS needs ports 80, 443 and 3001."
                done
                ;;
            3)
                ask CALLIOPE_PUBLIC_URL "The https:// address your proxy serves"
                local proxy_ip
                ask proxy_ip "The IP address your proxy connects from" "127.0.0.1"
                EXTRA_SETTINGS+=("CALLIOPE_TLS=proxy" "CALLIOPE_TRUSTED_PROXIES=$proxy_ip")
                say "Point the proxy at http://127.0.0.1:8080 (Calliope) and http://127.0.0.1:8081 (DataRabbit)."
                ;;
            *) die "Choose 1, 2 or 3." ;;
        esac
    fi
    case "$CALLIOPE_PUBLIC_URL" in
        http://localhost*|http://127.0.0.1*)
            # Local only: nothing listens beyond this machine.
            local data_port sql_port
            data_port=$(free_port 3001)
            sql_port=$(free_port 5432)
            [ -n "${CALLIOPE_DATA_URL:-}" ] || EXTRA_SETTINGS+=("CALLIOPE_DATA_URL=http://localhost:$data_port" "CALLIOPE_DATA_BIND=127.0.0.1:$data_port")
            [ -n "${CALLIOPE_SQL_BIND:-}" ] || EXTRA_SETTINGS+=("CALLIOPE_SQL_BIND=127.0.0.1:$sql_port")
            # An unattended CALLIOPE_PUBLIC_URL skips the choice above, which is
            # what used to leave this published on every interface.
            if [ -z "${CALLIOPE_HTTP_BIND:-}" ]; then
                CALLIOPE_HTTP_BIND=$(local_http_bind "$CALLIOPE_PUBLIC_URL")
            fi
            ;;
        https://*) ;;
        *) die "CALLIOPE_PUBLIC_URL must start with https:// (or be http://localhost:PORT)." ;;
    esac
    if [ -z "${CALLIOPE_SQL_BIND:-}" ] && [[ "$CALLIOPE_PUBLIC_URL" == https://* ]] && port_in_use 5432; then
        local sql_port
        sql_port=$(free_port 5433)
        warn "Port 5432 is in use; Calliope's PostgreSQL will listen on $sql_port."
        EXTRA_SETTINGS+=("CALLIOPE_SQL_BIND=0.0.0.0:$sql_port")
    fi
}

write_settings() { # write settings.env (0600) into $1 from the answers and the environment
    local file="$1/settings.env" key
    local passthrough=(CALLIOPE_DATA_URL CALLIOPE_DATA_BIND CALLIOPE_HTTP_BIND CALLIOPE_HTTPS_BIND
        CALLIOPE_ACME_EMAIL CALLIOPE_SQL_HOST CALLIOPE_SQL_BIND CALLIOPE_SQL_TLS CALLIOPE_TLS
        CALLIOPE_TRUSTED_PROXIES COMPOSE_PROFILES COMPOSE_PROJECT_NAME
        CALLIOPE_DEMO_MODE CALLIOPE_DEMO_SCHEMAS CALLIOPE_ROOT_REDIRECT)
    (
        umask 077
        {
            printf '# Written by install.sh on %s. Holds your bootstrap secrets: keep it private.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY"
            printf 'CALLIOPE_ADMIN_EMAIL=%s\n' "$CALLIOPE_ADMIN_EMAIL"
            printf 'CALLIOPE_ADMIN_PASSWORD=%s\n' "$CALLIOPE_ADMIN_PASSWORD"
            printf 'CALLIOPE_PUBLIC_URL=%s\n' "$CALLIOPE_PUBLIC_URL"
            for key in "${passthrough[@]}"; do
                [ -n "${!key:-}" ] && printf '%s=%s\n' "$key" "${!key}"
            done
            for key in "${EXTRA_SETTINGS[@]}"; do
                printf '%s\n' "$key"
            done
        } >"$file"
    )
}

# What makes this install itself lives at $ROOT; each release directory links
# to it, so an upgrade copies nothing and a rollback is just switching `current`.
DURABLE=(.env state settings.env connections.json)

adopt_durable() { # adopt_durable DIR: move DIR's install data to $ROOT and link it
    local item
    for item in "${DURABLE[@]}"; do
        if [ -e "$1/$item" ] && [ ! -L "$1/$item" ]; then
            mv "$1/$item" "$ROOT/$item"
        fi
        if [ -e "$ROOT/$item" ] && [ ! -e "$1/$item" ]; then
            ln -s "../../$item" "$1/$item"
        fi
    done
}

defer_hindsight() { # defer_hindsight FILE — keep Hindsight out of the health wait
    # `required: false` is not enough: Compose still starts an optional
    # dependency and `up --wait` waits for it. hermes only needs Hindsight to
    # have started, so drop that edge. calliope stays required. This is the
    # only depends_on hindsight in the shipping compose file.
    cat >"$1" <<'EOF'
services:
  hermes:
    depends_on: !override
      calliope:
        condition: service_healthy
        required: true
EOF
}

bring_up() { # bring_up DIR [text appended to the failure message]
    local dir=$1 suffix=${2:-} listing name override
    local -a core=()
    local saw_hindsight=0
    if ! listing=$(cd "$dir" && docker compose config --services); then
        die "Couldn't list Calliope's services in $dir."
    fi
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if [ "$name" = hindsight ]; then
            saw_hindsight=1
        else
            core+=("$name")
        fi
    done <<<"$listing"
    [ "${#core[@]}" -gt 0 ] || die "Calliope's compose file lists no services in $dir."
    if [ "$saw_hindsight" -eq 1 ]; then
        override=$(mktemp)
        CLEANUP+=("$override")
        defer_hindsight "$override"
        # Named services plus their required dependencies. Hindsight is neither,
        # so a 401 restart loop cannot fail this wait.
        if ! (cd "$dir" && docker compose -f compose.yaml -f "$override" up -d --wait --wait-timeout 900 --remove-orphans "${core[@]}"); then
            rm -f "$override"
            die "Calliope didn't come up healthy. See: cd $dir && docker compose ps -a && docker compose logs --tail 100${suffix}"
        fi
        rm -f "$override"
        start_hindsight "$dir"
    else
        (cd "$dir" && docker compose up -d --wait --wait-timeout 900 --remove-orphans "${core[@]}") \
            || die "Calliope didn't come up healthy. See: cd $dir && docker compose ps -a && docker compose logs --tail 100${suffix}"
    fi
}

start_hindsight() { # start_hindsight DIR — non-critical. Sets HINDSIGHT_UNHEALTHY on failure.
    local dir=$1
    if [ -n "${OPENROUTER_UNFUNDED:-}" ]; then
        (cd "$dir" && docker compose up -d hindsight) || true
        HINDSIGHT_UNHEALTHY=1
        return 0
    fi
    # The healthcheck allows about five minutes. Don't spend the whole stack
    # timeout here: everything else is already up.
    if ! (cd "$dir" && docker compose up -d --wait --wait-timeout 420 hindsight); then
        HINDSIGHT_UNHEALTHY=1
    fi
}

start_stack() { # start_stack DIR
    step "Downloading the images (the first time this takes a while)"
    (cd "$1" && docker compose pull)
    step "Starting Calliope"
    bring_up "$1" " (re-running the installer resumes)"
}

install_fresh() {
    collect_settings
    say ""
    say "Calliope will be installed in ${B}$ROOT${N} and served at ${B}$CALLIOPE_PUBLIC_URL${N}."
    confirm "Go ahead?" || exit 1
    resolve_version
    step "Getting Calliope $VERSION"
    fetch_bundle
    local dir="$ROOT/versions/$VERSION"
    write_settings "$dir"
    step "Configuring (generates this install's secrets and certificates)"
    configure_bundle "$dir"
    adopt_durable "$dir"
    ln -sfn "versions/$VERSION" "$ROOT/current"
    start_stack "$ROOT/current"
    finish "installed"
}

backup_postgres_dump() { # backup_postgres_dump DIR DEST — pg_dumpall.gz, mode 0600
    # The redirect has to share this subshell; outside it the file is created 0644.
    (umask 077; cd "$1" && docker compose exec -T postgres pg_dumpall -U postgres | gzip >"$2")
}

upgrade() {
    local dir old
    dir=$(cd "$ROOT/current" && pwd -P)
    old=$(release_version <"$dir/release.json")
    resolve_version
    if [ "$old" = "$VERSION" ]; then
        say "Calliope $VERSION is already installed in $ROOT."
        confirm "Make sure it's running?" && start_stack "$ROOT/current" && finish "running"
        return 0
    fi
    step "Upgrading Calliope $old to $VERSION"
    say "Your settings, secrets and data are kept, and the database is backed up first."
    confirm "Go ahead?" || exit 1
    fetch_bundle
    local new="$ROOT/versions/$VERSION" backup=""

    if [ -z "${CALLIOPE_SKIP_BACKUP:-}" ]; then
        backup="$ROOT/backups/pre-$VERSION-$(date -u +%Y%m%dT%H%M%SZ)"
        step "Backing up the database and install state to $backup"
        (umask 077; mkdir -p "$backup")
        local running
        running=$(cd "$dir" && docker compose ps --status running --services 2>/dev/null || true)
        if grep -qx postgres <<<"$running"; then
            backup_postgres_dump "$dir" "$backup/pg_dumpall.sql.gz" \
                || die "The database backup failed; nothing was changed. (CALLIOPE_SKIP_BACKUP=1 skips it.)"
        else
            warn "PostgreSQL isn't running, so the database wasn't dumped (its volume is kept as is)."
        fi
        local items=() item
        for item in "${DURABLE[@]}"; do [ -e "$ROOT/$item" ] && items+=("$item"); done
        # Containers create some of state/ as root (the PostgreSQL TLS keys), so
        # archive it from inside the release's own image rather than as this user.
        (umask 077; docker run --rm --user 0 --entrypoint tar -v "$ROOT:/install:ro" "$(server_image "$dir")" \
            -czf - -C /install "${items[@]}" >"$backup/install-state.tgz") \
            || die "Backing up $ROOT/state failed; nothing was changed. (CALLIOPE_SKIP_BACKUP=1 skips it.)"
    fi

    adopt_durable "$new"
    step "Downloading the new images"
    (cd "$new" && docker compose pull)
    step "Updating the database"
    (cd "$new" && docker compose up -d --wait --wait-timeout 600 postgres) \
        || die "PostgreSQL didn't start on the new release; the database hasn't been changed yet. To keep running $old: cd $dir && docker compose up -d --wait"
    (cd "$new" && docker compose exec -T postgres psql -U postgres -d rvbbit -v ON_ERROR_STOP=1 -qAt \
        -c 'SET client_min_messages TO warning' -c 'SELECT rvbbit.migrate()' >/dev/null) \
        || die "Database migrations failed. See: cd $new && docker compose logs --tail 100 postgres. The pre-upgrade backup is in ${backup:-(none: CALLIOPE_SKIP_BACKUP was set)}."
    (cd "$new" && docker compose exec -T -e PGUSER=postgres -e PGDATABASE=rvbbit postgres rvbbit-schema-release record >/dev/null)
    step "Starting Calliope $VERSION"
    bring_up "$new"
    ln -sfn "versions/$VERSION" "$ROOT/current"
    finish "upgraded to $VERSION"
    [ -n "$backup" ] && say "  Backup    $backup  ${D}(before the upgrade)${N}"
    say "  ${D}$old stays in $dir, but it refuses to run against the upgraded database:${N}"
    say "  ${D}going back means restoring the backup into a fresh database.${N}"
}

finish() {
    local url
    url=$(json_field web <"$ROOT/current/connections.json")
    step "Calliope is $1"
    say ""
    say "  Open      ${B}${url:-$CALLIOPE_PUBLIC_URL}${N}"
    [ -n "${CALLIOPE_ADMIN_EMAIL:-}" ] && say "  Sign in   $CALLIOPE_ADMIN_EMAIL"
    [ -n "${GENERATED_PASSWORD:-}" ] && say "  Password  ${B}$GENERATED_PASSWORD${N}  ${D}(generated; also in $ROOT/current/settings.env)${N}"
    say ""
    say "  Install   $ROOT/current  ${D}(keep .env and state/ private and backed up)${N}"
    say "  Endpoints $ROOT/current/connections.json  ${D}(web, DataRabbit, MCP, PostgreSQL)${N}"
    say "  Manage    cd $ROOT/current && docker compose ps | logs -f | stop | up -d"
    say "  Upgrade   run this installer again"
    if [ -n "${HINDSIGHT_UNHEALTHY:-}" ]; then
        say ""
        say "  ${Y}Warning${N}   Hindsight did not become healthy, so long-term memory is unavailable."
        say "            An OpenRouter key with no remaining credit leaves it restarting (HTTP 401)."
        say "            Credits  https://openrouter.ai/settings/credits"
        say "            Logs     cd $ROOT/current && docker compose logs --tail 100 hindsight"
        say "            Retry    cd $ROOT/current && docker compose up -d --wait hindsight"
    fi
}

CLEANUP=()
cleanup() { local d; for d in "${CLEANUP[@]}"; do rm -rf "$d"; done; }

main() {
    trap cleanup EXIT
    ROOT=${CALLIOPE_DIR:-$PWD/calliope}
    case "$ROOT" in /*) ;; *) ROOT="$PWD/$ROOT" ;; esac
    say "${B}Calliope installer${N}"
    check_machine
    if [ -f "$ROOT/current/.env" ]; then
        upgrade
    else
        [ -e "$ROOT/current" ] && die "$ROOT/current exists but isn't a configured install; move it aside first."
        install_fresh
    fi
}

main "$@"
