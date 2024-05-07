import json


def get_json_data(file_name: str) -> dict:
    with open(file_name) as file:
        return json.load(file)
