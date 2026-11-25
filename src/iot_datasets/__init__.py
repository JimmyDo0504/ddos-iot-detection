"""Real IoT intrusion dataset loaders (CIC-IoT-2023, N-BaIoT)."""
from iot_datasets.cic_iot2023 import load_cic_iot2023
from iot_datasets.n_baiot import load_n_baiot

DATASETS = {
    "cic_iot2023": {"loader": load_cic_iot2023, "folder": "CIC-IoT-2023", "label_mode": "8class",
                    "title": "CIC-IoT-2023"},
    "n_baiot": {"loader": load_n_baiot, "folder": "N-BaIoT", "label_mode": "11class",
                "title": "N-BaIoT"},
}
