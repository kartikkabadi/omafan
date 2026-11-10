// Run the actual QML library in a clean JS realm; no GUI or hardware writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '..', 'Telemetry.js'), 'utf8').replace(/^\.pragma library\s*/, '');
const context = vm.createContext({});
vm.runInContext(source, context, {filename: 'Telemetry.js'});
function fixture(changes = {}) {
  return {
    is_apple_silicon: true, has_fan: true, fan_count: 2,
    device_model: 'Apple MacBook Pro', fan_rpm: 2500, fan_target: 2500,
    fan_min: 1200, fan_max: 6241, mode: 'unknown',
    fan_control_enabled: true, control_available: true, persistent: true,
    per_fan_control: true, saved_mode: 'auto', saved_control_mode: 'auto',
    saved_fans: [], saved_rpm: null,
    fans: [{index: 1, label: 'Fan 1', rpm: 2300, min: 1200, max: 5779, target: 2317},
           {index: 2, label: 'Fan 2', rpm: 2500, min: 1200, max: 6241, target: 2502}],
    sensors: {nand: 0, battery: 34.5, regulator: null, wifi: 49},
    max_temp: 49, power_watts: 0,
    ...changes,
  };
}
function parse(changes) { return context.parse(JSON.stringify(fixture(changes))); }

