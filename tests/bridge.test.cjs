const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('remote/mcp.js', 'utf8');

function harness() {
  let now = 1000;
  const scheduled = [], requests = [], values = {};
  let created = 0;
  const label = {}, button = {};
  const context = vm.createContext({
    console, Date: { now: () => now },
    storage: {
      get: key => values[key], unload: key => { delete values[key]; },
      fetch: (...args) => { requests.push(args); },
    },
    app: {
      version: 'test', platform: 'windows',
      yield: (event, cycles) => { scheduled.push({event, cycles}); },
      createDialog: () => {
        created++;
        return {addLabel: () => label, addBreak() {}, addButton: () => button,
          close() { throw Error('must not rebuild dialog'); }};
      },
    },
  });
  vm.runInContext(source, context);
  const event = name => context.onEvent(name);
  function tick(ms = 200) {
    now += ms;
    assert.ok(scheduled.length, 'a recovery tick is scheduled');
    event(scheduled.shift().event);
  }
  function reply(req, status, data, emit = true) {
    values[req[1]] = JSON.stringify(data);
    values[req[1] + '_status'] = status;
    if (emit) event(req[1] + '_fetch');
  }
  event('init'); event('connect_click'); tick();
  function paired() {
    reply(requests[0], 200, {ok: true, protocol_version: 1, session_token: 'token-a', mode: 'safe'});
    tick();
  }
  return {context, event, tick, reply, paired, requests, scheduled, label, button, values, created: () => created};
}

test('one fetch at a time; idle waits >=500ms; UI is retained', () => {
  const h = harness(); h.paired();
  assert.equal(h.requests.length, 2);
  h.tick(); assert.equal(h.requests.length, 2);
  h.reply(h.requests[1], 200, {ok:true, idle:true});
  h.tick(300); assert.equal(h.requests.length, 2);
  h.tick(200); assert.equal(h.requests.length, 3);
  assert.equal(h.created(), 1);
  assert.match(h.label.text, /Connected - SAFE/);
  assert.equal(h.scheduled.length, 1);
});

test('HTTP 0 retains token and retries with backoff', () => {
  const h = harness(); h.paired();
  h.reply(h.requests[1], 0, null);
  assert.match(h.label.text, /Reconnecting/);
  h.tick(499); assert.equal(h.requests.length, 2);
  h.tick(1); assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].at(-1), 'token-a');
  h.reply(h.requests[2], 200, {ok:true, idle:true});
  assert.match(h.label.text, /Connected/);
});

test('lost pair reply retries same instance; ALREADY_PAIRED keeps retrying', () => {
  const h = harness();
  const body = h.requests[0][4];
  assert.equal(h.requests[0][1].includes(JSON.parse(body).bridge_id), false);
  h.reply(h.requests[0], 0, null); h.tick(500);
  assert.equal(h.requests[1][4], body);
  h.reply(h.requests[1], 409, {ok:false,error:{code:'ALREADY_PAIRED'}});
  h.tick(1000); assert.equal(h.requests.length, 3);
});

test('expired token re-pairs and uses new token', () => {
  const h = harness(); h.paired();
  h.reply(h.requests[1], 401, {ok:false,error:{code:'UNAUTHORIZED'}}); h.tick(500);
  assert.match(h.requests[2][0], /pair$/);
  h.reply(h.requests[2], 200, {ok:true,protocol_version:1,mode:'safe',session_token:'token-b'}); h.tick();
  assert.equal(h.requests[3].at(-1), 'token-b');
});

test('late callback cannot complete a newer fetch', () => {
  const h = harness(); h.paired();
  const old = h.requests[1];
  h.tick(8000); h.tick(500);
  const current = h.requests[2];
  assert.notEqual(current[1], old[1]);
  h.reply(old, 401, {ok:false});
  h.reply(current, 200, {ok:true,idle:true});
  assert.match(h.label.text, /Connected/);
});

test('watchdog recovers stored result when fetch event was lost', () => {
  const h = harness(); h.paired();
  h.reply(h.requests[1], 200, {ok:true,idle:true}, false);
  h.tick(8000);
  assert.match(h.label.text, /Connected/);
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.values, {});
});

