// Modo de voz avançado — cliente do navegador.
// Microfone sempre aberto com Silero VAD; a fala do assistente toca em fila e é
// cortada na hora quando o usuário começa a falar por cima.

const $ = (id) => document.getElementById(id);
const ui = {
  orbe: $('orbe'), estado: $('estado'), detalhe: $('detalhe'), fila: $('fila'), controles: $('controles'),
  conversa: $('conversa'), ponto: $('ponto-conexao'), mudo: $('botao-mudo'), parar: $('botao-parar'),
  anexo: $('botao-anexo'), nova: $('botao-nova'), arquivo: $('entrada-arquivo'), form: $('form-texto'),
  campo: $('campo-texto'), dialogo: $('dialogo-config'), botaoConfig: $('botao-config'), webui: $('link-webui'),
};

// ------------------------------------------------------------------ preferências
const PADRAO = { voz: 'pf_dora', velocidade: 1.0, silencio: 0.7, fone: false, transcricao: true };
const cfg = (() => {
  try { return { ...PADRAO, ...JSON.parse(localStorage.getItem('voz-config') || '{}') }; } catch { return { ...PADRAO }; }
})();
function salvarConfig() {
  try { localStorage.setItem('voz-config', JSON.stringify(cfg)); } catch { /* navegação privada */ }
}

// ------------------------------------------------------------------ estado
const s = {
  ws: null, tentativas: 0, iniciado: false, mudo: false,
  vad: null, ctx: null, analisador: null, dadosNivel: null,
  filaAudio: [], fonte: null, fonteTurno: null, cadeia: Promise.resolve(),
  silenciados: new Set(), turnoAtual: 0, estadoServidor: 'ouvindo', detalheServidor: '',
  usuarioFalando: false, transcrevendo: 0, bolhas: new Map(), filaPedidos: [],
};

// ------------------------------------------------------------------ conexão
function conectar() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    s.tentativas = 0;
    ui.ponto.classList.add('conectado');
    ui.ponto.title = 'Conectado';
    enviar({ tipo: 'config', voz: cfg.voz, velocidade: cfg.velocidade });
  };
  ws.onclose = () => {
    ui.ponto.classList.remove('conectado');
    ui.ponto.title = 'Reconectando…';
    pararAudio();
    setTimeout(conectar, Math.min(1000 * 2 ** s.tentativas++, 10000));
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') tratarEvento(JSON.parse(ev.data));
    else receberAudio(ev.data);
  };
  s.ws = ws;
}

function enviar(dados) {
  if (!s.ws || s.ws.readyState !== WebSocket.OPEN) return false;
  s.ws.send(dados instanceof ArrayBuffer ? dados : JSON.stringify(dados));
  return true;
}

// ------------------------------------------------------------------ microfone (VAD)
async function iniciar() {
  if (s.iniciado) return;
  if (!window.isSecureContext) {
    mostrarEstado('Microfone bloqueado', 'O navegador só libera o microfone em https:// ou em localhost. Use o endereço https indicado no leia-me.');
    return;
  }
  ui.orbe.dataset.estado = 'pensando';
  mostrarEstado('Abrindo o microfone…', '');
  s.ctx = new (window.AudioContext || window.webkitAudioContext)();
  await s.ctx.resume();
  s.analisador = s.ctx.createAnalyser();
  s.analisador.fftSize = 512;
  s.analisador.connect(s.ctx.destination);
  s.dadosNivel = new Uint8Array(s.analisador.fftSize);
  try {
    s.vad = await vad.MicVAD.new({
      model: 'v5',
      baseAssetPath: '/static/vad/',
      onnxWASMBasePath: '/static/vad/',
      ortConfig: (o) => { o.env.wasm.numThreads = 1; o.env.logLevel = 'error'; },
      positiveSpeechThreshold: 0.6,
      negativeSpeechThreshold: 0.4,
      redemptionMs: cfg.silencio * 1000,
      preSpeechPadMs: 400,
      minSpeechMs: 280,
      getStream: () => navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      }),
      onSpeechStart: () => { s.usuarioFalando = true; atualizarVisual(); },
      onSpeechRealStart: aoUsuarioComecarAFalar,
      onSpeechEnd: aoUsuarioTerminarDeFalar,
      onVADMisfire: () => { s.usuarioFalando = false; enviar({ tipo: 'fala_descartada' }); atualizarVisual(); },
      onFrameProcessed: (prob, quadro) => {
        if (!s.fonte) {
          let soma = 0;
          for (let i = 0; i < quadro.length; i += 4) soma += quadro[i] * quadro[i];
          definirNivel(Math.min(1, Math.sqrt(soma / (quadro.length / 4)) * 6));
        }
      },
    });
  } catch (erro) {
    console.error(erro);
    ui.orbe.dataset.estado = 'parado';
    mostrarEstado('Não consegui abrir o microfone', String(erro?.message || erro));
    return;
  }
  s.iniciado = true;
  ui.controles.hidden = false;
  ui.orbe.setAttribute('aria-label', 'Interromper o assistente');
  ajustarSensibilidade();
  animarNivel();
  atualizarVisual();
}

