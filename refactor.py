import re

with open('web/static/user_dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 1. Extract session block
session_block_regex = r"(<!-- ── ULANISH ── -->\s*<div v-else-if=\"tab === 'session'\".*?</div>\s*</div>\s*</div>)\s*(?=<!-- ── TARIF ── -->)"
match = re.search(session_block_regex, html, flags=re.DOTALL)
if not match:
    print('Session block not found!')
    exit(1)

session_content = match.group(1)

# Modify session content to fit inside settings
# Remove the v-else-if
session_content_clean = re.sub(r'<div v-else-if="tab === \'session\'" key="session" class="space-y-4">', '<div class="space-y-4 mb-4">', session_content)
# Remove the ULANISH comment
session_content_clean = session_content_clean.replace("<!-- ── ULANISH ── -->\n", "")
session_content_clean = session_content_clean.replace('<h2 class="text-xl font-bold">Akkaunt ulanishi</h2>', '<h3 class="text-lg font-bold">Akkaunt ulanishi</h3>')

# 2. Remove session block from original location
html = html[:match.start()] + html[match.end():]

# 3. Insert into sozlamalar block
sozlama_header = r'(<h2 class="text-xl font-bold">Sozlamalar</h2>)'
replacement = r"\1\n\n                <!-- Kiritilgan Ulanish qismi -->\n" + session_content_clean
html = re.sub(sozlama_header, replacement, html, count=1)

# 4. Remove session from bottom nav
nav_button_regex = r"<button @click=\"tab='session'\".*?</button>\s*"
html = re.sub(nav_button_regex, "", html, flags=re.DOTALL)

# 5. Change any tab='session' to tab='sozlama' (e.g. in home tab)
html = html.replace("tab='session'", "tab='sozlama'")
html = html.replace('tab="session"', 'tab="sozlama"')

with open('web/static/user_dashboard.html', 'w', encoding='utf-8') as f:
    f.write(html)

print('Success')
