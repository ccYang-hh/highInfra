from tmatrix.runtime.config import get_config_manager


if __name__ == '__main__':
    config_manager = get_config_manager(config_path="config.json")
    print(config_manager.config)
