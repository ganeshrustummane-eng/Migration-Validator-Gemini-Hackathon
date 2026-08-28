"""
Path management utilities for validation runs
"""
import os


def get_config_output_paths(run_id, layer_type, base_dir, config_path, validation_dirs, table_list):
    """
    Build directory structure and path mappings for validation execution
    
    Args:
        run_id: Unique run identifier
        layer_type: 'bronze', 'silver', 'gold', or 'reporting'
        base_dir: Base project directory
        config_path: Path to config directory
        validation_dirs: List of validation types ['count_validation', 'data_validation']
        table_list: List of table names or ['all']
    
    Returns:
        tuple: (outputpaths, configpaths, logpath)
            outputpaths: Dict mapping validation types to output directories
            configpaths: Dict mapping validation types to config file paths
            logpath: Path to log directory
    """
    outputpaths = {}
    configpaths = {}
    
    output_dir = os.path.join(base_dir, "output")
    
    # Normalize layer_type
    if isinstance(layer_type, list):
        layer = layer_type[0]
    else:
        layer = layer_type
    
    logpath = os.path.join(output_dir, layer, f"validation_{run_id}")
    os.makedirs(output_dir, exist_ok=True)
    
    # Create directories based on validation types
    for validation in validation_dirs:
        if validation == 'count_validation':
            path = os.path.join(
                output_dir,
                layer,
                f"validation_{run_id}",
                f"{validation}_{run_id}"
            )
            
            os.makedirs(path, exist_ok=True)
            outputpaths[validation] = path
            config_dir = os.path.join(config_path, layer, validation)
            yaml_paths = []
            if os.path.exists(config_dir):
                if 'all' in table_list:
                    yaml_paths = [
                        os.path.join(config_dir, name)
                        for name in os.listdir(config_dir)
                        if name.endswith('.yaml')
                    ]
                else:
                    yaml_paths = [
                        os.path.join(config_dir, f"{table}.yaml")
                        for table in table_list
                        if os.path.exists(os.path.join(config_dir, f"{table}.yaml"))
                    ]
            configpaths[validation] = yaml_paths
        
        elif validation == 'data_validation':
            path = os.path.join(
                output_dir,
                layer,
                f"validation_{run_id}",
                f"{validation}_{run_id}"
            )
            
            yaml_paths = []
            config_dir = os.path.join(config_path, layer, validation)
            
            if os.path.exists(config_dir):
                if 'all' in table_list:
                    all_configs = os.listdir(config_dir)
                    for table in all_configs:
                        if table.endswith('.yaml'):
                            yaml_paths.append(os.path.join(config_dir, table))
                else:
                    for table in table_list:
                        yaml_file = os.path.join(config_dir, f"{table}.yaml")
                        if os.path.exists(yaml_file):
                            yaml_paths.append(yaml_file)
            
            configpaths[validation] = yaml_paths
            outputpaths[validation] = path
            os.makedirs(path, exist_ok=True)
    
    return outputpaths, configpaths, logpath
