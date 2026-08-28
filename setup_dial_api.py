#!/usr/bin/env python3
"""
DIAL-API Configuration Setup Script
Helps configure the DIAL API for AI-powered SQL generation in Migration Validator
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional


def load_env_file(env_path: str = ".env") -> Dict[str, str]:
    """Load environment variables from .env file"""
    env_vars = {}
    if not os.path.exists(env_path):
        return env_vars
    
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    
    return env_vars


def update_env_file(updates: Dict[str, str], env_path: str = ".env"):
    """Update or add variables to .env file"""
    env_vars = load_env_file(env_path)
    env_vars.update(updates)
    
    # Read existing file to preserve comments
    lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
    
    # Update existing variables
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    # Add new variables
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")
    
    # Write back
    with open(env_path, 'w') as f:
        f.writelines(new_lines)


def create_dial_config(
    api_key_var: str = "${DIAL_API_KEY}",
    models: Optional[List[Dict]] = None
) -> Dict:
    """Create DIAL-API configuration"""
    
    if models is None:
        models = [
            {
                "id": "gpt-4o",
                "name": "GPT-4 Optimized (Best Accuracy)",
                "url": "https://ai-proxy.lab.epam.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-04-01-preview",
                "toolCalling": True,
                "vision": True,
                "maxInputTokens": 128000,
                "maxOutputTokens": 16000
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4 Mini (Faster & Cost-Effective)",
                "url": "https://ai-proxy.lab.epam.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-04-01-preview",
                "toolCalling": True,
                "vision": True,
                "maxInputTokens": 128000,
                "maxOutputTokens": 16000
            },
            {
                "id": "claude-3-5-sonnet",
                "name": "Claude 3.5 Sonnet",
                "url": "https://ai-proxy.lab.epam.com/anthropic/v1/messages",
                "toolCalling": True,
                "vision": True,
                "maxInputTokens": 200000,
                "maxOutputTokens": 4096
            },
            {
                "id": "gemini-2.0-flash",
                "name": "Gemini 2.0 Flash",
                "url": "https://ai-proxy.lab.epam.com/google/v1beta/models/gemini-2.0-flash:generateContent",
                "toolCalling": True,
                "vision": True,
                "maxInputTokens": 1000000,
                "maxOutputTokens": 8192
            }
        ]
    
    return {
        "name": "DIAL-API",
        "vendor": "customendpoint",
        "apiKey": api_key_var,
        "apiType": "chat-completions",
        "models": models
    }


def setup_dial_api_interactive():
    """Interactive setup for DIAL-API configuration"""
    
    print("=" * 80)
    print("DIAL-API Configuration Setup for Migration Validator")
    print("=" * 80)
    print()
    
    # Check if .env exists
    env_path = ".env"
    if not os.path.exists(env_path):
        print("⚠️  .env file not found. Copying from .env.example...")
        if os.path.exists(".env.example"):
            import shutil
            shutil.copy(".env.example", env_path)
            print("✅ Created .env file")
        else:
            print("❌ .env.example not found. Please create .env manually.")
            return
    
    print()
    
    # Load current env
    env_vars = load_env_file(env_path)
    
    # Get API key
    current_key = env_vars.get('DIAL_API_KEY', '')
    print(f"Current DIAL_API_KEY: {current_key[:20]}..." if current_key else "Current DIAL_API_KEY: Not set")
    print()
    print("Enter your DIAL API key (get it from https://ai-proxy.lab.epam.com)")
    print("Press Enter to keep current value, or paste new key:")
    
    new_key = input("DIAL_API_KEY: ").strip()
    if new_key:
        api_key = new_key
    else:
        api_key = current_key
    
    if not api_key:
        print()
        print("❌ API key is required. Please set DIAL_API_KEY in .env manually.")
        print("   Get your key from: https://ai-proxy.lab.epam.com")
        return
    
    # Get API base
    current_base = env_vars.get('DIAL_API_BASE', 'https://ai-proxy.lab.epam.com')
    print()
    print(f"Current DIAL_API_BASE: {current_base}")
    print("Press Enter to keep current, or enter new URL:")
    new_base = input("DIAL_API_BASE: ").strip()
    api_base = new_base if new_base else current_base
    
    # Get API version
    current_version = env_vars.get('DIAL_API_VERSION', '2025-04-01-preview')
    print()
    print(f"Current DIAL_API_VERSION: {current_version}")
    print("Press Enter to keep current, or enter new version:")
    new_version = input("DIAL_API_VERSION: ").strip()
    api_version = new_version if new_version else current_version
    
    # Choose default model
    print()
    print("Available models:")
    print("  1. gpt-4o (Best accuracy, higher cost)")
    print("  2. gpt-4o-mini (Faster, lower cost)")
    print("  3. claude-3-5-sonnet (Alternative, good for complex queries)")
    print("  4. gemini-2.0-flash (Fast, large context)")
    print()
    
    current_model = env_vars.get('DIAL_MODEL', 'gpt-4o')
    print(f"Current DIAL_MODEL: {current_model}")
    print("Press Enter to keep current, or enter model name (1-4 or full name):")
    
    model_choice = input("DIAL_MODEL: ").strip()
    
    model_map = {
        '1': 'gpt-4o',
        '2': 'gpt-4o-mini',
        '3': 'claude-3-5-sonnet',
        '4': 'gemini-2.0-flash'
    }
    
    if model_choice in model_map:
        default_model = model_map[model_choice]
    elif model_choice:
        default_model = model_choice
    else:
        default_model = current_model
    
    # Update .env file
    print()
    print("Updating .env file...")
    
    updates = {
        'DIAL_API_KEY': api_key,
        'DIAL_API_BASE': api_base,
        'DIAL_API_VERSION': api_version,
        'DIAL_MODEL': default_model
    }
    
    update_env_file(updates, env_path)
    print("✅ Updated .env file")
    
    # Create dial_config.json
    print()
    print("Creating dial_config.json...")
    
    config = create_dial_config(api_key_var="${DIAL_API_KEY}")
    
    with open('dial_config.json', 'w') as f:
        json.dump(config, f, indent=2)
    
    print("✅ Created dial_config.json")
    
    # Summary
    print()
    print("=" * 80)
    print("✅ DIAL-API Configuration Complete!")
    print("=" * 80)
    print()
    print("Configuration Summary:")
    print(f"  API Key: {api_key[:20]}..." if len(api_key) > 20 else f"  API Key: {api_key}")
    print(f"  API Base: {api_base}")
    print(f"  API Version: {api_version}")
    print(f"  Default Model: {default_model}")
    print()
    print("Next Steps:")
    print("  1. Test your configuration:")
    print("     python src/model_probe.py")
    print()
    print("  2. List available models:")
    print("     python src/validate_cli.py list-models")
    print()
    print("  3. Generate your first validation query:")
    print("     python src/validate_cli.py generate --help")
    print()
    print("Configuration files created:")
    print(f"  - {env_path} (updated)")
    print("  - dial_config.json (new)")
    print()
    print("For more information, see: DIAL_API_CONFIGURATION_GUIDE.md")
    print()


def verify_dial_config():
    """Verify DIAL-API configuration"""
    
    print("=" * 80)
    print("DIAL-API Configuration Verification")
    print("=" * 80)
    print()
    
    # Check .env
    env_vars = load_env_file(".env")
    
    required_vars = ['DIAL_API_KEY', 'DIAL_API_BASE', 'DIAL_API_VERSION', 'DIAL_MODEL']
    missing = []
    
    print("Environment Variables:")
    for var in required_vars:
        value = env_vars.get(var)
        if value:
            display_value = f"{value[:20]}..." if len(value) > 20 and var == 'DIAL_API_KEY' else value
            print(f"  ✅ {var}: {display_value}")
        else:
            print(f"  ❌ {var}: Not set")
            missing.append(var)
    
    print()
    
    # Check dial_config.json
    if os.path.exists('dial_config.json'):
        print("✅ dial_config.json exists")
        
        with open('dial_config.json', 'r') as f:
            config = json.load(f)
        
        print(f"  Models configured: {len(config.get('models', []))}")
        for model in config.get('models', []):
            model_id = model.get('id', 'Unknown')
            model_name = model.get('name', 'Unknown')
            print(f"    - {model_id}: {model_name}")
    else:
        print("❌ dial_config.json not found")
        missing.append('dial_config.json')
    
    print()
    
    # Check .dial_model_cache.json
    if os.path.exists('.dial_model_cache.json'):
        print("✅ .dial_model_cache.json exists (model probe has been run)")
        
        with open('.dial_model_cache.json', 'r') as f:
            cache = json.load(f)
        
        working_models = [k for k, v in cache.get('results', {}).items() if v]
        print(f"  Working models: {len(working_models)}")
        for model in working_models:
            print(f"    - {model}")
    else:
        print("⚠️  .dial_model_cache.json not found")
        print("   Run: python src/model_probe.py")
    
    print()
    
    if missing:
        print("❌ Configuration incomplete. Missing:")
        for item in missing:
            print(f"  - {item}")
        print()
        print("Run: python setup_dial_api.py")
    else:
        print("✅ All configuration files present")
        print()
        print("Next: python src/model_probe.py")
    
    print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify_dial_config()
    else:
        setup_dial_api_interactive()
