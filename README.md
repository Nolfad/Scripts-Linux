# Scripts-Linux-Kubuntu 24.04
# Scripts-Linux para facilitar a vida
# Scripts-Linux use com moderação e cuidado

## Carteira KDE com login automático

Depois de formatar o PC e entrar no **KDE Plasma**, você pode baixar só o script. Execute em um terminal da sua conta:

```bash
curl -fL https://raw.githubusercontent.com/Nolfad/Scripts-Linux/main/carteira-kde-tpm.py -o carteira-kde-tpm.py
python3 carteira-kde-tpm.py
```

- **Instalação limpa:** o script detecta o KWallet do Plasma 5 ou 6, instala pelo APT as dependências ausentes e, se ainda não houver carteira, pede que você escolha e confirme uma senha para a nova `kdewallet`. Pode pedir autorização administrativa durante a instalação.
- **Carteira já existente:** use a senha atual dela para fazer o cadastro. Se o desbloqueio já funciona, o script sai sem pedir senha.
- **Reset da BIOS:** se o TPM foi desativado, reative-o. Se foi limpo, rode o mesmo script para cadastrar de novo. Para forçar um novo cadastro, use `--recadastrar`. Para reparar os serviços com a chave existente, use `--reparar`.

A configuração é para TUXEDO OS, Kubuntu ou Debian e derivados com KDE Plasma 5/6, sessão gráfica gerenciada pelo systemd **252 ou superior**, TPM 2.0 habilitado, Python 3 do sistema e a carteira padrão do KDE em formato compatível com PAM (Blowfish). O APT precisa de acesso aos repositórios quando faltarem pacotes. Se você também quer entrar direto na área de trabalho após formatar, habilite o login automático nas configurações do KDE/SDDM.

O serviço abre a carteira no login usando uma chave derivada da senha, protegida pelo TPM e por uma chave da instalação. Alternar o Secure Boot não invalida o cadastro. Quem entra na sua sessão automática tem acesso à carteira aberta. Uma nova carteira começa vazia; para conservar senhas antigas após formatar, restaure antes a sua `kdewallet` e o `kdewallet.salt` correspondentes.
