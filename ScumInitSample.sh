#!/bin/bash

cd /mnt/m2/SteamLibrary/steamapps/common/SCUM Server/SCUM/Binaries/Win64/

# Define o prefixo do Wine/Proton, localizado na sua SteamLibrary principal
export WINEPREFIX="/mnt/m2/SteamLibrary/steamapps/compatdata/3792580/pfx"

# Define o steam compat data do Wine/Proton, localizado na sua SteamLibrary principal
export STEAM_COMPAT_DATA_PATH="/mnt/m2/SteamLibrary/steamapps/compatdata/3792580/"

export STEAM_COMPAT_CLIENT_INSTALL_PATH="/home/nolfad/.local/share/Steam/"


# Caminho completo para o executável do servidor SCUM na sua SteamLibrary principal
SERVER_EXE="/mnt/m2/SteamLibrary/steamapps/common/SCUM Server/SCUM/Binaries/Win64/SCUMServer.exe"

# Caminho EXATO para a versão do Proton que você deseja usar (Proton 9.0 (Beta))
# Observe que a pasta é "Proton 9.0 (Beta)", e o executável é "proton" dentro dela.
PROTON_PATH="/home/nolfad/.local/share/Steam/compatibilitytools.d/GE-Proton10-9/"

# Comando para iniciar o servidor
# Mantenha -nobattleye para evitar problemas de autenticação comuns em Linux/Proton.
# As portas são 7000 (principal) e 7777 (query), então a porta para conectar no jogo é 7002.
"$PROTON_PATH/proton" run "$SERVER_EXE" -log -multihome=0.0.0.0 -nobattleye

# Comando para iniciar o servidor com wine diretamente
#wine "$SERVER_EXE" -log -nobattleye