// Com o assistente falando pela caixa de som, exige uma fala mais clara para
// interromper (evita que o eco dele mesmo corte a resposta). Com fone, não precisa.
function ajustarSensibilidade() {
  if (!s.vad) return;
  const tocando = Boolean(s.fonte) || s.filaAudio.length > 0;
  const rigido = tocando && !cfg.fone;
  s.vad.setOptions({
    positiveSpeechThreshold: rigido ? 0.85 : 0.6,
    negativeSpeechThreshold: rigido ? 0.7 : 0.4,
    minSpeechMs: rigido ? 400 : 280,
    redemptionMs: cfg.silencio * 1000,
  });
}

function aoUsuarioComecarAFalar() {
  s.usuarioFalando = true;
  const assistenteAtivo = s.fonte || s.filaAudio.length || ['pensando', 'falando', 'ferramenta'].includes(s.estadoServidor);
  if (assistenteAtivo) interromperAssistente();
  atualizarVisual();
}

// Interrupção: corta o áudio local na hora e avisa o servidor qual turno estava tocando
function interromperAssistente() {
  const tocando = s.fonteTurno || (s.filaAudio[0] && s.filaAudio[0].turno) || s.turnoAtual;
  if (s.turnoAtual) s.silenciados.add(s.turnoAtual);
  pararAudio();
  enviar({ tipo: 'inicio_fala', turno: tocando || null });
}

function aoUsuarioTerminarDeFalar(audio) {
  s.usuarioFalando = false;
  if (s.mudo) return;
  const wav = vad.utils.encodeWAV(audio, 1, 16000, 1, 16);
  if (enviar(wav)) {
    s.transcrevendo++;
    setTimeout(() => { s.transcrevendo = Math.max(0, s.transcrevendo - 1); atualizarVisual(); }, 8000);
  }
  atualizarVisual();
}

function alternarMudo() {
  s.mudo = !s.mudo;
  ui.mudo.setAttribute('aria-pressed', String(s.mudo));
  if (s.vad) s.mudo ? s.vad.pause() : s.vad.start();
  atualizarVisual();
}

// ------------------------------------------------------------------ áudio do assistente
function receberAudio(buffer) {
  const dv = new DataView(buffer);
  const turno = dv.getUint32(0, true);
  const wav = buffer.slice(8);
  if (turno !== 0 && s.silenciados.has(turno)) return;
  if (!s.ctx) return;
  // Decodifica em ordem, para as frases não trocarem de lugar
  s.cadeia = s.cadeia.then(async () => {
    try {
      const audio = await s.ctx.decodeAudioData(wav);
      if (turno !== 0 && s.silenciados.has(turno)) return;
      s.filaAudio.push({ turno, audio });
      tocarProximo();
    } catch (e) { console.warn('áudio inválido', e); }
  });
}

function tocarProximo() {
  if (s.fonte || !s.filaAudio.length) return;
  const item = s.filaAudio.shift();
  if (item.turno !== 0 && s.silenciados.has(item.turno)) { tocarProximo(); return; }
  const fonte = s.ctx.createBufferSource();
  fonte.buffer = item.audio;
  fonte.connect(s.analisador);
  fonte.onended = () => {
    if (s.fonte !== fonte) return;
    s.fonte = null;
    s.fonteTurno = null;
    if (!s.filaAudio.length) ajustarSensibilidade();
    tocarProximo();
    atualizarVisual();
  };
  s.fonte = fonte;
  s.fonteTurno = item.turno;
  fonte.start();
  ajustarSensibilidade();
  atualizarVisual();
}

function pararAudio() {
  s.filaAudio = [];
  if (s.fonte) {
    const f = s.fonte;
    s.fonte = null;
    s.fonteTurno = null;
    try { f.stop(); } catch { /* já parada */ }
  }
  ajustarSensibilidade();
  atualizarVisual();
}

function definirNivel(n) { ui.orbe.style.setProperty('--nivel', n.toFixed(3)); }

function animarNivel() {
  if (s.fonte && s.analisador) {
    s.analisador.getByteTimeDomainData(s.dadosNivel);
    let soma = 0;
    for (const v of s.dadosNivel) soma += ((v - 128) / 128) ** 2;
    definirNivel(Math.min(1, Math.sqrt(soma / s.dadosNivel.length) * 4));
  }
  requestAnimationFrame(animarNivel);
}

