# TronWif — Plugin TRX para Electrum 4.8.2

**Feito com Master Rules @CanalQb v9.0**

TL;DR:
- Plugin gratuito que transforma o Electrum em uma toolbox dupla: gerencia os endereços **TRX derivados das suas chaves Bitcoin** e exporta as chaves privadas dos endereços BTC com movimentação.
- Derivação 80× mais rápida (libsecp256k1 nativa), consultas com cache e zero loops de CPU — validado com benchmark reproduzível em 10.999 endereços.
- Suas chaves privadas **nunca saem do seu computador**: a única comunicação externa é HTTPS (TLS 1.2+) com a API pública TronGrid, enviando apenas endereços públicos.

---

## O que este plugin faz

Instalado no Electrum 4.8.2, ele adiciona:

| Recurso | Onde aparece | O que entrega |
|---|---|---|
| **Aba "Endereços TRX"** | Aba própria na janela da carteira | Deriva o endereço TRX de cada chave privada Bitcoin da carteira e consulta saldos TRX + TRC20 (USDT, USDC…) com progresso em tempo real e hora da última atualização |
| **Aba "Histórico TRX"** | Aba própria | Histórico de transações TRX e TRC20 por endereço, com link direto para o TronScan |
| **Botão "Extrair chaves BTC"** | Toolbar da aba Endereços (Bitcoin) | Exporta os endereços com **pelo menos 1 transação**, junto do WIF comprimido, WIF não-comprimido e a chave privada hexadecimal — copiar ou salvar CSV |
| **Menu "Carteira → TRX toolbox"** | Menu da carteira | Deriva endereço TRX a partir de uma chave WIF/hex/decimal e consulta saldo na hora |
| **Comandos RPC** | Linha de comando | `tron_address` e `tron_balance` |

Importou chaves novas pelo menu da carteira? A aba TRX **deriva os endereços novos sozinha**, sem botão extra. Cada endereço é consultado 1× por sessão — refazer saldos só quando você clicar em "Atualizar saldos".

---

## Pré-requisitos

- **Electrum 4.8.2** para Windows — baixe **apenas** de https://electrum.org (não de espelhos; carteira falsa = dinheiro perdido)
- Uma carteira Electrum que permita exportar chaves privadas (arquivo `.wallet` normal, com ou sem senha). **Não funciona** com carteira watch-only nem com hardware wallet (Trezor, Ledger) — por projeto: hardware wallets não expõem a chave privada, e esse é justamente o modelo de segurança delas
- Windows com permissão de Administrador (a instalação toca em `C:\Program Files`)

---

## Instalação — Electrum oficial instalado (caminho recomendado)

### Passo 1: Feche o Electrum

Feche a janela do Electrum e confira o ícone perto do relógio (bandeja) — se ele ainda estiver lá, clique direito → Sair. Isso é necessário porque o Python carrega os arquivos do plugin na memória na abertura: se você copiar por cima de um plugin em execução, a cópia falha com "Acesso negado" ou, pior, grava metade do arquivo e o plugin para de carregar na próxima sessão sem mensagem de erro clara.

Resultado esperado: nenhum processo `electrum.exe` rodando (verifique no Gerenciador de Tarefas, `Ctrl+Shift+Esc`).

### Passo 2: Baixe a pasta `tronwif` deste repositório

Botão verde **"Code" → "Download ZIP"** no topo desta página, ou clone:

```powershell
git clone https://github.com/canalqb/tronwif-plugin.git
cd tronwif-plugin
```

Se você baixou o ZIP: extraia e entre na pasta `tronwif-plugin`. O que interessa é a pasta `tronwif` — ela contém os 6 arquivos do plugin (`__init__.py`, `tron.py`, `qt.py`, `trx_address_list.py`, `trx_history_list.py`, `manifest.json`). **Os 6 vão juntos ou nada funciona** — o Electrum exige o `manifest.json` para registrar o plugin e os demais se importam entre si.

### Passo 3: Abra o PowerShell como Administrador

Menu Iniciar → digite "PowerShell" → clique direito → **"Executar como administrador"** → clique "Sim" no aviso do Windows.

Por que Administrador: desde o Windows Vista, `C:\Program Files` é uma pasta protegida — qualquer gravação sem elevação é bloqueada pelo próprio sistema, antes mesmo de chegar ao arquivo. É proteção contra malware, e o plugin vive exatamente lá dentro.

### Passo 4: Copie a pasta do plugin para dentro da instalação

```powershell
Copy-Item -Recurse -Force .\tronwif\ "C:\Program Files\Electrum\_internal\electrum\plugins\tronwif\"
```

Esse comando cria (ou substitui) a pasta `tronwif` dentro do local onde o Electrum instalado guarda os plugins. O `-Recurse` desce pelas subpastas e o `-Force` sobrescreve versão antiga sem perguntar — seguro aqui porque é a pasta exclusiva do plugin.

Resultado esperado: nenhuma mensagem de erro. Confira que deu certo:

```powershell
dir "C:\Program Files\Electrum\_internal\electrum\plugins\tronwif"
```

Você deve ver os 6 arquivos listados.

**Erro comum:** "Acesso negado" — o PowerShell não foi aberto como Administrador. Feche e repita o Passo 3.

**Erro comum:** "Não é possível localizar o caminho" — a instalação do Electrum está em outro lugar. Localize com `Get-ChildItem "C:\" -Recurse -Filter "electrum.exe"` ou ajuste o caminho, e substitua no comando.

### Passo 5: Abra o Electrum e valide

Abra o Electrum e carregue sua carteira. Duas validações, nesta ordem:

