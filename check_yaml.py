import yaml
from yaml import SafeLoader
def mock_constructor(loader, suffix, node):
    return None
SafeLoader.add_multi_constructor('!', mock_constructor)
try:
    with open('template.yaml', 'r') as f:
        yaml.load(f, Loader=SafeLoader)
    print("YAML_PARSED_OK")
except Exception as e:
    print(f"YAML_ERROR: {e}")
