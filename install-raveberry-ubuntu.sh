#!/usr/bin/env bash
set -Eeuo pipefail

# =============================
# Raveberry Interactive Installer for Ubuntu, runs on localhost. Serves through newt to your server running Pangolin.
# Simply download where you want it as install-raveberry.sh, then run `chmod +x install-raveberry.sh` to make it executable.
# =============================

# ---------- Defaults ----------
DEFAULT_VENV_PARENT="/opt/venvs"
DEFAULT_VENV_DIR="/opt/venvs/raveberry-cli"
DEFAULT_CONFIG_PATH="$HOME/raveberry.yaml"
DEFAULT_INSTALL_DIR="/opt/raveberry/"
DEFAULT_HOSTNAME="127.0.0.1"
DEFAULT_PORT="8080"
DEFAULT_RAVEBERRY_REPO="https://github.com/OrangeC7/raveberry.git"
DEFAULT_RAVEBERRY_REF="master"

DEFAULT_YOUTUBE="true"
DEFAULT_SPOTIFY="false"
DEFAULT_SOUNDCLOUD="false"

# Automatically disable raspberry pi features.
DEFAULT_SCREEN_VIS="false"
DEFAULT_LED_VIS="false"
DEFAULT_AUDIO_NORMALIZATION="false"
DEFAULT_HOTSPOT="false"
DEFAULT_BUZZER="false"

# ---------- Logging ----------
log() { printf "\n==> %s\n" "$*"; }
warn() { printf "\n[WARN] %s\n" "$*" >&2; }
die() { printf "\n[ERROR] %s\n" "$*" >&2; exit 1; }

