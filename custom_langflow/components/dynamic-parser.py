from langflow.custom import Component
from langflow.io import Output, DataInput, TableInput
from langflow.schema import Data, Message
import pandas as pd
from typing import Any
import json

# ==========================================
# 1. HELPER CLASSES UNTUK DYNAMIC PARSING
# ==========================================

class SafeFallback(str):
    """Mengembalikan string kosong secara aman jika data terdalam tidak ditemukan."""
    def __getattr__(self, name: str) -> 'SafeFallback':
        return SafeFallback("")
    
    def __getitem__(self, key: Any) -> 'SafeFallback':
        return SafeFallback("")

class DeepParsedList(list):
    """Memungkinkan akses array index menggunakan dot-notation: {output.0.id}"""
    def __getattr__(self, index: str) -> Any:
        try:
            idx = int(index)
            return self[idx]
        except (ValueError, IndexError):
            return SafeFallback("")

    def __getitem__(self, index: Any) -> Any:
        try:
            return super().__getitem__(index)
        except (IndexError, TypeError):
            return SafeFallback("")

def _recursive_wrap(data: Any) -> Any:
    """Membungkus list dan dict agar bisa diparse mendalam."""
    if isinstance(data, dict):
        return DeepParsedDict(data)
    elif isinstance(data, list):
        return DeepParsedList([_recursive_wrap(i) for i in data])
    # Langflow Data Unpacker
    elif hasattr(data, "data") and isinstance(data.data, dict):
        return _recursive_wrap(data.data)
    return data

class DeepParsedDict(dict):
    """Dictionary dengan akses dot-notation tanpa batas kedalaman."""
    def __init__(self, data_map: dict):
        super().__init__()
        if not isinstance(data_map, dict):
            return
            
        for k, v in data_map.items():
            self[k] = _recursive_wrap(v)

    def __getattr__(self, key: str) -> Any:
        val = self.get(key)
        return SafeFallback("") if val is None else val

    def __missing__(self, key: str) -> Any:
        return SafeFallback("")


# ==========================================
# 2. KOMPONEN UTAMA
# ==========================================

