#!/usr/bin/env python3
"""Test Pydantic v2 settings configuration to debug Railway deployment issues"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os
import sys

# Set a test PORT environment variable
os.environ['PORT'] = '8080'

print("Testing Pydantic Settings with minimal environment variables...")
print(f"Environment: PORT={os.environ.get('PORT')}")
print("-" * 60)

class TestSettings(BaseSettings):
    # Test with different field definitions
    port: int = Field(default=8000, validation_alias='PORT')
    database_url: Optional[str] = Field(default=None, validation_alias='DATABASE_URL')
    api_key: Optional[str] = Field(default=None, validation_alias='API_KEY')
    
    class Config:
        # This is the old Pydantic v1 style Config - might be the issue
        env_file = ".env"
        case_sensitive = False
        populate_by_name = True

# Try the new Pydantic v2 style
class TestSettingsV2(BaseSettings):
    # Use proper Pydantic v2 field definitions
    port: int = Field(default=8000, alias='PORT')
    database_url: Optional[str] = Field(default=None, alias='DATABASE_URL')
    api_key: Optional[str] = Field(default=None, alias='API_KEY')
    
    model_config = {
        'env_file': '.env',
        'case_sensitive': False,
        'populate_by_name': True,
        'use_enum_values': True,
        'validate_default': True,
    }

print("Test 1: Original style (with validation_alias and Config class)")
try:
    settings1 = TestSettings()
    print(f"✅ Success! Port={settings1.port}, DB={settings1.database_url}")
except Exception as e:
    print(f"❌ Failed: {e}")

print("\nTest 2: Pydantic v2 style (with alias and model_config)")
try:
    settings2 = TestSettingsV2()
    print(f"✅ Success! Port={settings2.port}, DB={settings2.database_url}")
except Exception as e:
    print(f"❌ Failed: {e}")

print("\n" + "-" * 60)
print("Diagnosis: If Test 1 fails but Test 2 succeeds, we need to update to Pydantic v2 syntax")