// Teste do modo de voz no Chromium com microfone falso (arquivo WAV com fala).
// Uso: node tests/navegador/teste_voz.mjs http://127.0.0.1:8765 fala.wav [--interromper]
import { chromium } from 'playwright';

const [url, wav, ...flags] = process.argv.slice(2);
const interromper = flags.includes('--interromper');
const navegador = await chromium.launch({
  executablePath: process.env.CHROMIUM || undefined,
  args: [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${wav}`,
    '--autoplay-policy=no-user-gesture-required',
  ],
});
const pagina = await navegador.newPage();
const log = [];
pagina.on('console', (m) => log.push(`[${m.type()}] ${m.text()}`));
pagina.on('pageerror', (e) => log.push(`[pageerror] ${e.message}`));
await pagina.addInitScript(() => {
  // registra tudo o que a página manda pelo WebSocket
  const enviar = WebSocket.prototype.send;
  window.__enviados = [];
  WebSocket.prototype.send = function (d) {
    window.__enviados.push(typeof d === 'string' ? JSON.parse(d) : { binario: d.byteLength });
    return enviar.call(this, d);
  };
});
const t0 = Date.now();
const passo = (msg) => console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s  ${msg}`);
try {
  await pagina.goto(url);
  await pagina.waitForSelector('#ponto-conexao.conectado', { timeout: 10000 });
  passo('WebSocket conectado');
  await pagina.click('#orbe');
  await pagina.waitForFunction(() => window.__voz.estado.iniciado, null, { timeout: 30000 });
  passo('VAD carregado e microfone aberto');
  await pagina.waitForFunction(() => window.__enviados.some((m) => m.binario), null, { timeout: 30000 });
  const tamanho = await pagina.evaluate(() => window.__enviados.find((m) => m.binario).binario);
  passo(`fala detectada pelo VAD e enviada (${tamanho} bytes de WAV)`);
  await pagina.waitForSelector('.msg.usuario', { timeout: 15000 });
  await pagina.waitForFunction(() => [...document.querySelectorAll('.msg.assistente')].some((e) => e.textContent.includes('Brasília')), null, { timeout: 15000 });
  passo('resposta do assistente na tela');
  await pagina.waitForFunction(() => window.__voz.estado.fonte !== null, null, { timeout: 15000 });
  passo(`áudio do assistente tocando (estado do orbe: ${await pagina.getAttribute('#orbe', 'data-estado')})`);
  if (interromper) {
    await pagina.waitForFunction(() => window.__enviados.some((m) => m.tipo === 'inicio_fala'), null, { timeout: 30000 });
    passo('usuário falou por cima: interrupção enviada');
    await pagina.waitForFunction(() => document.querySelector('.msg.assistente.interrompida'), null, { timeout: 30000 });
    passo('resposta anterior marcada como interrompida');
    await pagina.waitForFunction(() => document.querySelectorAll('.msg.usuario').length >= 2, null, { timeout: 30000 });
    passo('nova fala transcrita e atendida');
  }
  await pagina.screenshot({ path: process.env.CAPTURA || 'captura-voz.png' });
  console.log('OK');
} catch (e) {
  console.log('FALHOU:', e.message);
  console.log(log.slice(-30).join('\n'));
  await pagina.screenshot({ path: process.env.CAPTURA || 'captura-voz.png' });
  process.exitCode = 1;
} finally {
  await navegador.close();
}
