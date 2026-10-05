#!/usr/bin/env python3
"""Gera os projetos Bambu Studio (A1 + PETG HF Creality preto) para bicos 0.4/0.6/0.8.

Uso: BAMBU=/caminho/para/squashfs-root python3 gerar.py
(Bambu Studio extraído do AppImage com --appimage-extract; requer numpy e trimesh)

Sem tela, o Bambu Studio fatia mas não gera as miniaturas. Para tê-las num servidor,
rode sob um compositor Wayland headless (weston) e aponte SHIM para as bibliotecas
a pré-carregar (LD_PRELOAD) que levam o OpenGL dele para o OSMesa.
"""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh

AQUI = Path(__file__).resolve().parent
BAMBU = Path(os.environ.get("BAMBU", "squashfs-root"))
SISTEMA = BAMBU / "resources/profiles/BBL"
STL = AQUI / "stl/GPU_Stand_v2.stl"
PERFIS = AQUI / "perfis"
SAIDA = AQUI / "bambu-a1"
TMP = Path(os.environ.get("TMPDIR_GERAR", "/tmp/gpu-stand"))

FILAMENTO_BASE = "Generic PETG HF @BBL A1"

# Ajustes comuns de processo: peça funcional que segura peso.
PROCESSO_COMUM = {
    "sparse_infill_pattern": "gyroid",
    "sparse_infill_density": "25%",
    "brim_type": "outer_only",
    "brim_width": "5",
    "brim_object_gap": "0.1",
    "enable_support": "0",
    "curr_bed_type": "Textured PEI Plate",
}

BICOS = {
    "0.4": {
        "maquina": "Bambu Lab A1 0.4 nozzle",
        "processo_base": "0.20mm Strength @BBL A1",
        "processo": {"wall_loops": "4", "top_shell_layers": "5", "bottom_shell_layers": "4"},
        "temp": "245",
        "vol_max": "18",
    },
    "0.6": {
        "maquina": "Bambu Lab A1 0.6 nozzle",
        "processo_base": "0.30mm Strength @BBL A1 0.6 nozzle",
        "processo": {"wall_loops": "3", "top_shell_layers": "4", "bottom_shell_layers": "3"},
        "temp": "250",
        "vol_max": "20",
    },
    "0.8": {
        "maquina": "Bambu Lab A1 0.8 nozzle",
        "processo_base": "0.40mm Standard @BBL A1 0.8 nozzle",
        "processo": {"wall_loops": "3", "top_shell_layers": "3", "bottom_shell_layers": "3"},
        "temp": "255",
        "vol_max": "20",
    },
}


def filamento(bico, cfg):
    t = cfg["temp"]
    return {
        "type": "filament",
        "name": f"Creality Hyper PETG Preto @A1 {bico}",
        "inherits": FILAMENTO_BASE,
        "from": "User",
        "instantiation": "true",
        "filament_vendor": ["Creality"],
        "filament_colour": ["#000000"],
        "filament_density": ["1.27"],
        "nozzle_temperature": [t],
        "nozzle_temperature_initial_layer": [t],
        "nozzle_temperature_range_low": ["220"],
        "nozzle_temperature_range_high": ["270"],
        "textured_plate_temp": ["70"],
        "textured_plate_temp_initial_layer": ["70"],
        "hot_plate_temp": ["70"],
        "hot_plate_temp_initial_layer": ["70"],
        "filament_max_volumetric_speed": [cfg["vol_max"]],
        "fan_min_speed": ["20"],
        "fan_max_speed": ["40"],
        "fan_cooling_layer_time": ["15"],
        "slow_down_layer_time": ["8"],
        "overhang_fan_speed": ["90"],
        "close_fan_the_first_x_layers": ["3"],
        "compatible_printers": [cfg["maquina"]],
    }


def processo(bico, cfg):
    p = {
        "type": "process",
        "name": f"GPU Stand PETG @A1 {bico}",
        "inherits": cfg["processo_base"],
        "from": "User",
        "instantiation": "true",
        "compatible_printers": [cfg["maquina"]],
    }
    p.update(PROCESSO_COMUM)
    p.update(cfg["processo"])
    return p


_SISTEMA_IDX = {}


def _sistema(nome):
    if not _SISTEMA_IDX:
        for f in SISTEMA.glob("*/*.json"):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            if "name" in d:
                _SISTEMA_IDX[d["name"]] = d
    d = _SISTEMA_IDX[nome]
    cheio = _sistema(d["inherits"]) if d.get("inherits") else {}
    for inc in d.get("include", []):  # templates de G-code da A1
        cheio.update({k: v for k, v in _sistema(inc).items() if k not in ("name", "instantiation")})
    cheio.update({k: v for k, v in d.items() if k != "include"})
    return cheio


