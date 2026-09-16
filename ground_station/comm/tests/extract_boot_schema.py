"""
Extract boot-default schema from firmware ELF.

Reads the actual struct layouts to understand what the 46 floats in slot 0 represent.
This shows what the 0x08 schema frame would contain if properly requested.

Usage:
    python -m ground_station.comm.tests.extract_boot_schema
"""

import subprocess
import sys
from pathlib import Path


def get_struct_fields(struct_name: str, elf_path: Path) -> list:
    """Extract field names and offsets from struct using livewatch."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'ground_station.livewatch', 'fields', struct_name],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            print(f'Failed to get fields for {struct_name}')
            return []
        
        # Parse output: "field_name: type @ offset"
        fields = []
        for line in result.stdout.splitlines():
            if ':' in line and '@' in line:
                field_name = line.split(':')[0].strip()
                fields.append(field_name)
        
        return fields
    
    except Exception as e:
        print(f'Error getting fields for {struct_name}: {e}')
        return []


def extract_boot_default_schema():
    """Extract the full 46-variable schema from firmware boot-default."""
    
    elf_path = Path('OBJ/JX_FLY.axf')
    if not elf_path.exists():
        print(f'ELF not found: {elf_path}')
        print('Run a build first to generate OBJ/JX_FLY.axf')
        return
    
    print('Boot-default slot 0 schema (from firmware Subscribe_BootDefault):')
    print('=' * 70)
    
    # Range 0: imu_data (whole _imu_st struct)
    print('\nRange 0: imu_data (_imu_st)')
    imu_fields = get_struct_fields('_imu_st', elf_path)
    if imu_fields:
        for i, field in enumerate(imu_fields):
            print(f'  [{i:2d}] imu_data.{field}')
        imu_count = len(imu_fields)
    else:
        imu_count = 0
        print('  (Could not extract fields — run livewatch verify first)')
    
    # Range 1: DroneStatus (whole DroneStatusTypeDef struct)
    print('\nRange 1: DroneStatus (DroneStatusTypeDef)')
    ds_fields = get_struct_fields('DroneStatusTypeDef', elf_path)
    if ds_fields:
        for i, field in enumerate(ds_fields):
            print(f'  [{imu_count + i:2d}] DroneStatus.{field}')
        ds_count = len(ds_fields)
    else:
        ds_count = 0
        print('  (Could not extract fields)')
    
    # Range 2: system_monitor (whole SYSTEM_MONITOR struct)
    print('\nRange 2: system_monitor (SYSTEM_MONITOR)')
    sys_fields = get_struct_fields('SYSTEM_MONITOR', elf_path)
    if sys_fields:
        for i, field in enumerate(sys_fields):
            print(f'  [{imu_count + ds_count + i:2d}] system_monitor.{field}')
        sys_count = len(sys_fields)
    else:
        sys_count = 0
        print('  (Could not extract fields)')
    
    # Range 3-4: scalars
    offset = imu_count + ds_count + sys_count
    print(f'\nRange 3: UA3RxFrameCnt')
    print(f'  [{offset:2d}] UA3RxFrameCnt')
    
    print(f'\nRange 4: UA3TxFrames')
    print(f'  [{offset+1:2d}] UA3TxFrames')
    
    total = offset + 2
    print(f'\n' + '=' * 70)
    print(f'Total variables: {total}')
    print(f'Expected from capture: 46')
    
    if total == 46:
        print('✓ Schema matches capture!')
    else:
        print(f'✗ Mismatch: schema has {total}, capture has 46')


if __name__ == '__main__':
    extract_boot_default_schema()
