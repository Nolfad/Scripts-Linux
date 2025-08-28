#!/usr/bin/env bash
set -euo pipefail

# ===== parte configuravel =====
LIBRARY_PATH="/mnt/m2/SteamLibrary"     # sua SteamLibrary
APPID=3792580                           # SCUM Dedicated Server
SCHEDULE=("00:00" "06:00" "14:00")      # horários de restart (HH:MM 24h)
LOGDIR="$HOME/scum"
LOGFILE="$LOGDIR/scum-server.log"
SIGINT_TIMEOUT="${SIGINT_TIMEOUT:-15}"   # seg. para aguardar após SIGINT (Ctrl-C)
WINESERVER_TIMEOUT="${WINESERVER_TIMEOUT:-30}"   # seg. para aguardar após wineserver -k
# ===================================

cd "$HOME"
mkdir -p "$LOGDIR"

# Steam root
STEAM_ROOT="${STEAM_ROOT:-$HOME/.local/share/Steam}"
[ -d "$STEAM_ROOT" ] || STEAM_ROOT="$HOME/.steam/steam"

# Proton: tenta GE primeiro, depois Proton oficial
find_proton() {
  local p
  p="$(find "$STEAM_ROOT/compatibilitytools.d" -maxdepth 1 -type d -iname "GE-Proton*" 2>/dev/null | sort -V | tail -n1 || true)"
  if [[ -n "$p" && -x "$p/proton" ]]; then echo "$p/proton"; return 0; fi
  p="$(find "$STEAM_ROOT/steamapps/common" -maxdepth 1 -type d -iname "Proton *" 2>/dev/null | sort -V | tail -n1 || true)"
  if [[ -n "$p" && -x "$p/proton" ]]; then echo "$p/proton"; return 0; fi
  return 1
}

PROTON="$(find_proton || true)"
if [[ -z "${PROTON:-}" ]]; then
  echo "ERRO: Proton não encontrado (GE ou oficial)." | tee -a "$LOGFILE"
  exit 1
fi

# wine/wineserver dentro do Proton (varia: files/bin ou dist/bin)
find_ws_bin() {
  local base
  base="$(dirname "$PROTON")"
  # GE/Proton comuns:
  local ws
  ws="$(find "$base" -type f -path "*/files/bin/wineserver" -print -quit 2>/dev/null || true)"
  [[ -z "$ws" ]] && ws="$(find "$base" -type f -path "*/dist/bin/wineserver" -print -quit 2>/dev/null || true)"
  echo "$ws"
}

WINESERVER="$(find_ws_bin || true)"

# Caminho do servidor e exe (variações de nome)
SERVER_DIR="$LIBRARY_PATH/steamapps/common/SCUM Server/SCUM/Binaries/Win64"
detect_server_exe() {
  local candidates=(
    "SCUMServer.exe"
    "SCUMServer-Win64-Shipping.exe"
    "SCUMServer-Win64-Test.exe"
  )
  for c in "${candidates[@]}"; do
    [[ -f "$SERVER_DIR/$c" ]] && { echo "$SERVER_DIR/$c"; return 0; }
  done
  local any
  any="$(ls "$SERVER_DIR"/SCUMServer*.exe 2>/dev/null | head -n1 || true)"
  [[ -n "$any" ]] && { echo "$any"; return 0; }
  return 1
}
SERVER_EXE="$(detect_server_exe || true)"
if [[ -z "${SERVER_EXE:-}" ]]; then
  echo "ERRO: não encontrei executável do SCUM em: $SERVER_DIR" | tee -a "$LOGFILE"
  exit 1
fi

# Compat/Prefix
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$LIBRARY_PATH/steamapps/compatdata/$APPID"
export WINEPREFIX="$STEAM_COMPAT_DATA_PATH/pfx"

server_pid=""
server_pgid=""
stop_requested=0

ts() { date '+%F %T'; }

start_server() {
  echo "[$(ts)] Iniciando SCUM: $SERVER_EXE" | tee -a "$LOGFILE"
  cd "$SERVER_DIR"
  # novo grupo p/ matar tudo junto depois
  setsid "$PROTON" run "$SERVER_EXE" -log -multihome=0.0.0.0 -nobattleye >>"$LOGFILE" 2>&1 &
  server_pid=$!
  sleep 0.3
  server_pgid="$(ps -o pgid= "$server_pid" 2>/dev/null | tr -d ' ' || true)"
  echo "[$(ts)] PID=$server_pid PGID=${server_pgid:-?}" | tee -a "$LOGFILE"
}

