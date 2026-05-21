#!/bin/bash

# =============================================================================
# Odoo Custom Addons Setup & Docker Launcher
# =============================================================================
# Usage: ./setup_odoo.sh
# Make executable first: chmod +x setup_odoo.sh
# =============================================================================

set -e  # Exit immediately on error

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Base directory where this script lives (docker-compose.yml will also live here)
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADDONS_DIR="$BASE_DIR/custom_addons"

# Public base repo — provides nginx.conf, docker-compose.yaml, odoo.conf
BASE_REPO_URL="https://github.com/dewnuwan7/odoo19-docker.git"
# Files to copy from the base repo into BASE_DIR
BASE_FILES=("nginx.conf" "docker-compose.yaml" "odoo.conf")

# Private module repos (SSH) — add/remove as needed
REPOS=(
    "git@github.com:dewnuwan7/odoo19_community_modules.git"
    "git@github.com:dewnuwan7/hr_attendance_import.git"
    "git@github.com:dewnuwan7/payslip_reports.git"
    "git@github.com:dewnuwan7/payroll_attendance_integration.git"
    "git@github.com:dewnuwan7/whatsapp_documents.git"
    
)

# ── COLORS ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── GITHUB SSH SETUP ──────────────────────────────────────────────────────────
setup_github_ssh() {
    local KEY="$HOME/.ssh/id_ed25519"

    # 1. Install openssh-client if ssh-keygen is missing
    if ! command -v ssh-keygen &>/dev/null; then
        log "ssh-keygen not found — installing openssh-client..."
        sudo apt-get install -y -qq openssh-client 2>/dev/null \
            || sudo yum install -y openssh 2>/dev/null \
            || error "Could not install openssh-client. Please install it manually."
    fi

    # 2. Generate a key if none exists
    if [ ! -f "$KEY" ]; then
        log "No SSH key found. Generating a new ED25519 key..."
        ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)" -f "$KEY" -N ""
        log "SSH key generated at $KEY"
    else
        log "Existing SSH key found: $KEY"
    fi

    # 3. Start ssh-agent and load the key
    eval "$(ssh-agent -s)" > /dev/null
    ssh-add "$KEY" 2>/dev/null

    # 4. Add GitHub to known_hosts (avoids interactive prompt on first connect)
    mkdir -p ~/.ssh
    chmod 700 ~/.ssh
    if ! grep -q "github.com" ~/.ssh/known_hosts 2>/dev/null; then
        log "Adding GitHub to known_hosts..."
        ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null
    fi

    # 5. Test GitHub connection — if it fails, show the public key and wait
    log "Testing GitHub SSH connection..."
    if ! ssh -T git@github.com -o BatchMode=yes -o ConnectTimeout=8 2>&1 | grep -q "successfully authenticated"; then
        echo ""
        echo -e "${YELLOW}┌─────────────────────────────────────────────────────────────┐${NC}"
        echo -e "${YELLOW}│  GitHub SSH auth failed. Add this public key to your account │${NC}"
        echo -e "${YELLOW}│  → https://github.com/settings/ssh/new                      │${NC}"
        echo -e "${YELLOW}└─────────────────────────────────────────────────────────────┘${NC}"
        echo ""
        echo -e "${GREEN}Your public key:${NC}"
        echo "──────────────────────────────────────────────────────────────"
        cat "${KEY}.pub"
        echo "──────────────────────────────────────────────────────────────"
        echo ""
        read -rp "Press [Enter] once you've added the key to GitHub, then we'll retry... "
        echo ""

        if ssh -T git@github.com -o BatchMode=yes -o ConnectTimeout=10 2>&1 | grep -q "successfully authenticated"; then
            log "GitHub SSH authentication successful!"
        else
            error "GitHub SSH auth still failing. Check the key was saved correctly and try again."
        fi
    else
        log "GitHub SSH authentication successful!"
    fi
}

setup_github_ssh