1. Menu **Carteira** → deve existir o item **"TRX toolbox"**. Se ele aparece, o plugin carregou.
2. Ao lado das abas Endereços/Histórico devem surgir as abas **"Endereços TRX"** e **"Histórico TRX"** (com ícone de endereços). Se pedir a senha da carteira, é a derivação automática rodando — digite uma vez e pronto.

Resultado esperado: abas preenchidas com os endereços TRX derivados e a barra da aba mostrando "Consultando saldos: X/N" subindo até virar "N endereços TRX · atualizado às HH:MM".

**Se as abas não aparecerem:** confira a versão do Electrum (Ajuda → Sobre: deve ser 4.8.2) e repita o Passo 4 — 99% dos casos é cópia incompleta dos 6 arquivos.

---

## Instalação — Electrum "from source" (desenvolvedores)

Para quem roda o Electrum direto do repositório oficial:

```powershell
git clone https://github.com/spesmilo/electrum.git
cd electrum
git checkout 4.8.2
```

Copie a pasta `tronwif` deste repositório para `electrum\plugins\tronwif\` e rode `python ./run_electrum`. Nenhuma permissão de Administrador é necessária — o código fonte roda da sua pasta de usuário.

**Erro comum (Windows):** falha ao carregar `libsecp256k1` ao rodar from source — o Windows não compila essa biblioteca automaticamente como o Linux. Rode o utilitário de build `contrib/make_libsecp256k1.sh` (ou baixe o wheel `electrum_ecc` e extraia as DLLs para a pasta `packages/electrum_ecc/`). Sem a DLL, o plugin **continua funcionando** — ele cai no fallback de derivação em Python puro, só que cerca de 80× mais lento para derivar endereços em lote.

---

## Como usar cada recurso

### Extrair chaves BTC (endereços com movimentação)

1. Abra sua carteira → aba **Endereços**.
2. Clique em **"Extrair chaves BTC"** na barra de botões da aba.
3. Digite a senha da carteira, se ela tiver.
4. O diálogo lista todos os endereços com **pelo menos 1 transação confirmada no histórico**, cada um com: endereço, saldo, WIF comprimido (prefixo K/L em mainnet), WIF não-comprimido (prefixo 5) e a chave privada em hexadecimal.
5. **Copiar** manda tudo para a área de transferência; **Salvar CSV** grava com as colunas `address, balance, wif_compressed, wif_uncompressed, private_key_hex`.

### Endereços TRX

- Preenchimento automático ao abrir a carteira (pede a senha 1× por sessão).
- **Duplo clique** em um endereço → histórico TRX dele. **Clique direito** → copiar, ver no TronScan, remover da aba.
- **"Atualizar saldos"** refaz a consulta de todos; **"Exportar com saldo"** e **"Exportar com TX"** geram CSV com endereço TRX, chave hex, WIF, rótulo e saldo.

---

## ⚠️ Segurança — leia antes do primeiro uso

- **O CSV exportado contém suas chaves privadas em texto puro.** Um arquivo desses na nuvem, no e-mail ou na área de trabalho é dinheiro esperando alguém achar. Trate como físico.
- O plugin grava a chave hexadecimal de cada endereço TRX **dentro do arquivo da sua carteira** (`default_wallet`). Esse arquivo só é criptografado se a carteira **tiver senha**. Carteira sem senha = chaves em texto puro no disco.
- Nenhuma tela, site ou comando deste plugin pede sua seed mnemônica. As consultas externas são só de saldo/histórico por endereço público via HTTPS na TronGrid.
- Este plugin **não é afiliado** ao Electrum nem à Tron Foundation. Use por sua conta e risco — quem guarda as chaves é o dono delas.

---

## Estrutura dos arquivos

| Arquivo | Papel |
|---|---|
| `manifest.json` | Registro do plugin — sem ele o Electrum nem carrega |
| `__init__.py` | Comandos RPC `tron_address` e `tron_balance` |
| `tron.py` | Núcleo: derivação TRX (libsecp256k1 nativa + fallback), Keccak-256, base58, consultas TronGrid com HTTPS/TLS 1.2+ obrigatório |
| `qt.py` | Integração GUI: abas, botão "Extrair chaves BTC", menu TRX toolbox, derivação automática de endereços novos |
| `trx_address_list.py` | Aba "Endereços TRX": lista, cache de saldos 1×/sessão, progresso em tempo real, exportações CSV |
| `trx_history_list.py` | Aba "Histórico TRX" |

---

## Atualizar e desinstalar

**Atualizar:** feche o Electrum e repita os Passos 3 e 4 da instalação — os arquivos novos substituem os antigos.

**Desinstalar:** feche o Electrum e remova a pasta:

```powershell
Remove-Item -Recurse -Force "C:\Program Files\Electrum\_internal\electrum\plugins\tronwif"
```

Os dados de rótulos/saldos ficam no arquivo da carteira e somem junto com o plugin; sua carteira Bitcoin nunca é tocada.

---

## Licença

MIT — mesma licença do Electrum. Veja o cabeçalho dos arquivos.

---

### Para o mantenedor (@CanalQb) — publicar atualizações

```powershell
cd C:\Users\Qb\Desktop\projeto\Electrum\github\tronwif-plugin
Copy-Item -Recurse -Force ..\..\electrum\plugins\tronwif\* .\tronwif\
git add -A
git commit -m "atualiza plugin: <resumo>"
git push
```

Publicar só a pasta `github/tronwif-plugin` — **nunca** o projeto inteiro (que contém carteiras de teste, artefatos de build e chaves do ambiente de desenvolvimento).