// ------------------------------------------------------------------ eventos do servidor
function tratarEvento(e) {
  switch (e.tipo) {
    case 'pronto':
      break;
    case 'estado':
      s.estadoServidor = e.estado;
      s.detalheServidor = e.detalhe || '';
      s.filaPedidos = e.fila || [];
      atualizarVisual();
      break;
    case 'transcricao':
      s.transcrevendo = Math.max(0, s.transcrevendo - 1);
      adicionarMensagem('usuario', e.texto);
      atualizarVisual();
      break;
    case 'turno_inicio':
      s.turnoAtual = e.turno;
      bolha(e.turno);
      break;
    case 'delta': {
      const b = bolha(e.turno);
      b.texto.textContent += e.texto;
      rolar();
      break;
    }
    case 'ferramenta': {
      const b = bolha(e.turno);
      let chip = b.el.querySelector(`.chip[data-nome="${e.nome}"].rodando`);
      if (e.status === 'inicio') {
        chip = document.createElement('span');
        chip.className = 'chip rodando';
        chip.dataset.nome = e.nome;
        chip.textContent = e.descricao || e.nome;
        b.extras.appendChild(chip);
      } else if (chip) {
        chip.classList.remove('rodando');
        chip.classList.add(e.status === 'erro' ? 'erro' : 'ok');
        if (e.resumo) chip.title = e.resumo;
      }
      rolar();
      break;
    }
    case 'imagem': {
      const img = document.createElement('img');
      img.src = e.url;
      img.alt = e.descricao || 'Imagem gerada';
      img.loading = 'lazy';
      img.onclick = () => window.open(e.url, '_blank');
      img.onload = rolar;
      bolha(e.turno).extras.appendChild(img);
      break;
    }
    case 'arquivo': {
      const a = document.createElement('a');
      a.className = 'arquivo';
      a.href = e.url;
      a.target = '_blank';
      a.download = e.nome;
      a.textContent = `📄 ${e.nome}`;
      bolha(e.turno).extras.appendChild(a);
      rolar();
      break;
    }
    case 'fontes': {
      if (!e.fontes?.length) break;
      const div = document.createElement('div');
      div.className = 'fontes';
      div.append('Fontes: ');
      e.fontes.slice(0, 4).forEach((f, i) => {
        if (i) div.append(' · ');
        const a = document.createElement('a');
        a.href = f.url; a.target = '_blank'; a.rel = 'noopener';
        try { a.textContent = new URL(f.url).hostname.replace(/^www\./, ''); } catch { a.textContent = f.titulo; }
        div.appendChild(a);
      });
      bolha(e.turno).extras.appendChild(div);
      break;
    }
    case 'silenciado':
      s.silenciados.add(e.turno);
      break;
    case 'retomado':
      s.silenciados.delete(e.turno);
      break;
    case 'turno_fim':
      if (e.interrompido) bolha(e.turno).el.classList.add('interrompida');
      break;
    case 'aviso':
      adicionarMensagem('aviso', e.texto);
      break;
    case 'erro':
      adicionarMensagem('erro', e.mensagem);
      break;
    case 'anexo_ok':
      adicionarMensagem('aviso', `Arquivo "${e.nome}" recebido. Pode perguntar sobre ele.`);
      break;
    case 'conversa_limpa':
      ui.conversa.replaceChildren();
      s.bolhas.clear();
      break;
  }
}

// ------------------------------------------------------------------ interface
function mostrarEstado(titulo, detalhe) {
  ui.estado.textContent = titulo;
  ui.detalhe.textContent = detalhe;
}

const ROTULOS = {
  ouvindo: ['Ouvindo', 'Pode falar.'],
  pensando: ['Pensando…', ''],
  falando: ['Falando', 'Fale por cima para interromper.'],
  ferramenta: ['Trabalhando…', ''],
};

function atualizarVisual() {
  if (!s.iniciado) return;
  let estado = s.estadoServidor;
  if (s.fonte) estado = 'falando';
  let [titulo, detalhe] = ROTULOS[estado] || ROTULOS.ouvindo;
  if (estado === 'ferramenta' && s.detalheServidor) titulo = `${s.detalheServidor}…`;
  if (estado === 'ferramenta') detalhe = 'Pode continuar falando: eu anoto e respondo em seguida.';
  if (s.usuarioFalando) { estado = 'usuario'; titulo = 'Ouvindo você…'; detalhe = ''; }
  else if (s.transcrevendo && estado === 'ouvindo') { titulo = 'Entendendo…'; }
  if (s.mudo) { estado = 'mudo'; titulo = 'Microfone desligado'; detalhe = 'Toque no microfone para voltar a ouvir.'; }
  ui.orbe.dataset.estado = estado;
  mostrarEstado(titulo, detalhe);
  if (s.filaPedidos.length) {
    ui.fila.hidden = false;
    ui.fila.textContent = `Na fila (${s.filaPedidos.length}): ${s.filaPedidos.join(' · ')}`;
  } else {
    ui.fila.hidden = true;
  }
}