class AstraDynamicParserFilterComponent(Component):
    display_name = "Dynamic Parser"
    description = "Menerima input lalu membangun filter secara dinamis. Mendukung array indexing (.0.) dengan output JSON, Data, atau Message."
    icon = "table"

    inputs = [
        DataInput(
            name="input_data",
            display_name="Input Data (Upstream)",
            info="Hubungkan node sebelumnya (Data, Message, dict, list/tabel, DataFrame).",
            input_types=["Message", "Data", "dict", "str", "list", "DataFrame"],
            required=False,
        ),
        TableInput(
            name="filter_table",
            display_name="Filter Parameters",
            info="Gunakan format template bersarang seperti {output.0.document_id} atau {document_id}",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Metadata Key",
                    "type": "str",
                },
                {
                    "name": "value",
                    "display_name": "Metadata Value",
                    "type": "str",
                },
            ],
            value=[],
            is_list=True,
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="JSON Dict", name="json_output", method="build_json"),
        Output(display_name="Table (Data)", name="data_output", method="build_data"),
        Output(display_name="Message Text", name="message_output", method="build_message"),
    ]

    def _extract_source_data(self) -> dict:
        source_dict = {}
        target_input = self.input_data

        if not target_input:
            return source_dict

        def try_parse_json(val: Any) -> Any:
            if isinstance(val, str):
                val_stripped = val.strip()
                if val_stripped.startswith(("{", "[")):
                    try:
                        return json.loads(val_stripped)
                    except Exception:
                        pass
            return val

        target_input = try_parse_json(target_input)

        # ========================================================
        # INTI PERBAIKAN: Menyamakan struktur Data sesuai layar UI
        # ========================================================
        match target_input:
            # 1. Jika data dari Table Operations (Pandas DataFrame)
            case _ if isinstance(target_input, pd.DataFrame) or type(target_input).__name__ == "DataFrame":
                records = target_input.to_dict(orient="records")
                source_dict["tabel"] = records
                source_dict["list"] = records
                # JAWABAN DARI MASALAH ANDA: Buatkan key 'output' 
                # agar {output.0.document_id} bisa terbaca dengan sempurna.
                source_dict["output"] = records 
                
                # Pasang data pertama di root agar {document_id} juga jalan
                if records:
                    source_dict.update(records[0])

            # 2. Jika data dari port yang mengirim List
            case list():
                clean_list = []
                for item in target_input:
                    if hasattr(item, "data") and isinstance(item.data, dict):
                        clean_list.append(item.data)
                    elif isinstance(item, dict):
                        clean_list.append(item)
                    else:
                        clean_list.append(item)
                
                source_dict["tabel"] = clean_list
                source_dict["list"] = clean_list
                source_dict["output"] = clean_list # Buatkan key 'output'
                
                if len(clean_list) > 0 and isinstance(clean_list[0], dict):
                    source_dict.update(clean_list[0])

            # 3. Jika berupa Langflow Data Object
            case _ if hasattr(target_input, "data") and isinstance(target_input.data, dict):
                source_dict.update(target_input.data)
                source_dict["data"] = dict(target_input.data)
                source_dict["output"] = [dict(target_input.data)] # Wrap ke dalam list

            # 4. Jika murni dictionary
            case dict():
                source_dict.update(target_input)
                # Jika kebetulan dia tidak punya "output", kita jadikan dirinya sbg isi output
                if "output" not in source_dict:
                    source_dict["output"] = [target_input]

            # 5. Jika Message dari Langflow LLM
            case _:
                text_content = ""
                if hasattr(target_input, "text") and isinstance(target_input.text, str):
                    text_content = target_input.text
                elif hasattr(target_input, "content") and isinstance(target_input.content, str):
                    text_content = target_input.content
                elif isinstance(target_input, str):
                    text_content = target_input
                
                if text_content:
                    parsed_text = try_parse_json(text_content)
                    if isinstance(parsed_text, dict):
                        source_dict.update(parsed_text)
                    elif isinstance(parsed_text, list):
                        source_dict["output"] = parsed_text
                        if len(parsed_text) > 0 and isinstance(parsed_text[0], dict):
                            source_dict.update(parsed_text[0])
                    else:
                        source_dict["text"] = text_content

        if "text" in source_dict and "prompt" not in source_dict:
            source_dict["prompt"] = source_dict["text"]

        return source_dict

    def _generate_parsed_dict(self) -> dict:
        source_data = self._extract_source_data()
        formatted_source = DeepParsedDict(source_data)
        result_filter = {}

        if not self.filter_table:
            return result_filter

        for row in self.filter_table:
            if isinstance(row, dict) and "key" in row and "value" in row:
                raw_k = str(row.get("key") or "").strip()
                v = row.get("value", "")

                if not raw_k:
                    continue

                k = raw_k
                if "{" in k and "}" in k:
                    try:
                        k = k.format_map(formatted_source)
                    except Exception:
                        k = raw_k
                
                k = k.strip()
                if not k:
                    continue

                formatted_val = v
                if isinstance(v, str):
                    v_trimmed = v.strip()
                    if "{" in v_trimmed and "}" in v_trimmed:
                        try:
                            formatted_val = v_trimmed.format_map(formatted_source)
                        except Exception:
                            formatted_val = v_trimmed

                    if isinstance(formatted_val, str):
                        val_lower = formatted_val.lower()
                        if val_lower == "true":
                            formatted_val = True
                        elif val_lower == "false":
                            formatted_val = False
                        elif val_lower in ("null", "none"):
                            formatted_val = None
                        elif formatted_val.isdigit():
                            formatted_val = int(formatted_val)

                result_filter[k] = formatted_val

        return result_filter
        
    def build_json(self) -> dict:
        return self._generate_parsed_dict()

    def build_data(self) -> Data:
        return Data(data=self._generate_parsed_dict())

    def build_message(self) -> Message:
        return Message(text=json.dumps(self._generate_parsed_dict(), indent=2))