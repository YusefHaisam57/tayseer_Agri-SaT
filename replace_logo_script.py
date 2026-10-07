import os
import re

d = r"d:\Tayseer Agri-SaT\Platform\tayseer_Agri-SaT\templates"

new_brand = '''        <!-- BRAND BLOCK -->
        <div class="flex flex-col items-center justify-center text-center pb-6 mb-6 border-b border-white/10">
            <img src="{{ url_for('static', filename='img/logo.png') }}" alt="Tayseer-AgriSaT" class="w-44 h-auto drop-shadow-md mb-2 object-contain" />
        </div>'''

for r, _, fs in os.walk(d):
    for f in fs:
        if f.endswith('.html'):
            p = os.path.join(r, f)
            with open(p, 'r', encoding='utf-8') as file:
                c = file.read()
            
            # 1. Replace the old "brand-block" version (if it exists)
            c = re.sub(
                r'<div class="brand-block">.*?</div>\s*</div>',
                new_brand,
                c,
                flags=re.DOTALL
            )
            
            # 2. Replace the older "flex items-center gap-3" version (with the satellite emoji)
            c = re.sub(
                r'<div class="flex items-center gap-3 mb-8 pb-5 border-b border-white/10">\s*<span class="text-4xl">\s*🛰️.*?</div>\s*</div>',
                new_brand,
                c,
                flags=re.DOTALL
            )
            
            # 3. If it ALREADY HAS the new logo chunk but with the wrong text from my previous mistake, fix that specifically
            c = re.sub(
                r'<div class="flex flex-col items-center justify-center text-center pb-6 mb-6 border-b border-white/10">.*?<img src="\{\{\s*url_for\(\'static\', filename=\'img/logo\.png\'\)\s*\}\}".*?</p>\s*</div>',
                new_brand,
                c,
                flags=re.DOTALL
            )
            
            with open(p, 'w', encoding='utf-8') as file:
                file.write(c)

print('Updated logos in all sidebars.')
