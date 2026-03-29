import re

filepath = r'C:\Users\Angela\Desktop\Loriens website\index.html'

with open(filepath, 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Add logo to nav
html = html.replace(
    '<div class="logo">Lorien',
    '<div class="logo"><img src="logo.png" alt="LL" style="height:28px;vertical-align:middle;margin-right:8px;border-radius:50%;">Lorien'
)

# 2. Add hero logo image before h1
html = html.replace(
    '<h1>Lorien',
    '<img src="logo.png" alt="Lorien\'s Library" style="width:120px;height:120px;border-radius:50%;margin-bottom:1.5rem;box-shadow:0 0 40px rgba(201,168,76,0.2);">\n  <h1>Lorien',
    1
)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(html)

print("Done - logo added to nav and hero with glow")
