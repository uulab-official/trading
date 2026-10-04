// Execute the actual embedded dashboard update function without a server or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../packages/monitor/web_dashboard.py'), 'utf8');
const start = source.indexOf('function updateDashboard(');
const end = source.indexOf('</script>', start);
assert(start >= 0 && end > start);
const elements = {};
const document = { getElementById(id) { return elements[id] ||= {}; } };
const context = vm.createContext({document, Date});
vm.runInContext(source.slice(start, end), context);
for (const [mark, expected, unknown] of [[0, '$0.00', false], ['0', '$0.00', false], [null, 'Mark unknown', true], [undefined, 'Mark unknown', true], [20, '$20.00', false]]) {
  vm.runInContext('updateDashboard', context)({portfolio: {}, positions: [{symbol:'BTC/USDT', side:'long', size:1, entry_price:1000, current_price:mark, unrealized_pnl:mark == null ? 0 : mark - 1000}]});
  const cells = elements.positionsBody.innerHTML.match(/<td[^>]*>[\s\S]*?<\/td>/g);
  assert(cells, 'Position row rendered');
  assert(cells[4].includes(expected), `${String(mark)}: ${cells[4]}`);
  assert.equal(cells[4].includes('Mark unknown'), unknown);
  if(unknown) assert(cells[4].includes('entry $1000.00'));
}
console.log('Dashboard mark rendering: 5 cases passed');
