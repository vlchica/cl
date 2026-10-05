# GPU Stand: arquivos prontos para a Bambu Lab A1 (PETG HF Creality preto)

## Qual usar: **v2** (`stl/GPU_Stand_v2.stl`)

As duas versões têm o mesmo tamanho (140 × 130 × 200 mm), a mesma placa vertical em "Y", o mesmo reforço no canto do L e o mesmo padrão de furação de ventoinha de 120 mm (4 furos de ⌀2,7 mm a 105 mm de distância). O que muda:

| | v1 (`GPU_Stand.stl`) | v2 (`GPU_Stand_v2.stl`) |
|---|---|---|
| Abertura da base (largura no meio) | 100–105 mm | **110 mm** |
| Área livre para o ar da ventoinha | 102 cm² | **109 cm² (+6%)** |
| Volume de plástico | 194 cm³ | **184 cm³ (−5%)** |
| Área apoiada na mesa | 36 cm² | 29 cm² |
| Encaixes laterais (as "orelhas") | entalhe em V | entalhe reto/arredondado |
| Bico 0.4: tempo / peso | 6,3 h / 137 g | **6,1 h / 132 g** |

A v2 abre mais a base para a hélice de 120 mm (a v1 cobre mais a ponta das pás), gasta menos filamento e mantém tudo o que segura peso igual: placa vertical, canto do L e furos. A lateral da base continua com no mínimo 5 mm de parede nas duas versões. A v1 só vale a pena se você não for usar ventoinha e quiser a base um pouco mais maciça.

## Arquivos (`bambu-a1/`)

Cada bico tem 2 arquivos:

- **`.gcode.3mf`**: já fatiado. É só imprimir: copie para o cartão microSD da A1 e escolha na tela, ou abra no Bambu Studio e clique em *Imprimir*.
- **`.3mf`**: o projeto editável, com impressora, filamento e processo já configurados. Abra no Bambu Studio, fatie e mande imprimir.

| Bico | Arquivo | Camada | Tempo | Filamento |
|---|---|---|---|---|
| 0.4 | `GPU_Stand_v2_A1_bico0.4_PETG-HF-preto` | 0,20 mm (1000 camadas) | ~6 h 05 | 132 g (43 m) |
| 0.6 | `GPU_Stand_v2_A1_bico0.6_PETG-HF-preto` | 0,30 mm (667 camadas) | ~3 h 32 | 138 g (45 m) |
| 0.8 | `GPU_Stand_v2_A1_bico0.8_PETG-HF-preto` | 0,40 mm (500 camadas) | ~2 h 52 | 156 g (51 m) |

Os tempos são a estimativa do Bambu Studio 2.8. Some uns 5–10 min para o aquecimento, a calibração e o nivelamento da A1.

## Configurações usadas

**Filamento (Creality Hyper PETG preto, perfil base "Generic PETG HF")**

| | Bico 0.4 | Bico 0.6 | Bico 0.8 |
|---|---|---|---|
| Bico (°C) | 245 | 250 | 255 |
| Mesa texturizada (°C) | 70 | 70 | 70 |
| Fluxo máximo (mm³/s) | 18 | 20 | 20 |
| Ventoinha | 20–40% (90% em pontes e balanços) | igual | igual |

**Processo**

| | Bico 0.4 | Bico 0.6 | Bico 0.8 |
|---|---|---|---|
| Perfil base | 0.20mm Strength | 0.30mm Strength | 0.40mm Standard |
| Paredes | 4 (≈1,7 mm) | 3 (≈1,9 mm) | 3 (≈2,5 mm) |
| Topo / fundo | 5 / 4 camadas | 4 / 3 | 3 / 3 |
| Preenchimento | 25% giroide | 25% giroide | 25% giroide |

Nos três: sem suporte (a peça só tem chanfros de 45° e pontes de até 20 mm), brim externo de 5 mm, placa Textured PEI.

A peça está **girada 90°** em relação ao STL original, com a placa vertical alinhada ao eixo Y. A mesa da A1 anda em Y, então a placa de 20 cm balança no sentido em que é rígida, e não no fino.

## Diferenças entre os bicos

- **0.4**: o melhor acabamento. As camadas aparecem menos, os furos de ⌀2,7 mm saem na medida e os detalhes das orelhas ficam mais definidos. Em compensação, é o mais demorado (~6 h).
- **0.6**: o meio-termo. Fica quase 2× mais rápido que o 0.4, com camadas visíveis mas uniformes. Com 3 paredes de 0,62 mm, a casca fica até um pouco mais grossa que a do 0.4. Para uma peça funcional como esta, é o que eu escolheria.
- **0.8**: o mais rápido (~2 h 50) e o mais robusto. As paredes de 0,82 mm e as camadas de 0,4 mm grudam muito bem uma na outra. O acabamento é grosseiro, os cantos ficam arredondados e os furos pequenos saem apertados. Gasta mais filamento (156 g).

## Antes de imprimir

1. **Troque o bico de verdade** e informe o diâmetro novo para a A1 (na tela da impressora ou na aba *Dispositivo* do Bambu Studio). O arquivo de 0.6 só pode ser impresso com o bico 0.6, e assim por diante.
2. **Use a placa Textured PEI.** Na placa lisa, o PETG gruda demais e pode arrancar pedaços do PEI. Se for usar a lisa, passe cola bastão antes.
3. **Seque o PETG** se ele estiver aberto há tempo (65 °C por 4–6 h). Filamento úmido dá fiapos e bolhas.
4. Deixe a **calibração de fluxo** marcada ao mandar imprimir. A A1 mede o pressure advance desse filamento sozinha.
5. Os furos da ventoinha são para **parafuso M3 autoatarraxante**. No 0.8 eles saem um pouco menores: passe uma broca de 2,8–3 mm antes.

## Pastas

- `stl/`: os dois modelos originais.
- `bambu-a1/`: os 6 arquivos de impressão.
- `perfis/`: as configurações de filamento e processo em JSON, só como referência. Para guardar os perfis no Bambu Studio, abra o `.3mf` e salve por lá.
- `gerar.py`: o script que gerou os arquivos com o Bambu Studio 2.8 em linha de comando. Ele resolve a herança dos perfis de sistema da A1, coisa que o CLI não faz sozinho.
