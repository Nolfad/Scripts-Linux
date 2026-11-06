# Scripts-Linux-Kubuntu 24.04
# Scripts-Linux para facilitar a vida
# Scripts-Linux use com moderação e cuidado

## Carteira KDE com login automático

No TUXEDO OS ou Kubuntu com Plasma 6, `ksecretd` e TPM 2.0, execute em um terminal do KDE, com seu usuário:

```bash
./carteira-kde-tpm.py
```

Se o desbloqueio já funciona, o script apenas confirma e sai. No primeiro cadastro, ele pede autorização administrativa e a senha atual da carteira no próprio terminal. A senha da conta e a da carteira continuam iguais às que você escolheu. A chave derivada fica protegida pelo TPM e pelo segredo local do sistema. Quem entra na sua sessão automática tem acesso à carteira aberta.

A chave não depende do estado do Secure Boot. Se a BIOS desativar o TPM, reative-o antes de rodar o script. Se o TPM for limpo, rode o mesmo comando para cadastrar novamente. Para forçar um cadastro, use `--recadastrar`. Para reinstalar os serviços reaproveitando um cadastro válido, use `--reparar`.
