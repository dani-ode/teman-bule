from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data, Message
import json

class CombineJSONDataComponent(Component):
    display_name = "Combine 2 JSON (Data)"
    description = "Menggabungkan payload dari multiple Data objects menjadi satu kesatuan."

    inputs = [
        DataInput(
            name="input_data",
            display_name="Input Data (List)",
            is_list=True,
            info="Sambungkan output data yang berisi list/array yang ingin digabung."
        ),
    ]

    outputs = [
        Output(display_name="Combined Data", name="combined_data", method="combine_data"),
        Output(display_name="Combined JSON String", name="json_string", method="to_json_string"),
    ]

    def combine_data(self) -> Data:
        merged_payload = {}
        for item in self.input_data:
            if isinstance(item, Data) and isinstance(item.data, dict):
                merged_payload.update(item.data)
        return Data(data=merged_payload)

    def to_json_string(self) -> Message:
        """Output sebagai Message string untuk ChatOutput."""
        merged_payload = {}
        for item in self.input_data:
            if isinstance(item, Data) and isinstance(item.data, dict):
                merged_payload.update(item.data)
        # Return sebagai Message dengan text berisi JSON string
        return Message(text=json.dumps(merged_payload, ensure_ascii=False))