test('positive setpoint cannot create manual mode', () => {
  assert.equal(parse().mode, 'unknown');
  assert.equal(parse({mode: 'auto'}).mode, 'auto');
});
test('valid zeros and missing readings remain distinct', () => {
  const data = parse({fan_rpm: 0, fan_target: null, max_temp: 0});
  assert.equal(data.fanRpm, 0); assert.equal(data.fanTarget, null);
  assert.equal(data.sensors.nand, 0); assert.equal(data.sensors.regulator, null);
  assert.equal(data.maxTemp, 0); assert.equal(data.powerWatts, 0);
});
test('invalid numeric strings are not coerced', () => {
  const data = parse({fan_rpm: '2500', max_temp: '49', power_watts: '10'});
  assert.equal(data.fanRpm, null); assert.equal(data.maxTemp, null); assert.equal(data.powerWatts, null);
});
test('all reported fan limits must be safe for manual controls', () => {
  assert.equal(parse().controlEnabled, true);
  for (const min of [null, 0, 999, 7000]) {
    const fans = fixture().fans; fans[1].min = min;
    assert.equal(parse({fans}).controlEnabled, false);
  }
  const fans = fixture().fans; fans[1].max = 8000;
  assert.equal(parse({fans}).controlEnabled, false);
});
test('unavailable permission never enables controls', () => {
  assert.equal(parse({control_available: false}).controlEnabled, false);
  assert.equal(parse({control_available: 'true'}).controlEnabled, false);
});
test('fractional hardware limits cannot enable integer RPM controls', () => {
  for (const field of ['min', 'max']) {
    const fans = fixture().fans;
    fans[0][field] += 0.5;
    assert.equal(parse({fans}).controlEnabled, false);
  }
  for (const field of ['fan_min', 'fan_max']) {
    assert.equal(parse({[field]: fixture()[field] + 0.5}).controlEnabled, false);
  }
});
test('legacy or malformed persistence capability cannot enable lasting controls', () => {
  for (const persistent of [false, null, undefined, 'true', 1])
    assert.equal(parse({persistent}).controlEnabled, false);
  assert.equal(parse({persistent: true}).controlEnabled, true);
});
test('saved manual preferences require integer RPM and integer owner', () => {
  const valid = {saved_mode: 'manual', saved_control_mode: 'shared', saved_rpm: 4000, saved_owner_uid: 1000};
  const result = parse(valid);
  assert.equal(result.savedMode, 'manual');
  assert.equal(result.savedRpm, 4000);
  assert.equal(result.savedOwnerUid, 1000);
  assert.equal(parse({...valid, saved_rpm: 4000.5}).savedRpm, null);
  assert.equal(parse({...valid, saved_owner_uid: 1000.5}).savedOwnerUid, null);
});
test('legacy capability cannot enable independent fan writes', () => {
  for (const per_fan_control of [false, undefined, null, 'true', 1])
    assert.equal(parse({per_fan_control}).controlEnabled, false);
});
test('unequal saved fan targets map by channel index rather than array position', () => {
  const data = parse({saved_mode: 'manual', saved_control_mode: 'individual',
    saved_fans: [{index: 2, rpm: 5000}, {index: 1, rpm: 1600}]});
  assert.equal(data.savedFansValid, true);
  assert.equal(data.preferenceValid, true);
  assert.equal(data.controlEnabled, true);
  assert.equal(data.savedFans.find(fan => fan.index === 1).rpm, 1600);
  assert.equal(data.savedFans.find(fan => fan.index === 2).rpm, 5000);
  assert.equal(data.savedRpm, null);
});
test('invalid per-fan target maps never enable numeric controls', () => {
  const valid = [{index: 1, rpm: 1600}, {index: 2, rpm: 5000}];
  const malformed = [null, {}, 'targets', [valid[0], valid[0]],
    [{index: 1.5, rpm: 1600}, valid[1]], [{index: '1', rpm: 1600}, valid[1]],
    [{index: 0, rpm: 1600}, valid[1]], [{index: 65, rpm: 1600}, valid[1]],
    [{index: 1, rpm: 1600.5}, valid[1]], [{index: 1, rpm: '1600'}, valid[1]],
    [{index: 1, rpm: 999}, valid[1]], [{index: 1, rpm: 8000}, valid[1]],
    Array.from({length: 65}, (_, i) => ({index: i + 1, rpm: 1600}))];
  for (const saved_fans of malformed) {
    const data = parse({saved_mode: 'manual', saved_control_mode: 'individual', saved_fans});
    assert.equal(data.savedFansValid, false);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('missing and newly appeared physical fans invalidate individual preference matching', () => {
  for (const saved_fans of [[{index: 1, rpm: 1600}],
                           [{index: 1, rpm: 1600}, {index: 3, rpm: 5000}],
                           [{index: 1, rpm: 1600}, {index: 2, rpm: 5000}, {index: 3, rpm: 3000}]]) {
    const data = parse({saved_mode: 'manual', saved_control_mode: 'individual', saved_fans});
    assert.equal(data.savedFansValid, true);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('canonical mode combinations do not accept contradictory shared and individual data', () => {
  for (const data of [
    {saved_mode: 'auto', saved_control_mode: 'auto', saved_fans: [{index: 1, rpm: 1600}]},
    {saved_mode: 'auto', saved_control_mode: 'auto', saved_rpm: 4000},
    {saved_mode: 'manual', saved_control_mode: 'shared', saved_rpm: 4000, saved_fans: [{index: 1, rpm: 1600}]},
    {saved_mode: 'manual', saved_control_mode: 'individual', saved_rpm: 4000,
     saved_fans: [{index: 1, rpm: 1600}, {index: 2, rpm: 5000}]},
  ]) assert.equal(parse(data).preferenceValid, false);
});
test('saved requests above a current hardware maximum remain valid desired values', () => {
  const data = parse({saved_mode: 'manual', saved_control_mode: 'individual',
    saved_fans: [{index: 1, rpm: 6241}, {index: 2, rpm: 6241}]});
  assert.equal(data.preferenceValid, true);
  assert.equal(data.savedFans.find(fan => fan.index === 1).rpm, 6241);
});
test('remembered Separate map is retained independently of active Linked and Auto modes', () => {
  const saved_separate_fans = [{index: 2, rpm: 5000}, {index: 1, rpm: 1600}];
  for (const active of [{saved_mode: 'auto', saved_control_mode: 'auto', saved_rpm: null},
                       {saved_mode: 'manual', saved_control_mode: 'shared', saved_rpm: 4000}]) {
    const data = parse({...active, separate_memory: true, saved_separate_fans});
    assert.equal(data.separateMemory, true);
    assert.equal(data.separateMemoryValid, true);
    assert.equal(data.separateRestoreAvailable, true);
    assert.equal(data.preferenceValid, true);
    assert.equal(data.controlEnabled, true);
    assert.equal(data.savedSeparateFans.find(fan => fan.index === 1).rpm, 1600);
    assert.equal(data.savedSeparateFans.find(fan => fan.index === 2).rpm, 5000);
  }
});
test('inactive remembered inventory mismatch leaves Linked valid but disables restoration', () => {
  const data = parse({saved_mode: 'manual', saved_control_mode: 'shared', saved_rpm: 4000,
    separate_memory: true, saved_separate_fans: [{index: 1, rpm: 1600}, {index: 3, rpm: 5000}]});
  assert.equal(data.separateMemoryValid, true);
  assert.equal(data.separateRestoreAvailable, false);
  assert.equal(data.preferenceValid, true);
  assert.equal(data.controlEnabled, true);
  assert.equal(data.savedControlMode, 'shared');
  assert.equal(data.savedRpm, 4000);
});
test('claimed remembered profiles with malformed data fail closed', () => {
  const malformed = [undefined, null, {}, 'targets', [{index: 1, rpm: 1600}, {index: 1, rpm: 5000}],
    [{index: 1.5, rpm: 1600}], [{index: '1', rpm: 1600}], [{index: 0, rpm: 1600}],
    [{index: 65, rpm: 1600}], [{index: 1, rpm: 1600.5}], [{index: 1, rpm: '1600'}],
    [{index: 1, rpm: 999}], [{index: 1, rpm: 8000}],
    Array.from({length: 65}, (_, i) => ({index: i + 1, rpm: 1600}))];
  for (const saved_separate_fans of malformed) {
    const data = parse({separate_memory: true, saved_separate_fans});
    assert.equal(data.separateMemoryValid, false);
    assert.equal(data.separateRestoreAvailable, false);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('active Separate preferences must agree with remembered values by index', () => {
  const saved_fans = [{index: 1, rpm: 1600}, {index: 2, rpm: 5000}];
  const active = {saved_mode: 'manual', saved_control_mode: 'individual', saved_fans, separate_memory: true};
  const good = parse({...active, saved_separate_fans: [...saved_fans].reverse()});
  assert.equal(good.separateMemoryValid, true);
  assert.equal(good.preferenceValid, true);
  for (const saved_separate_fans of [[], [{index: 1, rpm: 1600}, {index: 2, rpm: 4000}]]) {
    const data = parse({...active, saved_separate_fans});
    assert.equal(data.separateMemoryValid, false);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('empty remembered history can seed a profile and unclaimed support is never fabricated', () => {
  const empty = parse({separate_memory: true, saved_separate_fans: []});
  assert.equal(empty.separateMemoryValid, true);
  assert.equal(empty.separateRestoreAvailable, true);
  for (const separate_memory of [false, undefined, 'true', 1]) {
    const data = parse({separate_memory, saved_separate_fans: 'untrusted legacy field'});
    assert.equal(data.separateMemory, false);
    assert.equal(data.savedSeparateFans.length, 0);
    assert.equal(data.preferenceValid, true);
  }
});
test('inactive Linked bank remains independent from higher Separate targets and Auto', () => {
  const saved_fans = [{index: 1, rpm: 4500}, {index: 2, rpm: 5000}];
  for (const active of [{saved_mode: 'auto', saved_control_mode: 'auto'},
                       {saved_mode: 'manual', saved_control_mode: 'individual', saved_fans}]) {
    const data = parse({...active, linked_memory: true, saved_linked_rpm: 2500});
    assert.equal(data.linkedMemory, true);
    assert.equal(data.linkedMemoryValid, true);
    assert.equal(data.savedLinkedRpm, 2500);
    assert.equal(data.preferenceValid, true);
    assert.equal(data.controlEnabled, true);
  }
});
test('active Linked scalar must match its remembered Linked bank', () => {
  const active = {saved_mode: 'manual', saved_control_mode: 'shared', saved_rpm: 4000, linked_memory: true};
  assert.equal(parse({...active, saved_linked_rpm: 4000}).preferenceValid, true);
  for (const saved_linked_rpm of [null, 2500, 5000]) {
    const data = parse({...active, saved_linked_rpm});
    assert.equal(data.linkedMemoryValid, false);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('claimed Linked bank rejects malformed or out-of-range scalar values', () => {
  for (const saved_linked_rpm of [undefined, false, true, {}, [], '2500', 2500.5, 0, 999, 8000, -1]) {
    const data = parse({linked_memory: true, saved_linked_rpm});
    assert.equal(data.linkedMemoryValid, false);
    assert.equal(data.preferenceValid, false);
    assert.equal(data.controlEnabled, false);
  }
});
test('null Linked bank explicitly represents first-use history without invalidating Auto', () => {
  const data = parse({linked_memory: true, saved_linked_rpm: null});
  assert.equal(data.linkedMemory, true);
  assert.equal(data.linkedMemoryValid, true);
  assert.equal(data.savedLinkedRpm, null);
  assert.equal(data.preferenceValid, true);
});
test('legacy telemetry cannot fabricate Linked-memory support from a stray scalar', () => {
  for (const linked_memory of [false, undefined, 'true', 1]) {
    const data = parse({linked_memory, saved_linked_rpm: 4000});
    assert.equal(data.linkedMemory, false);
    assert.equal(data.savedLinkedRpm, null);
    assert.equal(data.linkedMemoryValid, true);
    assert.equal(data.preferenceValid, true);
  }
});
test('fanless/error states cannot enable numeric controls', () => {
  assert.equal(parse({has_fan: false}).controlEnabled, false);
  assert.equal(parse({fans: [], has_fan: false}).controlEnabled, false);
  assert.equal(parse({error: 'macsmc_hwmon_missing'}).controlEnabled, false);
});
test('malformed, duplicated or truncated fan entries disable numeric controls', () => {
  const valid = fixture().fans[0];
  for (const fans of [[valid, {index: '2', min: null, max: null}],
                      [valid, {...valid}],
                      Array.from({length: 33}, (_, i) => ({...valid, index: i + 1}))]) {
    assert.equal(parse({fans}).controlEnabled, false);
  }
});
test('invalid aggregate limits disable controls', () => {
  for (const limits of [{fan_min: null}, {fan_min: 7000}, {fan_max: 8000}, {fan_max: 0}])
    assert.equal(parse(limits).controlEnabled, false);
});
test('unsupported and missing driver replies retain error', () => {
  const data = context.parse(JSON.stringify({is_apple_silicon: true, error: 'macsmc_hwmon_missing'}));
  assert.equal(data.error, 'macsmc_hwmon_missing'); assert.equal(data.controlEnabled, false);
});
test('malformed protocol objects are rejected', () => {
  for (const value of ['', 'null', '42', '[]', '{}', '{"is_apple_silicon":"true"}', '{'])
    assert.throws(() => context.parse(value));
  assert.throws(() => context.parse(' '.repeat(65537)));
});
test('primitive sensor container cannot fabricate zeros', () => {
  for (const sensors of [0, false, '', null, [], 'temperature']) {
    const data = parse({sensors});
    for (const key of ['nand', 'battery', 'regulator', 'wifi']) assert.equal(data.sensors[key], null);
  }
});
test('all numeric validators reject nonfinite and out-of-range inputs', () => {
  for (const value of [NaN, Infinity, -Infinity, '1', null, true, {}, -1, 100001])
    assert.equal(context.number(value, 0, 100000), null);
});
test('labels are bounded and control characters stripped', () => {
  assert.equal(context.boundedText('A\u0000\u0001B\u007fC', 20), 'ABC');
  assert.equal(context.boundedText('a'.repeat(1000), 64).length, 64);
  assert.equal(context.boundedText({toString: () => 'unsafe'}, 64), '');
});
test('markup is neutralized before shared tooltip sinks', () => {
  const text = context.escapeMarkup('<img src="https://example.invalid/x">&\'');
  assert.equal(text, '&lt;img src=&quot;https://example.invalid/x&quot;&gt;&amp;&#39;');
  assert.equal(text.includes('<'), false);
});
test('generation is validated without coercion', () => {
  assert.equal(parse({broker_generation: 'a'.repeat(32), lease_owned: true}).generation, 'a'.repeat(32));
  for (const value of ['', 'a'.repeat(31), 'A'.repeat(32), '<img>', null, 123])
    assert.equal(parse({broker_generation: value}).generation, '');
});
test('high-index fans remain visible and fan count is derived', () => {
  const fans = fixture().fans; fans[1].index = 999;
  const data = parse({fans, fan_count: 9999});
  assert.equal(data.fanCount, 2); assert.equal(data.fans[1].index, 999);
  assert.equal(data.controlEnabled, false);
});
