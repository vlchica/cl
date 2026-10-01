// Testa a interface do Open WebUI (rodando o ensaio: python3 tests/ensaio_openwebui.py --manter):
// login, chat que usa a ferramenta criar_documento e modo de chamada com microfone falso.
// Uso: node tests/navegador/teste_openwebui.mjs SENHA fala.wav [http://localhost:13000]
import { chromium } from 'playwright';

const [senha, wav, url = 'http://localhost:13000'] = process.argv.slice(2);
const nav = await chromium.launch({
  executablePath: process.env.CHROMIUM || undefined,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${wav}`, '--autoplay-policy=no-user-gesture-required'],
});
const ctx = await nav.newContext({ locale: 'pt-BR', viewport: { width: 1280, height: 800 }, permissions: ['microphone'] });
const p = await ctx.newPage();
const t0 = Date.now();
const passo = (m) => console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s  ${m}`);
async function fecharDialogos() {
  // "Novidades" e outros avisos do Open WebUI aparecem por cima nas primeiras visitas
  for (let i = 0; i < 5 && (await p.locator('div[role="dialog"]').count()); i++) {
    await p.keyboard.press('Escape');
    await p.waitForTimeout(400);
  }
}
async function entrar() {
  await p.goto(`${url}/auth`);
  await p.fill('input[type=email]', 'admin@assistente.local');
  await p.fill('input[type=password]', senha);
  await p.keyboard.press('Enter');
  await p.waitForSelector('#chat-input', { timeout: 30000 });
  await p.waitForTimeout(800);
  await fecharDialogos();
}
try {
  await entrar();
  passo(`login OK (interface em ${await p.evaluate(() => document.documentElement.lang)})`);
  await p.click('#chat-input');
  await p.keyboard.type('Crie um documento PDF com a lista de compras');
  await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.body.innerText.includes('criar_documento') && document.body.innerText.includes('Já está na tela'), null, { timeout: 60000 });
  passo('chat: o modelo chamou criar_documento e respondeu');
  await p.goto(url);
  await p.waitForSelector('#chat-input');
  await p.waitForTimeout(800);
  await fecharDialogos();
  await p.locator('button[aria-label="Modo de voz"], button[aria-label="Voice mode"]').first().click();
  passo('modo de chamada aberto');
  await p.waitForFunction(() => /capital do Brasil/i.test(document.body.innerText), null, { timeout: 45000 });
  passo('fala transcrita pelo Whisper e enviada');
  await p.waitForFunction(() => /Brasília/i.test(document.body.innerText), null, { timeout: 45000 });
  passo('resposta recebida (e falada pelo serviço de fala)');
  console.log('OK');
} catch (e) {
  console.log('FALHOU:', e.message);
  process.exitCode = 1;
} finally {
  await nav.close();
}