# ── FETCH BASE CONFIG FILES (public repo) ─────────────────────────────────────
fetch_base_configs() {
    local TMP_DIR
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    log "Cloning base config repo..."
    git clone --depth=1 "$BASE_REPO_URL" "$TMP_DIR/base_repo" \
        || error "Failed to clone base repo: $BASE_REPO_URL"

    log "Copying config files to $BASE_DIR..."
    for FILE in "${BASE_FILES[@]}"; do
        SRC="$TMP_DIR/base_repo/$FILE"
        DST="$BASE_DIR/$FILE"
        if [ -f "$SRC" ]; then
            if [ -f "$DST" ]; then
                warn "$FILE already exists — overwriting with latest from base repo."
            fi
            cp "$SRC" "$DST"
            log "  ✔ $FILE"
        else
            warn "  $FILE not found in base repo — skipping."
        fi
    done

    trap - EXIT
    rm -rf "$TMP_DIR"
    log "Base config files ready."
}

fetch_base_configs

# ── CREATE & ENTER custom_addons ──────────────────────────────────────────────
log "Setting up custom_addons directory at: $ADDONS_DIR"
mkdir -p "$ADDONS_DIR"
cd "$ADDONS_DIR"
log "Now in: $(pwd)"

# ── CLONE OR PULL PRIVATE MODULE REPOS ───────────────────────────────────────
for REPO in "${REPOS[@]}"; do
    REPO_NAME=$(basename "$REPO" .git)

    if [ -d "$REPO_NAME/.git" ]; then
        log "Updating existing repo: $REPO_NAME"
        git -C "$REPO_NAME" pull --rebase origin HEAD \
            && log "$REPO_NAME updated." \
            || warn "Pull failed for $REPO_NAME — check for conflicts."
    else
        log "Cloning: $REPO → $REPO_NAME"
        git clone "$REPO" "$REPO_NAME" \
            && log "$REPO_NAME cloned." \
            || error "Failed to clone $REPO — check SSH access and repo URL."
    fi
done


log "All custom modules ready in $ADDONS_DIR."

# ── INSTALL DOCKER (if missing) ───────────────────────────────────────────────
install_docker() {
    log "Docker not found. Installing Docker Engine..."

    if [ "$EUID" -ne 0 ] && ! sudo -n true 2>/dev/null; then
        warn "Docker installation requires sudo privileges. You may be prompted for your password."
    fi

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_ID="${ID}"
    else
        error "Cannot detect OS. Please install Docker manually: https://docs.docker.com/engine/install/"
    fi

    case "$OS_ID" in
        ubuntu|debian)
            log "Detected $OS_ID — installing via apt..."
            sudo apt-get update -qq
            sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL "https://download.docker.com/linux/$OS_ID/gpg" \
                | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg

            echo \
              "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
              https://download.docker.com/linux/$OS_ID \
              $(lsb_release -cs) stable" \
              | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

            sudo apt-get update -qq
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;

        centos|rhel|fedora|rocky|almalinux)
            log "Detected $OS_ID — installing via dnf/yum..."
            if command -v dnf &>/dev/null; then
                PKG_MGR="dnf"
            else
                PKG_MGR="yum"
            fi
            sudo $PKG_MGR install -y yum-utils
            sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            sudo $PKG_MGR install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;

        *)
            error "Unsupported OS '$OS_ID'. Install Docker manually: https://docs.docker.com/engine/install/"
            ;;
    esac

    sudo systemctl enable --now docker
    log "Docker installed successfully."

    if ! groups "$USER" | grep -q docker; then
        sudo usermod -aG docker "$USER"
        warn "User '$USER' added to the 'docker' group."
        warn "For group changes to take effect without re-login, this script will use 'sudo docker'."
        DOCKER_CMD="sudo docker"
    fi
}

if ! command -v docker &>/dev/null; then
    install_docker
else
    log "Docker is already installed: $(docker --version)"
fi

# ── LAUNCH DOCKER COMPOSE ─────────────────────────────────────────────────────
cd "$BASE_DIR"
log "Starting Docker Compose from: $(pwd)"

if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="${DOCKER_CMD:-docker} compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    error "Docker Compose plugin not found. Install it: https://docs.docker.com/compose/install/"
fi

log "Running: $COMPOSE_CMD up -d"
$COMPOSE_CMD up -d

log "✅ Done! Odoo should be starting up."
log "   Run '$COMPOSE_CMD logs -f' to follow the logs."
