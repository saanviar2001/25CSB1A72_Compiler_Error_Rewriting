import sys, io
sys.path.insert(0, 'src')
from webapp import app

client = app.test_client()

# 1. Test index page
res = client.get('/')
assert res.status_code == 200, f'Index failed: {res.status_code}'
assert b'Error Distribution' in res.data, 'Missing analytics in HTML'
print('Test 1: Index page OK')

# 2. Test sample file endpoint
res = client.get('/api/sample?name=semicolon')
assert res.status_code == 200
data = res.get_json()
assert 'code' in data and 'filename' in data
print('Test 2: Sample endpoint OK')

# 3. Test /api/analyze with missing semicolon
semicolon_code = data['code']
file_data = {'source': (io.BytesIO(semicolon_code.encode('utf-8')), 'test_semicolon.cpp')}
res = client.post('/api/analyze', data=file_data, content_type='multipart/form-data')
assert res.status_code == 200, f'Analyze failed: {res.status_code}, {res.data}'
an_data = res.get_json()
assert 'analytics' in an_data, 'Missing analytics in analyze response'
assert 'category_percentages' in an_data['analytics']
print('Test 3: /api/analyze OK -> Analytics:', an_data['analytics'])

# 4. Test /api/fix with missing semicolon (Phase 4 Agentic Auto-Heal)
file_data = {'source': (io.BytesIO(semicolon_code.encode('utf-8')), 'test_semicolon.cpp')}
res = client.post('/api/fix', data=file_data, content_type='multipart/form-data')
assert res.status_code == 200, f'Fix failed: {res.status_code}, {res.data}'
fix_data = res.get_json()
assert 'verdict' in fix_data
assert 'attempts' in fix_data
assert len(fix_data['attempts']) > 0
print('Test 4: /api/fix OK -> Verdict:', fix_data['verdict'], 'Attempts:', len(fix_data['attempts']))
for a in fix_data['attempts']:
    num = a['number']
    strat = a['strategy']
    reas = a['reasoning']
    comp = a['compiles']
    print(f'   Attempt {num}: {strat} | reasoning: {reas} | compiles: {comp}')

print('ALL ENDPOINT TESTS PASSED!')
