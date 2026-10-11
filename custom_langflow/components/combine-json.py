from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data

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
    ]

    def combine_data(self) -> Data:
        merged_payload = {}
        
        # Iterasi seluruh data input dan gabungkan dictionary-nya
        for item in self.input_data:
            if isinstance(item, Data) and isinstance(item.data, dict):
                # Memastikan kita hanya menggabungkan inner payload, bukan meta-keys
                merged_payload.update(item.data)
                
        # Kembalikan sebagai Data object tunggal yang baru dan bersih
        return Data(data=merged_payload)