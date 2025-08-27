#!/usr/bin/env bash
set -euo pipefail

# ===== parâmetros que você pediu =====
ZRAM_PERCENT=200         # 200% de 32 GB => ~64 GB de zram
ZRAM_PRIORITY=150        # zram deve ser usada antes de qualquer swap de disco
DISK_SWAP_PRI=-5         # swap de disco fica por último
SWAPPINESS=10            # baixa agressividade (só troca quando memória bem cheia)
# ====================================

echo "== 1) Instalação do zram-tools =="
sudo apt update
sudo apt install -y zram-tools

echo "== 2) Config do /etc/default/zramswap =="
sudo install -m 0644 -D /dev/null /etc/default/zramswap
sudo tee /etc/default/zramswap >/dev/null <<EOC
# zram-tools config
ALGO=zstd
PERCENT=${ZRAM_PERCENT}
PRIORITY=${ZRAM_PRIORITY}
# ZRAM_NUM_DEVICES pode ficar padrão (1)
EOC

echo "== 3) Ativando/reativando zramswap =="
sudo systemctl enable --now zramswap.service
# forçar reconfig sem reboot
sudo systemctl restart zramswap.service

echo "== 4) Ajuste do kernel: swappiness e page-cluster =="
sudo tee /etc/sysctl.d/99-zram-tuning.conf >/dev/null <<EOT
vm.swappiness=${SWAPPINESS}
vm.page-cluster=0
EOT
sudo sysctl --system >/dev/null

echo "== 5) Garantir swap de DISCO com prioridade menor =="
# Descobre swap(s) de disco em uso (partition/file) e aplica prioridade baixa
# - Reativa cada uma com 'swapon -p DISK_SWAP_PRI'
mapfile -t DISK_SWAPS < <(swapon --noheadings --show=NAME,TYPE | awk '$2=="partition" || $2=="file" {print $1}')
if [ ${#DISK_SWAPS[@]} -gt 0 ]; then
  echo "   Encontrado(s) swap de disco: ${DISK_SWAPS[*]}"
  for dev in "${DISK_SWAPS[@]}"; do
    echo "   -> Reaplicando prioridade ${DISK_SWAP_PRI} em ${dev}"
    sudo swapoff "${dev}" || true
    sudo swapon -p ${DISK_SWAP_PRI} "${dev}"
  done
else
  echo "   (Nenhuma swap de disco ativa encontrada. Tudo bem.)"
fi

# opcional: marcar prioridade no fstab do swapfile, se existir
if grep -qE '^\s*/swapfile\s' /etc/fstab; then
  echo "== 6) Ajustando pri no /etc/fstab para /swapfile (${DISK_SWAP_PRI}) =="
  sudo cp /etc/fstab "/etc/fstab.bak.$(date +%F-%H%M%S)"
  # adiciona/atualiza 'pri=' nas opções do swapfile
  sudo awk -v pri="pri=${DISK_SWAP_PRI}" '
    $1=="/swapfile" && $3=="swap" {
      # se já tem pri=, substitui; se não, adiciona antes de 0 0
      if ($0 ~ /pri=[^[:space:]]+/) gsub(/pri=[^[:space:]]+/, pri);
      else sub(/[[:space:]]0[[:space:]]0$/, " " pri " 0 0");
    }
    {print}
  ' /etc/fstab | sudo tee /etc/fstab.tmp >/dev/null
  sudo mv /etc/fstab.tmp /etc/fstab
fi

echo "== 7) Status final =="
echo "-- swapon --show=NAME,PRIO,TYPE,SIZE,USED --"
swapon --show=NAME,PRIO,TYPE,SIZE,USED
echo
echo "-- zramctl --"
command -v zramctl >/dev/null && zramctl || echo "(zramctl indisponível)"
echo
echo "Concluído:"
echo " - ZRAM: ${ZRAM_PERCENT}% da RAM (alvo ~64 GB) | prioridade ${ZRAM_PRIORITY}"
echo " - Swap de disco: prioridade ${DISK_SWAP_PRI} (usada por último)"
echo " - swappiness=${SWAPPINESS} (swap só quando a RAM estiver bem cheia)"
