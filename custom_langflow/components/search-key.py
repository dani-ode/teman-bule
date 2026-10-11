from langflow.custom import Component
from langflow.io import Output, DataInput, TableInput, MessageTextInput
from langflow.schema import Data, Message
from typing import Any
import json

class SafeFallback(str):
    """
    Helper class untuk mengembalikan string kosong secara aman 
    ketika mencoba mengakses atribut atau index pada data yang terdalam.
    """
    def __getattr__(self, name: str) -> 'SafeFallback':
        return SafeFallback("")
    
    def __getitem__(self, key: Any) -> 'SafeFallback':
        return SafeFallback("")

class DeepParsedDict(dict):
    """
    Dictionary wrapper yang mengubah dictionary biasa menjadi object 
    yang mendukung dot-notation bersarang dengan kedalaman tak terbatas.
    """
    def __init__(self, data_map: dict):
        super().__init__()
        if not isinstance(data_map, dict):
            return
            
        for k, v in data_map.items():
            if isinstance(v, dict):
                self[k] = DeepParsedDict(v)
            elif isinstance(v, list):
                self[k] = [DeepParsedDict(i) if isinstance(i, dict) else i for i in v]
            else:
                self[k] = v

    def __getattr__(self, key: str) -> Any:
        val = self.get(key)
        return SafeFallback("") if val is None else val

    def __missing__(self, key: str) -> Any:
        return SafeFallback("")

class DynamicVoiceIDMapper(Component):
    display_name = "Search Key"
    description = "Menerima input dinamis (Message/JSON), memparsing string, mencari kecocokan di tabel, dan mengembalikan nilainya."
    icon = "MessageSquare"

    inputs = [
        DataInput(
            name="input_data",
            display_name="Input Data (Source)",
            info="Hubungkan node sebelumnya di sini (Message, Data, dict, atau str) untuk sumber data variabel.",
            input_types=["Message", "Data", "dict", "str"],
            required=False,
        ),
        MessageTextInput(
            name="lookup_template",
            display_name="Lookup String Template",
            info="Kunci yang akan dicari. Bisa berupa teks statis ('elean') atau template dinamis ('{text}', '{data.nama}').",
            value="{text}",
            required=True,
        ),
        TableInput(
            name="mapping_table",
            display_name="Mapping Table",
            info="Tabel perbandingan. Jika 'Lookup String' sama dengan 'Key', maka 'Value' akan dikeluarkan.",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Key (Pencarian)",
                    "type": "str",
                    "description": "Kata kunci untuk dicocokkan (misal: elean)",
                },
                {
                    "name": "value",
                    "display_name": "Value (Hasil)",
                    "type": "str",
                    "description": "Nilai yang dikeluarkan jika cocok (misal: m3yAHyFEFKtbCIM5n7GF)",
                },
            ],
            value=[],
            is_list=True,
            required=True,
        ),
        MessageTextInput(
            name="fallback_value",
            display_name="Fallback Message",
            info="Pesan yang dikembalikan jika Key tidak ditemukan di dalam tabel. Gunakan {key} untuk menampilkan input yang gagal.",
            value="Input tidak dikenali: {key}",
            required=False,
        )
    ]

    outputs = [
        Output(
            display_name="Output Message", 
            name="output_message", 
            method="map_and_convert"
        ),
    ]

    def _extract_source_data(self) -> dict:
        source_dict = {}
        target_input = self.input_data

        if not target_input:
            return source_dict

        if isinstance(target_input, list) and len(target_input) > 0:
            target_input = target_input[0]

        if isinstance(target_input, dict):
            source_dict.update(target_input)
        elif hasattr(target_input, "data") and isinstance(target_input.data, dict):
            source_dict.update(target_input.data)
            source_dict["data"] = dict(target_input.data)

        if hasattr(target_input, "text") and isinstance(target_input.text, str):
            source_dict["text"] = target_input.text
        elif hasattr(target_input, "content") and isinstance(target_input.content, str):
            source_dict["text"] = target_input.content
            
        if "text" in source_dict and "prompt" not in source_dict:
            source_dict["prompt"] = source_dict["text"]

        return source_dict

    def map_and_convert(self) -> Message:
        source_data = self._extract_source_data()
        formatted_source = DeepParsedDict(source_data)
        
        # 1. Evaluasi Input: Mengubah variabel {} menjadi string utuh
        raw_template = str(self.lookup_template).strip()
        parsed_key = raw_template
        
        if "{" in raw_template and "}" in raw_template:
            try:
                parsed_key = raw_template.format_map(formatted_source)
            except Exception as e:
                print(f"Error memproses lookup_template '{raw_template}': {e}")
                
        parsed_key = parsed_key.strip()
        
        # 2. Proses Pencarian di Tabel
        result_text = None
        if self.mapping_table:
            for row in self.mapping_table:
                if isinstance(row, dict) and "key" in row and "value" in row:
                    row_key = str(row.get("key") or "").strip()
                    if row_key == parsed_key:
                        result_text = str(row.get("value") or "")
                        break
        
        # 3. Tangani jika Key tidak ditemukan di dalam tabel (Fallback)
        if result_text is None:
            fallback_template = str(self.fallback_value)
            # Menambahkan variabel {key} agar bisa dipanggil di template fallback
            source_data["key"] = parsed_key 
            fallback_source = DeepParsedDict(source_data)
            
            try:
                result_text = fallback_template.format_map(fallback_source)
            except Exception:
                # Jika terjadi error saat memformat fallback
                result_text = fallback_template.replace("{key}", parsed_key)

        return Message(text=result_text)