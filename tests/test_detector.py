"""Testes da escolha de modelos pelo hardware."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import detectar_hardware as d  # noqa: E402


def hw(gpus, ram=32, livre=500):
    lista = []
    for i, (nome, gb, cc) in enumerate(gpus):
        lista.append({"indice": i, "fabricante": "nvidia", "nome": nome, "vram_mb": int(gb * 1024), "vram_usada_mb": 0,
                      "compute_cap": cc, "uuid": f"GPU-{i}"})
    return {"gpus": lista, "ram_gb": ram, "discos": [{"nome": "projeto", "caminho": "/", "total_gb": 1000, "livre_gb": livre}],
            "driver_nvidia": "570.1", "outras_gpus": [], "ollama_nativo": False}


def test_rtx_3090_roda_qwen3_omni_com_sdxl():
    p = d.planejar(hw([("RTX 3090", 24, 8.6)], ram=64))
    assert p["llm_principal"] == d.QWEN3_OMNI and p["llm_reserva"] == "qwen3:14b"
    assert p["sd_modelo"] == "sdxl" and p["liberar_vram_para_imagem"] is True
    assert p["torch_indice"] == "cu126" and p["whisper_compute"] == "int8_float16"
    assert not any("NÃO CABE" in a for a in p["avisos"])


def test_placa_de_12gb_avisa_que_omni_nao_cabe():
    p = d.planejar(hw([("RTX 3060", 12, 8.6)]))
    assert p["llm_principal"] == "qwen3:8b"
    assert any("NÃO CABE" in a for a in p["avisos"])


def test_rig_de_mineracao_divide_gpus():
    p = d.planejar(hw([("RTX 3070", 8, 8.6)] * 4, ram=16))
    assert p["llm_principal"] == d.QWEN3_OMNI
    assert p["gpus_llm"] == "0,1,2" and p["gpus_aux"] == "3"
    assert any("RAM" in a for a in p["avisos"])


def test_pascal_usa_int8_e_rtx50_usa_cu128():
    assert d.planejar(hw([("GTX 1060", 6, 6.1)]))["whisper_compute"] == "int8"
    assert d.planejar(hw([("RTX 5090", 32, 12.0)]))["torch_indice"] == "cu128"


def test_sem_gpu_roda_na_cpu():
    p = d.planejar(hw([], ram=16))
    assert p["modo"] == "cpu" and p["torch_indice"] == "cpu" and p["whisper_modelo"] == "small"
    assert p["llm_principal"] == "qwen3:4b"


def test_pouco_disco_rebaixa_em_ordem():
    p = d.planejar(hw([("RTX 3090", 24, 8.6)], livre=60))
    assert p["llm_principal"] == "qwen3:14b" and p["sd_modelo"] == "sd15"
    assert p["disco_necessario_gb"] <= 60


def test_forcar_omni():
    p = d.planejar(hw([("RTX 4060 Ti", 16, 8.9)]), forcar_omni=True)
    assert p["llm_principal"] == d.QWEN3_OMNI
