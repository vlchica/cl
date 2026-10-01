# Assistente de voz local em português

Um assistente que roda **no seu computador, offline**, e conversa por voz em português do Brasil, no espírito do modo de voz avançado do ChatGPT:

- **conversa contínua:** o microfone fica aberto, com detecção de voz (Silero VAD) e fim de fala automático;
- **interrupção:** fale por cima e ele para de falar na hora;
- **fila em segundo plano:** enquanto ele pesquisa, gera uma imagem ou monta um documento, você continua falando e ele anota os pedidos e responde em seguida;
- **ferramentas:** busca na web, leitura de arquivos e páginas, geração de imagens (Stable Diffusion) e criação de documentos em PDF, Word, TXT e Markdown;
- **uma interface só:** o [Open WebUI](https://github.com/open-webui/open-webui) com tudo conectado (LLM, Whisper, síntese de voz, imagens, busca e ferramentas), mais uma tela dedicada ao modo de voz avançado (o link aparece num aviso dentro do Open WebUI).

A instalação é um comando só. Ela levanta o hardware, escolhe os modelos que cabem, instala drivers e dependências, sobe tudo em Docker, configura o início automático e faz um teste real de ponta a ponta.

> **Onde isto foi feito.** O pacote foi escrito numa sessão na nuvem, **sem acesso ao seu computador**. Por isso a instalação de verdade, o teste com seus modelos e a medição de VRAM acontecem quando você roda o instalador. Ele grava tudo em `relatorio-instalacao.md`. A seção [O que já foi testado](#o-que-já-foi-testado) explica o que foi validado antes da entrega.

---

## Instalação

**Linux (Ubuntu, Debian, Mint, Pop!_OS; Fedora e Arch em modo de melhor esforço) ou WSL2:**

```bash
git clone https://github.com/vlchica/cl.git assistente && cd assistente
./instalar.sh
```

**Windows 10/11 (PowerShell):**

```powershell
git clone https://github.com/vlchica/cl.git assistente; cd assistente
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
```

O instalador pede permissão de administrador uma vez e depois segue sozinho:

| Etapa | Linux | Windows |
|---|---|---|
| Driver NVIDIA | instala/atualiza (`ubuntu-drivers`, `nvidia-driver`, RPM Fusion ou `nvidia`); se precisar reiniciar, reinicia em 2 min e **continua sozinho** | usa o driver do Windows (tenta instalar o NVIDIA App se faltar) |
| Docker | Docker Engine + Compose (get.docker.com) | Docker Desktop com WSL2 (reinicia e continua sozinho se o WSL2 for novo) |
| GPU no Docker | NVIDIA Container Toolkit + `nvidia-ctk` | nativo do Docker Desktop |
| CUDA | **não instala CUDA no sistema**: cada contêiner traz o seu (PyTorch cu126/cu128, Ollama, llama.cpp). Só o driver importa | idem |
| Python | do sistema | Python 3.12 via winget |
| Resto | `scripts/instalar.py` (igual nos dois sistemas) | idem |

Opções: `--forcar-omni` (usa o Qwen3-Omni mesmo sem VRAM suficiente, com parte na RAM), `--forcar-cpu`, `--sem-imagens`, `--pular-teste`, `--sem-autostart` (no Windows: `-ForcarOmni`, `-ForcarCpu` etc.).

Rodar o instalador de novo é seguro: ele reaproveita o que já foi baixado e corrige o que mudou.

**Espaço em disco:** ~45 GB no mínimo (modo CPU) e ~80 GB no perfil completo (Qwen3-Omni + SDXL). O detector confere antes e rebaixa os modelos se faltar espaço.

---

## Endereços de acesso

Ao final o instalador mostra os endereços reais (as portas mudam sozinhas se já estiverem ocupadas) e grava tudo em `relatorio-instalacao.md`. Os padrões são:

| O quê | Neste computador | Pela rede (celular, outro PC) |
|---|---|---|
| **Open WebUI** (interface principal) | http://localhost:3000 | https://IP-DO-PC:3443 |
| **Modo de voz avançado** | http://localhost:3001 | https://IP-DO-PC:3444 |
| Downloads de documentos | http://localhost:3002/arquivos/… | http://IP-DO-PC:3002/arquivos/… |
| ComfyUI (opcional, avançado) | http://localhost:8188 | só local |

- **Login:** `admin@assistente.local`; a senha é gerada na instalação e fica no `.env` (`ADMIN_SENHA`). O cadastro de novos usuários fica desligado.
- **Microfone em outro aparelho:** o navegador só libera o microfone em `https://` ou em `localhost`. Pela rede, use os endereços `https://` (certificado próprio: aceite o aviso do navegador uma vez).

---

## Como usar

### No Open WebUI

- **Texto:** é um chat normal. O modelo decide sozinho quando pesquisar na web, gerar imagem ou criar documento (chamada de função nativa). Os botões de busca e imagem já vêm ligados.
- **Arquivos:** arraste PDF, DOCX, TXT, planilhas etc. para o chat. A leitura usa embeddings multilíngues (bons para português).
- **Voz:** o botão redondo ao lado do campo de texto ("Modo de voz") abre a **chamada**. Ela já vem em português, com envio automático e **interrupção por voz ligada**. Limites da chamada do Open WebUI: ela detecta o fim da fala por volume, com 2 s fixos de silêncio, e interromper cancela a resposta em andamento. Para conversa contínua com VAD de verdade (0,7 s), tarefas em segundo plano e fila de pedidos, use o modo de voz avançado.
- **Idioma da interface:** segue o idioma do navegador (português num navegador em português). Para fixar: Configurações › Geral › Idioma.
- **Ditado:** o microfone do campo de texto transcreve com o Whisper.

### No modo de voz avançado (http://localhost:3001)

1. Toque no círculo para abrir o microfone. A partir daí é só falar.
2. Quando você para de falar (silêncio de 0,7 s, ajustável), ele transcreve e responde, falando frase a frase enquanto ainda gera o texto.
3. **Para interromper**, fale por cima ou toque no círculo: o áudio corta na hora.
   - Se ele só estava falando, abandona a resposta e atende o que você disse.
   - Se estava fazendo uma tarefa (busca, imagem, documento), a tarefa continua em segundo plano. O que você disser entra na fila (aparece na tela) e é atendido em seguida. Pedidos acumulados vão juntos.
   - Uma tosse ou ruído não derruba a resposta: se a transcrição vier vazia, ele retoma de onde estava.
4. Diga **"para"**, **"cancela"** ou **"esquece"** para cancelar tudo.
5. 📎 envia um arquivo para ele ler e responder sobre o conteúdo. ⚙️ muda a voz (Dora, Alex, Santa ou Faber), a velocidade e o tempo de silêncio.
6. **Fone de ouvido** deixa a interrupção mais sensível. Na caixa de som, ele exige uma fala mais clara enquanto está falando, para não se interromper com o próprio eco.

Exemplos: *"Pesquise a previsão do tempo para amanhã em Curitiba"*, *"Gere uma imagem de um farol ao pôr do sol"* (e, enquanto ela é gerada, *"e me lembra que dia é hoje"*), *"Crie um PDF com uma lista de compras para um churrasco"*.

---

## Escolha automática dos modelos

`scripts/detectar_hardware.py` lê o sistema operacional, cada GPU (modelo, VRAM, compute capability, processos ocupando memória), a RAM e o disco livre, e escolhe:

| Hardware (VRAM útil para o LLM) | LLM principal | Reserva | Imagens | Observações |
|---|---|---|---|---|
| ≥ 21 GB (RTX 3090/4090/5090, ou várias placas somadas) | **Qwen3-Omni 30B-A3B Q4_K_M** | Qwen3 14B | SDXL 1.0 | Com 24 GB, o LLM sai da VRAM durante cada imagem e volta sozinho depois |
| 11–21 GB (RTX 4060 Ti 16 GB, 4080, 3080 Ti) | Qwen3 14B | Qwen3 8B | SDXL | **Qwen3-Omni não cabe**; o instalador avisa |
| 7–11 GB (RTX 3060 12 GB, 3070, 2080) | Qwen3 8B | Qwen3 4B | SDXL ou SD 1.5 | |
| 4–7 GB (GTX 1060/1660, RTX 3050) | Qwen3 4B | Qwen3 1.7B | SD 1.5 | Pascal usa Whisper em int8 |
| sem GPU NVIDIA | Qwen3 4B (CPU) | Qwen3 1.7B | SD 1.5 na CPU (1–3 min/imagem) | Funciona, mas lento |

- **Várias GPUs** (por exemplo, uma rig de mineração): o LLM é dividido entre as placas maiores e a voz e as imagens ficam numa placa separada. Se um minerador ou jogo estiver ocupando VRAM, o relatório avisa.
- **RTX 50 (Blackwell)** usa PyTorch cu128; as demais usam cu126, que funciona da GTX 900 à RTX 40.
- **Pouco disco:** rebaixa primeiro o SD (SDXL → 1.5), depois a reserva e só então o modelo principal.
- O resultado fica em `relatorio-hardware.md`, com avisos claros do que **não coube**.

### Sobre o Qwen3-Omni

- O arquivo usado é o GGUF oficial de 4 bits do ggml-org: `ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF:Q4_K_M` (18,6 GB).
- **O Ollama ainda não suporta oficialmente o Qwen3-Omni** (o pedido segue aberto, [ollama/ollama#12376](https://github.com/ollama/ollama/issues/12376), e o Ollama 0.35 não traz essa arquitetura). O instalador tenta pelo Ollama primeiro. Se o modelo não carregar, ele usa **o mesmo arquivo no llama.cpp server** (versão b11277, que tem suporte oficial), sem baixar de novo, e conecta o Open WebUI a ele. Se nem assim funcionar, a reserva assume e o relatório explica o motivo.
- A entrada de áudio e a fala nativas do Omni não são usadas: o Ollama e o llama.cpp não as expõem de forma estável. A voz passa pelo Whisper (ouvir) e pelo Kokoro (falar), que entregam latência menor e português melhor. O Omni entra como "cérebro": é um MoE com 3B parâmetros ativos, rápido para conversa.

---

## O que é instalado

| Componente | Versão | Função | Alternativa automática se falhar |
|---|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui) | 0.11.4 | Interface única (chat, chamada de voz, arquivos, imagens) | — |
| [Ollama](https://ollama.com) | 0.35.0 | Motor do LLM | llama.cpp server b11277 (mesmo GGUF) |
| Qwen3-Omni 30B-A3B Instruct | Q4_K_M | LLM principal (quando cabe) | Qwen3 14B/8B/4B, conforme a VRAM |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 1.2.1, modelo `large-v3-turbo` | Reconhecimento de voz em português, VAD de fim de fala, filtro de "alucinações" no silêncio | float16 → CPU int8 → modelo `small` |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | 0.9.4 | Síntese de voz pt-BR (vozes `pf_dora`, `pm_alex`, `pm_santa`), < 1 s por frase | GPU → CPU → [Piper](https://github.com/OHF-Voice/piper1-gpl) 1.8 `pt_BR-faber-medium` |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | v0.38.0 | Stable Diffusion (o projeto mais ativo e estável hoje; o AUTOMATIC1111 está parado) | SDXL → SD 1.5 → imagens desativadas |
| Stable Diffusion | SDXL 1.0 base ou SD 1.5 fp16 | Geração de imagens | |
| [SearXNG](https://github.com/searxng/searxng) | 2026.9.30 | Busca na web local, sem chave de API | |
| Servidor de ferramentas (`servicos/ferramentas`) | deste projeto | PDF/DOCX/TXT/MD, busca, imagens, leitura de páginas, data e hora | |
| Modo de voz (`servicos/voz`) | deste projeto | Full duplex com VAD no navegador, interrupção e fila | Se o LLM principal falhar no meio, usa a reserva |
| Caddy | 2.11.4 | HTTPS na rede local (microfone no celular) | |
| PyTorch | 2.9.1 (cu126, cu128 ou cpu) | Base de fala e imagens | |

Tudo roda em contêineres com `restart: unless-stopped`; os modelos ficam em volumes do Docker.

---

## VRAM por componente

Valores **esperados** (o consumo real da sua máquina é medido no teste final e fica em `relatorio-instalacao.md`; veja a qualquer momento com `./assistente.sh vram`):

| Componente | VRAM |
|---|---:|
| Qwen3-Omni 30B-A3B Q4_K_M + contexto de 16k (cache q8) | ~19,5–20 GB |
| Qwen3 14B Q4_K_M (16k) | ~10,8 GB |
| Qwen3 8B Q4_K_M (16k / 8k) | ~6,6 / ~6,0 GB |
| Whisper large-v3-turbo (int8_float16) | ~1,0–1,3 GB |
| Kokoro-82M na GPU (na CPU: 0) | ~0,6 GB |
| SDXL no ComfyUI, gerando 1024×1024 | ~7–8 GB (só durante a geração) |
| SD 1.5 fp16, 512×512 | ~2,5–3 GB |
| Open WebUI, SearXNG, ferramentas, modo de voz | 0 (CPU) |

Numa placa de 24 GB, o Qwen3-Omni e o Whisper ficam carregados o tempo todo e o Kokoro roda na CPU. Para cada imagem, o serviço de ferramentas descarrega o LLM, gera a imagem, libera o ComfyUI e o LLM volta na próxima pergunta. Isso custa alguns segundos a mais por imagem.

---

## Início automático

- **Linux:** serviço systemd `assistente` (sobe com o Docker no boot e atualiza o IP da rede no `.env`).
- **Windows:** tarefa agendada "Assistente de voz" ao entrar no Windows, e Docker Desktop configurado para iniciar junto.

---

## Comandos do dia a dia

```bash
./assistente.sh status        # contêineres e VRAM
./assistente.sh logs [serviço] # ex.: logs fala, logs open-webui
./assistente.sh testar        # roda de novo o teste de ponta a ponta
./assistente.sh vram          # consumo por componente
./assistente.sh parar | iniciar
./assistente.sh reinstalar --forcar-omni
./assistente.sh desinstalar   # remove contêineres e modelos (pergunta antes)
```

No Windows, use os mesmos comandos `docker compose …` e `python scripts\…` dentro da pasta.

---

## Teste de ponta a ponta

`scripts/teste_ponta_a_ponta.py` roda no fim da instalação e grava `relatorio-teste.md`:

1. **Ouvir:** o Open WebUI pede ao Kokoro uma pergunta falada e mede a latência (meta: < 1 s);
2. **Falar:** o Whisper transcreve esse áudio de volta pelo Open WebUI;
3. **Conversa:** o LLM responde pelo Open WebUI;
4. **Voz completa:** o modo de voz recebe o áudio, transcreve, responde e começa a falar, medindo o tempo até o primeiro som;
5. **Busca:** SearXNG direto e o assistente decidindo pesquisar sozinho;
6. **Imagem:** pelo Open WebUI e pelo assistente (PNG baixado de volta);
7. **Documento:** o assistente cria um PDF; a ferramenta cria um TXT com acentos;
8. **VRAM** por componente.

---

## O que já foi testado

Esta sessão na nuvem não tinha GPU, microfone nem acesso ao Hugging Face e ao registro do Ollama, então os modelos em si não puderam rodar aqui. O que foi validado:

- **63 testes automatizados** (`pytest tests/`): escolha de modelos para vários hardwares (3090, 4060 Ti, 3060, rig com 4×3070, GTX 1060, RTX 5090, só CPU, pouco disco); limpeza de texto pt-BR para a fala (R$, %, °C, horas, abreviações, markdown) e filtro de alucinações do Whisper; API OpenAI do serviço de fala; servidor de ferramentas (PDF/DOCX/TXT/MD gerados e lidos de volta, busca, fluxo do ComfyUI liberando a VRAM do LLM, API de imagens); e o modo de voz (frases em streaming, filtro de `<think>`, fila durante tarefa em segundo plano, interrupção, comando "para", retomada após ruído, troca para o modelo de reserva).
- **Modo de voz num Chromium real** com microfone falso tocando uma fala em português: o Silero VAD carrega, detecta a fala e envia o áudio; a resposta aparece e toca; falando por cima, o áudio corta na hora, a resposta fica marcada como interrompida e a fala nova é atendida (`tests/navegador/teste_voz.mjs`).
- **Ensaio com o Open WebUI 0.11.4 de verdade** (`tests/ensaio_openwebui.py`): Open WebUI instalado com exatamente as variáveis do `docker-compose.yml`, serviços de ferramentas e de voz reais, e simuladores só no lugar dos modelos. Resultados:
  - admin criado pelas variáveis;
  - configurador idempotente (a segunda execução não muda nada);
  - prompt em português chegando ao LLM;
  - **8 de 8 etapas** do teste de ponta a ponta;
  - na interface (Chromium, `tests/navegador/teste_openwebui.mjs`): o chat chamou a ferramenta `criar_documento` sozinho, e o modo de chamada, em português, transcreveu a fala do microfone, respondeu e falou.

  O ensaio também achou e corrigiu dois problemas reais: o prompt de sistema padrão é ignorado pelo Open WebUI 0.11 sem um registro por modelo (o configurador agora cria esse registro), e a conexão de ferramentas era regravada a cada execução.
- **Configuração:** docker-compose validado com e sem GPU; Caddyfile validado; SearXNG subindo com a configuração do projeto (JSON ligado e IPv4 forçado, o que corrigiu uma queda em Docker sem IPv6).

Os tempos medidos nesses ensaios são dos simuladores, não dos modelos: a latência real da sua máquina sai no `relatorio-teste.md`.

---

## Solução de problemas

| Sintoma | O que fazer |
|---|---|
| "O Docker não acessou a GPU" | Reinicie o computador e rode o instalador de novo. No Linux: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| Respostas lentas, LLM "dividido com a RAM" | Outro programa está usando a VRAM (minerador, jogo, outro Ollama). Pause-o ou rode `./assistente.sh reinstalar` para escolher modelos menores |
| O microfone não abre no celular | Use o endereço `https://…:3444` e aceite o certificado |
| Ele se interrompe sozinho na caixa de som | Ative "Estou de fone de ouvido" só se estiver de fone; sem fone, aumente o volume do microfone e reduza o da caixa |
| Imagem falhou | `./assistente.sh logs comfyui`; o instalador já troca SDXL por SD 1.5 sozinho quando falta memória |
| Porta ocupada | O detector escolhe outra sozinho; os endereços finais estão no `relatorio-instalacao.md` e no `.env` |
| Ver tudo o que aconteceu | `logs/instalacao-*.log`, `relatorio-hardware.md`, `relatorio-teste.md`, `relatorio-instalacao.md` |

---

## Privacidade e uso offline

Conversa, voz, imagens e documentos são processados só no seu computador. A internet é usada apenas para **baixar** imagens Docker e modelos na instalação e para a **busca na web**, quando você pede (pelo SearXNG local, sem conta nem chave). Telemetria do Open WebUI e do Ollama desligada.

---

## Estrutura

```
instalar.sh / instalar.ps1     instaladores de sistema (driver, Docker, Python)
assistente.sh                  comandos do dia a dia
docker-compose.yml             todos os serviços (+ docker-compose.gpu.yml quando há NVIDIA)
config/                        SearXNG e Caddy (HTTPS)
scripts/
  instalar.py                  orquestra a instalação inteira
  detectar_hardware.py         levanta o hardware e escolhe os modelos
  preparar_llm.py              baixa e testa os LLMs (Ollama → llama.cpp → reserva)
  configurar_openwebui.py      admin, chave de API e conferência das conexões
  teste_ponta_a_ponta.py       teste final e relatório
  medir_vram.py                VRAM por componente
servicos/
  base/                        Python 3.12 + PyTorch (cu126/cu128/cpu)
  fala/                        Whisper + Kokoro/Piper, API OpenAI
  ferramentas/                 busca, imagens (ComfyUI), documentos, data/hora
  voz/                         modo de voz avançado (servidor + página)
  comfyui/                     Stable Diffusion
sistema/assistente.service     início automático no Linux
tests/                         testes automatizados e simuladores
```

Para rodar os testes: `python -m venv .venv && .venv/bin/pip install -r servicos/ferramentas/requirements.txt pytest pytest-timeout soundfile numpy num2words && .venv/bin/pytest tests/`.