test('lost result acknowledgment retries cached response without executing twice', () => {
  const h = harness(); h.paired();
  let reads = 0;
  Object.defineProperty(h.context.app, 'activeFrameNumber', {get() { reads++; return 0; }});
  const operation = {protocol_version:1,request_id:'id-1',operation:'get_active_frame',payload:{}};
  h.reply(h.requests[1], 200, operation); h.tick();
  const result = h.requests[2][4];
  h.reply(h.requests[2], 0, null); h.tick(500);
  assert.equal(h.requests[3][4], result);
  assert.equal(reads, 1);
  h.reply(h.requests[3], 200, {ok:true}); h.tick();
  h.reply(h.requests[4], 200, operation); h.tick();
  assert.equal(h.requests[5][4], result);
  assert.equal(reads, 1);
});

test('late result 409 resumes polling; SAFE refuses injected JavaScript', () => {
  const h = harness(); h.paired();
  h.reply(h.requests[1], 200, {protocol_version:1,request_id:'id-1',operation:'run_script',payload:{script:'throw "executed";'}});
  h.tick();
  assert.equal(JSON.parse(h.requests[2][4]).error.code, 'DEV_MODE_REQUIRED');
  h.reply(h.requests[2], 409, {ok:false,error:{code:'UNKNOWN_REQUEST_ID'}}); h.tick(500);
  assert.match(h.requests[3][0], /next$/);
});

test('disconnect during pending next never executes newly returned operation', () => {
  const h = harness(); h.paired(); h.event('disconnect_click');
  h.reply(h.requests[1], 200, {protocol_version:1,request_id:'id-1',operation:'get_active_frame'});
  h.tick(); assert.match(h.requests[2][0], /disconnect$/);
  h.reply(h.requests[2], 200, {ok:true}); h.tick();
  assert.equal(h.requests.length, 3);
  h.event('connect_click'); h.tick(); assert.match(h.requests[3][0], /pair$/);
});

test('close while pairing ignores late dialog updates and lets lease expire', () => {
  const h = harness(); h.event('mcp_dialog_close');
  h.reply(h.requests[0], 200, {ok:true,protocol_version:1,mode:'safe',session_token:'late'});
  h.tick(); assert.equal(h.requests.length, 1);
  assert.equal(h.created(), 1);
});

test('native synchronous failure respects backoff', () => {
  const h = harness(); h.paired();
  h.context.storage.fetch = () => { throw new Error('native fetch failed'); };
  h.reply(h.requests[1], 200, {ok:true,idle:true});
  h.tick(500);
  assert.match(h.label.text, /Reconnecting/);
  h.tick(100);
  assert.equal(h.requests.length, 2);
});

test('missing callbacks are bounded, avoiding unbounded native workers', () => {
  const h = harness();
  h.tick(8000); h.tick(500);
  h.tick(8000); h.tick(1000);
  h.tick(8000);
  assert.equal(h.requests.length, 3);
  assert.match(h.label.text, /Native fetch stopped responding/);
});

test('native nested GUI yields cannot execute/poll another operation during save', () => {
  const h = harness(); h.paired();
  let saved = 0;
  h.context.app.activeSprite = {filename:'test.aseprite', saveAs() {
    saved++;
    for (let i=0;i<10;i++) h.tick(500);
    assert.equal(h.requests.length, 2);
  }};
  h.reply(h.requests[1], 200, {protocol_version:1,request_id:'save',operation:'save_copy',payload:{path:'copy.aseprite'}});
  h.tick();
  assert.equal(saved, 1);
  assert.equal(JSON.parse(h.requests[2][4]).result.saved, true);
});

test('legacy exports and non-editable saves never open native dialogs', () => {
  for (const operation of ['export_gif','export_png','save_as','save_sprite']) {
    const h = harness(); h.paired();
    h.context.app.activeSprite = {filename:'test.gif', saveAs(){throw Error('unsafe native save');}, save(){throw Error('unsafe native save');}};
    h.reply(h.requests[1], 200, {protocol_version:1,request_id:'export',operation,payload:{path:'test.gif'}});
    h.tick();
    const response = JSON.parse(h.requests[2][4]);
    assert.equal(response.ok === false || response.result.unsupported === true, true);
    assert.equal(JSON.stringify(response).includes('unsafe native save'), false);
  }
});
