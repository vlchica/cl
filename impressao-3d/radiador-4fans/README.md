# Berço de radiador 4 ventoinhas — pronto para a A1 (PETG HF Creality preto)

Duas metades (A e B) de um suporte de radiador de 4 ventoinhas de 120 mm. Cada
metade é uma placa chata de **120 × 220 × 20 mm**, com duas aberturas de ventoinha
de ~110 mm e furos de parafuso de ~3 mm.

## Bico escolhido: **0.6**

É o mais rápido que ainda sai bem. A peça é deitada e rígida (20 mm de espessura),
então não tem balanço nem empenamento pra se preocupar — o que limita o bico são os
furos de parafuso de 3 mm. No 0.6 eles saem usáveis (dá pra passar uma broca de 3 mm
se quiser o ajuste exato); no 0.8 fechariam. O 0.4 sairia um pouco mais limpo, mas
quase dobra o tempo sem necessidade real aqui.

Nenhuma precisa de suporte: as bordas das aberturas são chanfros de 45–60°, que a
impressora faz no ar sem problema.

## Arquivos (`bambu-a1/`)

Para cada opção há um `.gcode.3mf` (já fatiado, vai direto pro microSD ou Bambu
Studio) e um `.3mf` (projeto editável, com impressora/filamento/processo já postos).

| Arquivo | O que é | Tempo | Filamento |
|---|---|---|---|
| `radiador_4fans_A1_bico0.6_pecaA` | só a metade A, centralizada | ~2 h 02 | 92 g |
| `radiador_4fans_A1_bico0.6_pecaB` | só a metade B, centralizada | ~2 h 02 | 92 g |
| `radiador_4fans_A1_bico0.6_2pecas` | as duas juntas na mesma mesa | ~3 h 55 | 184 g |

### Qual usar

**Recomendado: as duas separadas (pecaA e depois pecaB).** Fazer uma de cada vez
custa só ~10 min a mais no total (~4 h 05 contra 3 h 55), e é bem mais seguro: cada
peça fica centralizada, com o brim inteiro em volta, e se uma falhar a outra não vai
junto. É o caminho de menor risco.

**`2pecas`** é a opção de um trabalho só. Também foi testada e fatia limpa, mas as
duas peças ficam a ~2 mm uma da outra e o brim se encontra no meio (você separa
depois). Use se preferir não trocar o carretel/mexer entre as duas.

## Configuração

- Bico **0.6** a **250 °C**, placa **Textured PEI a 70 °C**.
- Camada 0,30 mm (67 camadas), 3 paredes, 25% de preenchimento giroide.
- Brim de 5 mm, **sem suporte**.
- PETG HF Creality preto (perfil base "Generic PETG HF").

## Antes de imprimir

1. **Use o bico 0.6** e informe o diâmetro à A1 (tela da impressora ou aba
   *Dispositivo* do Bambu Studio). Estes arquivos são só para o 0.6.
2. **Placa texturizada** (Textured PEI). No PETG, na placa lisa, passe cola bastão.
3. **Seque o PETG** se estiver aberto há tempo (65 °C por 4–6 h).
4. Os furos de ~3 mm são para parafuso de ventoinha; se precisar do encaixe exato,
   passe uma broca de 3 mm depois.
5. A malha original dos STLs vinha com superfícies sobrepostas; o Bambu Studio reparou
   no fatiamento e as aberturas e furos saíram corretos (conferido na pré-visualização).

## Pastas

- `stl/`: as duas metades originais.
- `bambu-a1/`: os arquivos de impressão.
- `gerar.py`: script que gera tudo pelo CLI do Bambu Studio 2.8.
