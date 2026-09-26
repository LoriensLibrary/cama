"""Execute the dashboard renderers with hostile stored data, without a browser."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_stored_content_is_text_not_markup():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for dashboard rendering regression tests')
    page = Path(__file__).parents[1] / 'cama/dashboard/cama_dashboard.html'
    script = page.read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    # No network, timers, or real DOM; exercise the actual renderer functions.
    prelude = '''
const assert = require('node:assert/strict');
const document = {
  getElementById: () => ({style:{}, appendChild(){}}),
  createElement: () => ({style:{}}), querySelectorAll: () => []
};
const setInterval = () => {};
const fetch = async () => ({json: async () => ({error:'test'})});
'''
    checks = '''
const attack = '<img src=x onerror="alert(1)">';
const d = {
 stats: {}, compliance: {current:{}, history:[{date:attack,exchanges:attack}]},
 emotional_state:{mood:attack, emotions:{[attack]:0.5}},
 ring:[{type:attack,age:attack,text:attack}],
 islands:[{name:attack,members:attack,strength:0.5,color:'red; background:url(https://bad)'}],
 corrections:[attack], recent_activity:[{type:attack,id:attack,age:attack,text:attack}],
 benchmarks:{safety:{error:attack},retrieval:{timestamp:attack},
 counterweight:{counterweight_inventory:{[attack]:attack},total_counterweights:attack}}
};
for (const renderer of [rO,rI,rM,rC,rB]) {
 const html = renderer(d);
 assert.ok(!html.includes('<img'), renderer.name+' contains injected markup');
 assert.ok(!html.includes('https://bad'), renderer.name+' contains injected CSS');
}
assert.ok(rO(d).includes('&lt;img'));
assert.equal(esc('A & B "quoted"'), 'A &amp; B &quot;quoted&quot;');
assert.equal(safeColor('#aBcD12'), '#aBcD12');
'''
    result = subprocess.run([node, '-e', prelude + script + checks], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