# ---------- Validators ----------
is_abs_path() { [[ "${1:-}" == /* ]]; }
is_valid_port() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
  (( "$1" >= 1 && "$1" <= 65535 ))
}
normalize_bool() {
  case "${1,,}" in
    y|yes|true|1|on) echo "true" ;;
    n|no|false|0|off) echo "false" ;;
    *) return 1 ;;
  esac
}

# ---------- Prompt helpers ----------
ask_text() {
  local __var="$1" prompt="$2" default="$3" validator="${4:-}"
  local val
  while true; do
    read -r -p "$prompt [$default]: " val || true
    val="${val:-$default}"
    if [[ -n "$validator" ]]; then
      if "$validator" "$val"; then
        printf -v "$__var" '%s' "$val"
        return 0
      else
        warn "Invalid value: $val"
      fi
    else
      printf -v "$__var" '%s' "$val"
      return 0
    fi
  done
}

ask_bool() {
  local __var="$1" prompt="$2" default="$3"
  local val norm
  while true; do
    read -r -p "$prompt [${default}]: " val || true
    val="${val:-$default}"
    if norm="$(normalize_bool "$val")"; then
      printf -v "$__var" '%s' "$norm"
      return 0
    fi
    warn "Please answer yes/no (y/n)."
  done
}

show_explainer() {
  cat <<'TXT'

We will configure only relevant options:

- install_directory:
  Where Raveberry app files are installed on disk.

- hostname:
  Hostname value used by installer/system config.

- port:
  Web server port (80 = normal HTTP).

- youtube:
  Enables YouTube source support. Installer includes YouTube deps only when true.

- spotify:
  Enables Spotify source support. Installer adds Spotify deps only when true.

- soundcloud:
  Enables SoundCloud source support. Installer adds SoundCloud deps only when true.

Pi/hardware-centric options (LED/screen/buzzer/hotspot) are auto-set to false in this script.

TXT
}

# ---------- Collect config ----------
collect_answers() {
  show_explainer

  ask_text VENV_PARENT "Virtualenv parent directory" "$DEFAULT_VENV_PARENT" is_abs_path
  ask_text VENV_DIR "Virtualenv directory for raveberry CLI" "$DEFAULT_VENV_DIR" is_abs_path
  ask_text CONFIG_PATH "Path to write raveberry.yaml" "$DEFAULT_CONFIG_PATH"
  ask_text INSTALL_DIR "Raveberry install_directory" "$DEFAULT_INSTALL_DIR" is_abs_path
  ask_text HOSTNAME_VALUE "Bind address for Raveberry (127.0.0.1 = local-only)" "$DEFAULT_HOSTNAME"
  ask_text PORT_VALUE "Web port" "$DEFAULT_PORT" is_valid_port

  ask_bool YOUTUBE "Enable YouTube support?" "$DEFAULT_YOUTUBE"
  ask_bool SPOTIFY "Enable Spotify support? (requires Spotify account setup later)" "$DEFAULT_SPOTIFY"
  ask_bool SOUNDCLOUD "Enable SoundCloud support?" "$DEFAULT_SOUNDCLOUD"

  SCREEN_VIS="$DEFAULT_SCREEN_VIS"
  LED_VIS="$DEFAULT_LED_VIS"
  AUDIO_NORMALIZATION="$DEFAULT_AUDIO_NORMALIZATION"
  HOTSPOT="$DEFAULT_HOTSPOT"
  BUZZER="$DEFAULT_BUZZER"
}

print_summary() {
  cat <<EOF

==================== REVIEW ====================
1) VENV_PARENT            = $VENV_PARENT
2) VENV_DIR               = $VENV_DIR
3) CONFIG_PATH            = $CONFIG_PATH
4) INSTALL_DIR            = $INSTALL_DIR
5) HOSTNAME               = $HOSTNAME_VALUE
6) PORT                   = $PORT_VALUE
7) YOUTUBE                = $YOUTUBE
8) SPOTIFY                = $SPOTIFY
9) SOUNDCLOUD             = $SOUNDCLOUD

Automatically disabled:
- screen_visualization    = $SCREEN_VIS
- led_visualization       = $LED_VIS
- audio_normalization     = $AUDIO_NORMALIZATION
- hotspot                 = $HOTSPOT
- buzzer                  = $BUZZER
================================================
EOF
}

edit_loop() {
  while true; do
    print_summary
    echo "Choose: [C]ontinue, [E]dit a field, [Q]uit"
    read -r -p "> " action || true
    action="${action:-C}"
    case "${action,,}" in
      c|continue)
        return 0
        ;;
      q|quit)
        die "User aborted."
        ;;
      e|edit)
        read -r -p "Enter field number to edit (1-9): " n || true
        case "$n" in
          1) ask_text VENV_PARENT "Virtualenv parent directory" "$VENV_PARENT" is_abs_path ;;
          2) ask_text VENV_DIR "Virtualenv directory for raveberry CLI" "$VENV_DIR" is_abs_path ;;
          3) ask_text CONFIG_PATH "Path to write raveberry.yaml" "$CONFIG_PATH" ;;
          4) ask_text INSTALL_DIR "Raveberry install_directory" "$INSTALL_DIR" is_abs_path ;;
          5) ask_text HOSTNAME_VALUE "Hostname for Raveberry" "$HOSTNAME_VALUE" ;;
          6) ask_text PORT_VALUE "Web port" "$PORT_VALUE" is_valid_port ;;
          7) ask_bool YOUTUBE "Enable YouTube support?" "$YOUTUBE" ;;
          8) ask_bool SPOTIFY "Enable Spotify support?" "$SPOTIFY" ;;
          9) ask_bool SOUNDCLOUD "Enable SoundCloud support?" "$SOUNDCLOUD" ;;
          *) warn "Invalid field number." ;;
        esac
        ;;
      *)
        warn "Unknown option."
        ;;
    esac
  done
}

# ---------- Install steps ----------
run_install() {
  log "[1/6] Installing prerequisites"
  sudo apt update
  sudo apt install -y python3 python3-pip python3-venv python3-dev git
  sudo apt install -y build-essential libpq-dev pkg-config rsync

  log "[2/6] Creating venv location and ownership"
  sudo mkdir -p "$VENV_PARENT"
  sudo chown "$USER:$USER" "$VENV_PARENT"

  log "[3/6] Creating and activating venv"
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  log "[4/6] Installing raveberry CLI from your GitHub repo"
  pip install --upgrade pip setuptools wheel

  # install from our repo
  pip install --force-reinstall \
    "raveberry[install] @ git+${DEFAULT_RAVEBERRY_REPO}@${DEFAULT_RAVEBERRY_REF}"

  raveberry --help >/dev/null

  python - <<'PY'
import importlib.metadata as m
print("Installed raveberry version:", m.version("raveberry"))
PY

  log "[5/6] Writing config to $CONFIG_PATH"
  cat > "$CONFIG_PATH" <<YAML
install_directory: $INSTALL_DIR
hostname: $HOSTNAME_VALUE
port: $PORT_VALUE

youtube: $YOUTUBE
spotify: $SPOTIFY
soundcloud: $SOUNDCLOUD

screen_visualization: $SCREEN_VIS
led_visualization: $LED_VIS
audio_normalization: $AUDIO_NORMALIZATION
hotspot: $HOTSPOT
buzzer: $BUZZER

cache_dir:
cache_medium:

hotspot_ssid: Raveberry
hotspot_password:
homewifi:

remote_key:
remote_bind_address:
remote_ip:
remote_port:
remote_url:

db_backup:
backup_command:
YAML

  ls -l "$CONFIG_PATH"

  log "[6/6] Running installer"
  raveberry --config-file "$CONFIG_PATH" install

  log "Done. Open via hostname/IP on port $PORT_VALUE."
}

main() {
  collect_answers
  edit_loop
  run_install
}

main "$@"
