import base64, sys
# Read base64 from stdin and decode to logo.png
b64 = sys.stdin.read().strip()
raw = base64.b64decode(b64)
with open(r'C:\Users\User\Desktop\Loriens website\logo.png', 'wb') as f:
    f.write(raw)
print(f"Written {len(raw)} bytes")
