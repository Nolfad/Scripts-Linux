#!/usr/bin/env bash
set -Eeuo pipefail

if (( $# > 0 )); then
  COUNTRIES=("$@")
else
  COUNTRIES=(cn ru kr ua in)
fi

V4_URL_BASE="https://www.ipdeny.com/ipblocks/data/countries"
V6_URL_BASE="https://www.ipdeny.com/ipv6/ipaddresses/blocks"

# Garante tabela/sets
sudo nft list table inet filter >/dev/null 2>&1 || sudo nft add table inet filter
sudo nft list set inet filter geo_block4 >/dev/null 2>&1 || sudo nft add set inet filter geo_block4 '{ type ipv4_addr; flags interval; }'
sudo nft list set inet filter geo_block6 >/dev/null 2>&1 || sudo nft add set inet filter geo_block6 '{ type ipv6_addr; flags interval; }'

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "🔄 Atualizando blocklists para países: ${COUNTRIES[*]}"

v4_agg="$tmpdir/v4.all"
v6_agg="$tmpdir/v6.all"
: >"$v4_agg"; : >"$v6_agg"

fetch_ok() { curl -fsSL --retry 3 --retry-delay 1 -o "$2" "$1"; }

for cc in "${COUNTRIES[@]}"; do
  echo "➡  Baixando blocos para $cc…"

  v4f="$tmpdir/${cc}-v4.zone"
  v6f="$tmpdir/${cc}-v6.zone"

  # IPv4
  if fetch_ok "$V4_URL_BASE/${cc}.zone" "$v4f"; then
    if head -n1 "$v4f" >/dev/null; then
      # limpa CR, valida e agrega
      tr -d '\r' < "$v4f" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+' >> "$v4_agg" || true
    fi
  else
    echo "   ⚠️  Falha ao baixar IPv4 de $cc"
  fi

  # IPv6
  if fetch_ok "$V6_URL_BASE/${cc}.zone" "$v6f"; then
    if head -n1 "$v6f" >/dev/null; then
      tr -d '\r' < "$v6f" | grep -E '^[0-9A-Fa-f:]+/[0-9]+' >> "$v6_agg" || true
    fi
  else
    echo "   ⚠️  Falha ao baixar IPv6 de $cc"
  fi
done

# Ordena/unique e mostra contagens
sort -u -o "$v4_agg" "$v4_agg" || true
sort -u -o "$v6_agg" "$v6_agg" || true

v4_count=$(wc -l < "$v4_agg" || echo 0)
v6_count=$(wc -l < "$v6_agg" || echo 0)
echo "ℹ️  Total IPv4 agregados: $v4_count"
echo "ℹ️  Total IPv6 agregados: $v6_count"

flush_and_load() {
  local family_set="$1" file="$2" chunk=800
  local count=0 line
  local -a buf=()

  sudo nft "flush set $family_set" || true

  [[ -s "$file" ]] || { echo "   (sem dados para $family_set)"; return 0; }

  # carrega em blocos
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    buf+=("$line"); ((count++))
    if (( count >= chunk )); then
      printf 'add element %s { %s }\n' "$family_set" "$(IFS=,; echo "${buf[*]}")" | sudo nft -f -
      buf=(); count=0
    fi
  done < "$file"

  if (( count > 0 )); then
    printf 'add element %s { %s }\n' "$family_set" "$(IFS=,; echo "${buf[*]}")" | sudo nft -f -
  fi

  # confere quantos elementos ficaram no set
  local after
  after=$(sudo nft list set $family_set | sed -n '/elements = {/,/}/p' | grep -vc 'elements = {' || true)
  echo "✅ Carregados $after elementos em $family_set"
}

flush_and_load "inet filter geo_block4" "$v4_agg"
flush_and_load "inet filter geo_block6" "$v6_agg"

# Falha se nada foi carregado (para não “fingir sucesso”)
if (( v4_count == 0 )) && (( v6_count == 0 )); then
  echo "❌ Nenhuma CIDR foi baixada. Verifique conectividade/URLs."
  exit 1
fi

echo "✅ Geo-block atualizado."
