#!/usr/bin/env python3
"""Gera os projetos Bambu Studio (A1 + PETG HF Creality preto, bico 0.6) do berço
de radiador de 4 ventoinhas: cada metade centralizada (mais seguro) e as duas juntas.

Uso: BAMBU=/caminho/para/squashfs-root python3 gerar.py
(Bambu Studio extraído do AppImage; requer numpy e trimesh. Miniaturas: ver
impressao-3d/gpu-stand/gerar.py para o truque de OSMesa/weston sem tela.)
"""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
BAMBU = Path(os.environ.get("BAMBU", "squashfs-root"))
SISTEMA = BAMBU / "resources/profiles/BBL"
STL = AQUI / "stl"
SAIDA = AQUI / "bambu-a1"
TMP = Path(os.environ.get("TMPDIR_GERAR", "/tmp/radiador-4fans"))

MAQUINA = "Bambu Lab A1 0.6 nozzle"
FILAMENTO_BASE = "Generic PETG HF @BBL A1"
PROCESSO_BASE = "0.30mm Strength @BBL A1 0.6 nozzle"
MODEL_ID = "N2S"  # Bambu Lab A1

PECAS = {
    "pecaA": ["obj_1_radiator 4 fans v5.stl_A.stl"],
    "pecaB": ["obj_2_radiator 4 fans v5.stl_B.stl"],
    "2pecas": ["obj_1_radiator 4 fans v5.stl_A.stl", "obj_2_radiator 4 fans v5.stl_B.stl"],
}

FILAMENTO = {
    "type": "filament",
    "name": "Creality Hyper PETG Preto @A1 0.6",
    "inherits": FILAMENTO_BASE,
    "from": "User",
    "instantiation": "true",
    "filament_vendor": ["Creality"],
    "filament_colour": ["#000000"],
    "filament_density": ["1.27"],
    "nozzle_temperature": ["250"],
    "nozzle_temperature_initial_layer": ["250"],
    "nozzle_temperature_range_low": ["220"],
    "nozzle_temperature_range_high": ["270"],
    "textured_plate_temp": ["70"],
    "textured_plate_temp_initial_layer": ["70"],
    "hot_plate_temp": ["70"],
    "hot_plate_temp_initial_layer": ["70"],
    "filament_max_volumetric_speed": ["20"],
    "fan_min_speed": ["20"],
    "fan_max_speed": ["40"],
    "fan_cooling_layer_time": ["15"],
    "slow_down_layer_time": ["8"],
    "overhang_fan_speed": ["90"],
    "close_fan_the_first_x_layers": ["3"],
    "compatible_printers": [MAQUINA],
}

# Peça chata e rígida (20 mm): adesão enorme, sem suporte. Brim segura os cantos
# da placa de 220 mm; 25% giroide dá rigidez de sobra para um berço de radiador.
PROCESSO = {
    "type": "process",
    "name": "Radiador 4fans PETG @A1 0.6",
    "inherits": PROCESSO_BASE,
    "from": "User",
    "instantiation": "true",
    "compatible_printers": [MAQUINA],
    "sparse_infill_pattern": "gyroid",
    "sparse_infill_density": "25%",
    "brim_type": "outer_only",
    "brim_width": "5",
    "brim_object_gap": "0.1",
    "enable_support": "0",
    "curr_bed_type": "Textured PEI Plate",
    "wall_loops": "3",
    "top_shell_layers": "4",
    "bottom_shell_layers": "3",
}

META = {"type", "name", "inherits", "from", "instantiation", "compatible_printers"}
_IDX = {}


def _sistema(nome):
    if not _IDX:
        for f in SISTEMA.glob("*/*.json"):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            if "name" in d:
                _IDX[d["name"]] = d
    d = _IDX[nome]
    cheio = _sistema(d["inherits"]) if d.get("inherits") else {}
    for inc in d.get("include", []):
        cheio.update({k: v for k, v in _sistema(inc).items() if k not in ("name", "instantiation")})
    cheio.update({k: v for k, v in d.items() if k != "include"})
    return cheio


def achatar(perfil, destino):
    """O CLI do Bambu não resolve 'inherits' de perfil de usuário: grava completo."""
    pai = _sistema(perfil["inherits"])
    alteradas = sorted(k for k, v in perfil.items() if k not in META and pai.get(k) != v)
    cheio = {k: v for k, v in pai.items() if k not in ("setting_id", "description", "instantiation")}
    cheio.update(perfil)
    destino.write_text(json.dumps(cheio, indent=4, ensure_ascii=False))
    return destino, ";".join(alteradas)


def maquina_cheia(destino):
    cheio = _sistema(MAQUINA)
    cheio["inherits"] = ""
    cheio["model_id"] = MODEL_ID
    destino.write_text(json.dumps(cheio, indent=4, ensure_ascii=False))
    return destino


def ajustar_3mf(caminho, alteradas):
    """Completa o que o CLI deixa em branco e a interface gráfica preenche."""
    tmp = caminho.with_suffix(".tmp")
    with zipfile.ZipFile(caminho) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            dados = zin.read(item.filename)
            if item.filename == "Metadata/project_settings.config":
                cfg = json.loads(dados)
                cfg["different_settings_to_system"] = [*alteradas, ""]
                dados = json.dumps(cfg, indent=4, ensure_ascii=False).encode()
            elif item.filename == "Metadata/slice_info.config":
                dados = dados.replace(b'key="printer_model_id" value=""',
                                      b'key="printer_model_id" value="%s"' % MODEL_ID.encode())
            zout.writestr(item, dados)
    tmp.replace(caminho)


def rodar(args, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(BAMBU / "AppRun"), "--debug", "1", "--outputdir", str(outdir), *args]
    env = dict(os.environ, LD_PRELOAD=os.environ.get("SHIM", ""))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    erros = [l for l in (r.stdout + r.stderr).splitlines() if "[error]" in l and "ZFiller" not in l]
    if erros:
        print("\n".join(erros), file=sys.stderr)
    return json.loads((outdir / "result.json").read_text())


def main():
    SAIDA.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    maquina = maquina_cheia(TMP / "maquina.json")
    proc, p_alt = achatar(PROCESSO, TMP / "processo.json")
    fila, f_alt = achatar(FILAMENTO, TMP / "filamento.json")
    base = ["--load-settings", f"{maquina};{proc}", "--load-filaments", str(fila),
            "--arrange", "1", "--ensure-on-bed"]
    resumo = {}
    for nome, arquivos in PECAS.items():
        stls = [str(STL / a) for a in arquivos]
        saida_nome = f"radiador_4fans_A1_bico0.6_{nome}_PETG-HF-preto"
        res = rodar([*base, "--slice", "0", "--export-3mf", f"{saida_nome}.gcode.3mf", *stls],
                    TMP / f"fatiado_{nome}")
        rodar([*base, "--export-3mf", f"{saida_nome}.3mf", *stls], TMP / f"projeto_{nome}")
        for sub, arq in ((f"fatiado_{nome}", f"{saida_nome}.gcode.3mf"),
                         (f"projeto_{nome}", f"{saida_nome}.3mf")):
            (SAIDA / arq).write_bytes((TMP / sub / arq).read_bytes())
            ajustar_3mf(SAIDA / arq, (p_alt, f_alt))
        resumo[nome] = res["sliced_plates"][0]
    for nome, r in resumo.items():
        print(nome, "tempo_h=%.2f" % (r["total_predication"] / 3600),
              "g=%.1f" % r["filaments"][0]["total_used_g"])


if __name__ == "__main__":
    main()
