with open('ground_station/comm/wifi_bridge.py', 'rb') as f:
    content = f.read()

# Find the target
idx = content.find(b'# Update request state and record sent timestamp in metadata')
if idx >= 0:
    print('Found at offset:', idx)
    print(repr(content[idx:idx+600]))
    # Check what's after
    after = content[idx:idx+700]
    # Find the next meaningful content
    print()
    print('Searching for retry_count...')
    idx2 = after.find(b'retry_count')
    print('retry_count at offset:', idx2)
    print(repr(after[idx2-20:idx2+50]))
