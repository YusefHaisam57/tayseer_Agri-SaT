import os

d = r'd:\Tayseer Agri-SaT\Platform\tayseer_Agri-SaT\templates'

new_link = '''            <a href="/map-view" class="nav-link" data-ar="🗺️ خريطة المخاطر" data-en="🗺️ Risk Map">🗺️ خريطة المخاطر</a>
            <a class="nav-link'''

files_to_update = [
    'whatsapp.html', 
    'telegram.html', 
    'settings.html', 
    'profile.html', 
    'mini_tayseer.html'
]

for f in files_to_update:
    p = os.path.join(d, f)
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as file:
            c = file.read()
            
        if '🗺️ خريطة المخاطر' not in c:
            c = c.replace('<a class="nav-link', new_link, 1) # Only replace the first occurrence
            with open(p, 'w', encoding='utf-8') as file:
                file.write(c)

# dashboard.html has a slightly different structure for the active link
dashboard_p = os.path.join(d, 'dashboard.html')
if os.path.exists(dashboard_p):
    with open(dashboard_p, 'r', encoding='utf-8') as file:
        dashboard_c = file.read()
        
    if '🗺️ خريطة المخاطر' not in dashboard_c:
        dashboard_new = '''            <a href="/map-view" class="nav-link" data-ar="🗺️ خريطة المخاطر" data-en="🗺️ Risk Map">🗺️ خريطة المخاطر</a>
            <a class="nav-link active"'''
        dashboard_c = dashboard_c.replace('<a class="nav-link active"', dashboard_new, 1)
        with open(dashboard_p, 'w', encoding='utf-8') as file:
            file.write(dashboard_c)

print('Updated sidebars successfully.')