META = {"type", "name", "inherits", "from", "instantiation", "compatible_printers"}


def achatar(perfil, destino):
    """O CLI não resolve 'inherits' de perfil de usuário: grava a versão completa.

    Devolve também as chaves alteradas em relação ao perfil de sistema (o Bambu
    Studio usa isso para marcar o que foi modificado no projeto)."""
    pai = _sistema(perfil["inherits"])
    alteradas = sorted(k for k, v in perfil.items() if k not in META and pai.get(k) != v)
    cheio = dict(pai)
    for k in ("setting_id", "description", "instantiation"):
        cheio.pop(k, None)
    cheio.update(perfil)
    destino.write_text(json.dumps(cheio, indent=4, ensure_ascii=False))
    return destino, ";".join(alteradas)


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
                dados = dados.replace(b'key="printer_model_id" value=""', b'key="printer_model_id" value="N2S"')
            zout.writestr(item, dados)
    tmp.replace(caminho)


def maquina_cheia(nome, destino):
    """Perfil de sistema da A1 completo (o CLI também não resolve a cadeia/includes dele)."""
    cheio = _sistema(nome)
    cheio["inherits"] = ""
    destino.write_text(json.dumps(cheio, indent=4, ensure_ascii=False))
    return destino


def stl_posicionado():
    """Gira 90° em Z (placa vertical alinhada ao eixo Y da mesa da A1) e centraliza."""
    m = trimesh.load(STL)
    m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 0, 1]))
    lo, hi = m.bounds
    m.apply_translation([128 - (lo[0] + hi[0]) / 2, 128 - (lo[1] + hi[1]) / 2, -lo[2]])
    TMP.mkdir(parents=True, exist_ok=True)
    out = TMP / "GPU_Stand_v2.stl"
    m.export(out)
    return out


def rodar(args, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(BAMBU / "AppRun"), "--debug", "1", "--outputdir", str(outdir), *args]
    env = dict(os.environ, LD_PRELOAD=os.environ.get("SHIM", ""))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    erros = [l for l in (r.stdout + r.stderr).splitlines() if "[error]" in l and "ZFiller" not in l]
    if r.returncode or erros:
        print("\n".join(erros) or r.stdout[-2000:], file=sys.stderr)
    return json.loads((outdir / "result.json").read_text())


def main():
    PERFIS.mkdir(exist_ok=True)
    SAIDA.mkdir(exist_ok=True)
    stl = stl_posicionado()
    resumo = {}
    for bico, cfg in BICOS.items():
        f = PERFIS / f"filamento_creality-hyper-petg-preto_A1_bico{bico}.json"
        p = PERFIS / f"processo_gpu-stand_A1_bico{bico}.json"
        f.write_text(json.dumps(filamento(bico, cfg), indent=4, ensure_ascii=False) + "\n")
        p.write_text(json.dumps(processo(bico, cfg), indent=4, ensure_ascii=False) + "\n")
        maquina = maquina_cheia(cfg["maquina"], TMP / f"maquina_{bico}.json")
        p_cheio, p_alt = achatar(processo(bico, cfg), TMP / f"processo_{bico}.json")
        f_cheio, f_alt = achatar(filamento(bico, cfg), TMP / f"filamento_{bico}.json")
        base = [
            "--load-settings", f"{maquina};{p_cheio}",
            "--load-filaments", str(f_cheio),
            "--arrange", "0",
        ]
        nome = f"GPU_Stand_v2_A1_bico{bico}_PETG-HF-preto"
        res = rodar([*base, "--slice", "0", "--export-3mf", f"{nome}.gcode.3mf", str(stl)], TMP / f"fatiado_{bico}")
        rodar([*base, "--export-3mf", f"{nome}.3mf", str(stl)], TMP / f"projeto_{bico}")
        for sub, arq in ((f"fatiado_{bico}", f"{nome}.gcode.3mf"), (f"projeto_{bico}", f"{nome}.3mf")):
            (SAIDA / arq).write_bytes((TMP / sub / arq).read_bytes())
            ajustar_3mf(SAIDA / arq, (p_alt, f_alt))
        resumo[bico] = res["sliced_plates"][0]
    json.dump(resumo, open(TMP / "resumo.json", "w"), indent=2)
    for bico, r in resumo.items():
        print(bico, "tempo_h=%.2f" % (r["total_predication"] / 3600), "g=%.1f" % r["filaments"][0]["total_used_g"])


if __name__ == "__main__":
    main()