function adicionarMensagem(tipo, texto) {
  const div = document.createElement('div');
  div.className = `msg ${tipo}`;
  div.textContent = texto;
  ui.conversa.appendChild(div);
  rolar();
  return div;
}

function bolha(turno) {
  if (!s.bolhas.has(turno)) {
    const el = adicionarMensagem('assistente', '');
    const texto = document.createElement('span');
    const extras = document.createElement('div');
    el.append(texto, extras);
    s.bolhas.set(turno, { el, texto, extras });
  }
  return s.bolhas.get(turno);
}

function rolar() { ui.conversa.scrollTop = ui.conversa.scrollHeight; }

async function enviarArquivo(arquivo) {
  adicionarMensagem('aviso', `Lendo "${arquivo.name}"…`);
  const dados = new FormData();
  dados.append('arquivo', arquivo);
  try {
    const r = await fetch('/api/extrair', { method: 'POST', body: dados });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    enviar({ tipo: 'anexo', nome: j.nome, texto: j.texto });
  } catch (e) {
    adicionarMensagem('erro', `Não consegui ler o arquivo: ${e.message || e}`);
  }
}

// ------------------------------------------------------------------ ligações
ui.orbe.addEventListener('click', () => {
  if (!s.iniciado) { iniciar(); return; }
  // Tocar no orbe enquanto o assistente fala = interromper
  if (s.fonte || s.filaAudio.length) interromperAssistente();
});
ui.mudo.addEventListener('click', alternarMudo);
ui.parar.addEventListener('click', () => {
  if (s.turnoAtual) s.silenciados.add(s.turnoAtual);
  pararAudio();
  enviar({ tipo: 'cancelar' });
});
ui.nova.addEventListener('click', () => { pararAudio(); enviar({ tipo: 'nova_conversa' }); });
ui.anexo.addEventListener('click', () => ui.arquivo.click());
ui.arquivo.addEventListener('change', () => {
  if (ui.arquivo.files[0]) enviarArquivo(ui.arquivo.files[0]);
  ui.arquivo.value = '';
});
ui.form.addEventListener('submit', (ev) => {
  ev.preventDefault();
  const texto = ui.campo.value.trim();
  if (!texto) return;
  if (!s.ctx) {
    s.ctx = new (window.AudioContext || window.webkitAudioContext)();
    s.analisador = s.ctx.createAnalyser();
    s.analisador.connect(s.ctx.destination);
    s.dadosNivel = new Uint8Array(s.analisador.fftSize);
  }
  s.ctx.resume();
  if (s.fonte || s.filaAudio.length) interromperAssistente();
  enviar({ tipo: 'texto', texto });
  ui.campo.value = '';
});

// Configurações
const campos = {
  voz: $('cfg-voz'), velocidade: $('cfg-velocidade'), silencio: $('cfg-silencio'), fone: $('cfg-fone'), transcricao: $('cfg-transcricao'),
};
function aplicarConfig() {
  campos.voz.value = cfg.voz;
  campos.velocidade.value = cfg.velocidade;
  campos.silencio.value = cfg.silencio;
  campos.fone.checked = cfg.fone;
  campos.transcricao.checked = cfg.transcricao;
  $('cfg-velocidade-valor').textContent = `${Number(cfg.velocidade).toFixed(2)}×`;
  $('cfg-silencio-valor').textContent = `${Number(cfg.silencio).toFixed(1)} s`;
  ui.conversa.classList.toggle('oculta', !cfg.transcricao);
}
ui.botaoConfig.addEventListener('click', () => ui.dialogo.showModal());
for (const [chave, el] of Object.entries(campos)) {
  el.addEventListener('input', () => {
    cfg[chave] = el.type === 'checkbox' ? el.checked : (el.type === 'range' ? Number(el.value) : el.value);
    salvarConfig();
    aplicarConfig();
    ajustarSensibilidade();
    if (chave === 'voz' || chave === 'velocidade') enviar({ tipo: 'config', voz: cfg.voz, velocidade: cfg.velocidade });
  });
}

aplicarConfig();
conectar();
fetch('/config.json').then((r) => r.json()).then((c) => {
  if (c.webui) { ui.webui.href = c.webui; ui.webui.hidden = false; }
}).catch(() => {});

// Ganchos para testes automatizados
window.__voz = { estado: s, tratarEvento, iniciar };