# encerra via wineserver -k (Proton/Wine), com timeout; depois TERM/KILL no grupo
stop_server() {
  echo "[$(ts)] Parando SCUM..." | tee -a "$LOGFILE"

    # 0) tenta SIGINT no grupo (equivalente a Ctrl-C)
  if [[ -n "${server_pgid:-}" ]] && ps -p "$server_pid" >/dev/null 2>&1; then
    echo "[$(ts)] kill -INT -$server_pgid (Ctrl-C equivalente)" | tee -a "$LOGFILE"
    kill -INT "-$server_pgid" 2>/dev/null || true
    for _ in $(seq 1 $SIGINT_TIMEOUT); do
      ps -p "$server_pid" >/dev/null 2>&1 || { echo "[$(ts)] Encerrado via SIGINT."; return 0; }
      sleep 1
    done
  fi

  # 1) tenta limpar pelo wineserver (se achamos o bin)
  if [[ -n "${WINESERVER:-}" && -x "$WINESERVER" ]]; then
    echo "[$(ts)] wineserver -k (prefix=$WINEPREFIX)" | tee -a "$LOGFILE"
    WINEPREFIX="$WINEPREFIX" "$WINESERVER" -k || true

    # espera até 30s o fim do wineserver/clients
    for _ in $(seq 1 $SIGINT_TIMEOUT); do
      # quando todos processos wine do prefix saem, o wineserver também cai
      if ! pgrep -u "$USER" -f "$WINEPREFIX" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi

  # 2) ainda vivo? TERM no grupo
  if [[ -n "${server_pgid:-}" ]] && ps -p "$server_pid" >/dev/null 2>&1; then
    echo "[$(ts)] kill -TERM -$server_pgid" | tee -a "$LOGFILE"
    kill -TERM "-$server_pgid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      ps -p "$server_pid" >/dev/null 2>&1 || break
      sleep 1
    done
  fi

  # 3) último recurso: KILL no grupo + pkill defensivo
  if ps -p "$server_pid" >/dev/null 2>&1; then
    echo "[$(ts)] kill -KILL -$server_pgid (forçando)" | tee -a "$LOGFILE"
    kill -KILL "-$server_pgid" 2>/dev/null || true
    sleep 1
  fi

  # pkill por segurança (processos wine atrelados ao prefix/SCUM)
  pkill -u "$USER" -f "SCUMServer.*\.exe" 2>/dev/null || true
  pkill -u "$USER" -f "$WINEPREFIX" 2>/dev/null || true
  pkill -u "$USER" -f "wineserver.*$APPID" 2>/dev/null || true

  echo "[$(ts)] Parada solicitada." | tee -a "$LOGFILE"
}

next_restart_epoch() {
  local now next=0 candidate
  now="$(date +%s)"
  for t in "${SCHEDULE[@]}"; do
    candidate="$(date -d "today $t" +%s)"
    (( candidate <= now )) && candidate="$(date -d "tomorrow $t" +%s)"
    (( next == 0 || candidate < next )) && next="$candidate"
  done
  echo "$next"
}

wait_until_or_exit() {
  local target="$1"
  while :; do
    # se foi pedido stop (Ctrl+C) sai da espera
    (( stop_requested == 1 )) && return 0
    # se o servidor caiu, sai pra reiniciar
    ps -p "$server_pid" >/dev/null 2>&1 || { echo "[$(ts)] Servidor saiu antes do horário; reiniciando..." | tee -a "$LOGFILE"; return 0; }
    (( $(date +%s) >= target )) && return 0
    sleep 5
  done
}

# sinais de parada
trap 'stop_requested=1; echo "[$(ts)] Sinal recebido, encerrando..."; stop_server; exit 0' INT TERM

# loop principal
while :; do
  start_server
  next_ts="$(next_restart_epoch)"
  echo "[$(ts)] Próximo restart: $(date -d "@$next_ts")" | tee -a "$LOGFILE"
  wait_until_or_exit "$next_ts"
  # se não foi pedido stop manual, e ainda tá rodando, faz o restart agendado
  if (( stop_requested == 0 )) && ps -p "$server_pid" >/dev/null 2>&1; then
    stop_server
    # volta ao loop para iniciar novamente
  else
    exit 0
  fi
done
